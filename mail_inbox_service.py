"""KOL 建联的收信侧：抓回信、匹配到发出去的那封、维护会话线和报价账本。

依赖方向是单向的——本模块 import mail_blaster_service（用它的账号、解密、抑制名单），
反过来不需要，所以拆得出去、不成环。

AI 相关的调用不在这里直连：mail_blaster_service 不能 import app（会循环），
所以 ai_call 由 worker 的 handler 注入进来，和 sentiment_insight 的既有写法一致。
"""
from __future__ import annotations

import email
import email.utils
import imaplib
import json
import logging
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header

import database as db
import mail_blaster_service as mb

logger = logging.getLogger(__name__)

FETCH_BATCH = 50          # 一次最多拉几封，别让单个账号占死 worker
MAX_BODY_CHARS = 20000    # 正文截断，防止一封巨型信撑爆库和 AI 上下文
BACKFILL_DAYS = 14        # 首次接入某账号时只回填最近这些天，不然会把历史全拉进来


# --------------------------------------------------------------------------- #
# MIME 解析
# --------------------------------------------------------------------------- #

def _decode_header(raw: str) -> str:
    """收件箱里的头常常是 =?utf-8?B?...?= 这种编码字。"""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return str(raw).strip()


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


# 引用回复的分界。匹配到第一处就把后面全切掉——
# 不切的话每一轮回信都会把之前所有内容再带一遍，AI 上下文和库都会被撑爆。
_QUOTE_MARKERS = [
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.I | re.M),
    re.compile(r"^\s*On .{5,80}\bwrote:\s*$", re.I | re.M),
    re.compile(r"^\s*在\s*.{4,60}\s*(写道|寫道)[:：]\s*$", re.M),
    re.compile(r"^\s*From:\s*.+\n\s*Sent:\s*", re.I | re.M),
    re.compile(r"^\s*发件人[:：]\s*.+\n\s*发送时间[:：]", re.M),
    re.compile(r"^\s*_{10,}\s*$", re.M),
    re.compile(r"^\s*>{1,}\s?.*(\n\s*>{1,}\s?.*){2,}", re.M),
]


def strip_quoted(text: str) -> str:
    """只留这一轮新写的内容。切不干净也没关系——宁可多留，不能把正文切没了。"""
    if not text:
        return ""
    cut = len(text)
    for pat in _QUOTE_MARKERS:
        m = pat.search(text)
        if m and m.start() < cut:
            cut = m.start()
    head = text[:cut].strip()
    # 切完只剩没几个字，多半是切错了位置，退回原文
    return head if len(head) >= 12 else text.strip()


def parse_message(raw_bytes: bytes) -> dict:
    """原始邮件 → 结构化字段。"""
    msg = email.message_from_bytes(raw_bytes)
    from_name, from_email = email.utils.parseaddr(msg.get("From") or "")
    _, to_email = email.utils.parseaddr(msg.get("To") or "")

    text_parts, html_parts = [], []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                text_parts.append(_part_text(part))
            elif ctype == "text/html":
                html_parts.append(_part_text(part))
    else:
        (html_parts if msg.get_content_type() == "text/html" else text_parts).append(
            _part_text(msg))

    body_text = "\n".join(t for t in text_parts if t).strip()
    body_html = "\n".join(h for h in html_parts if h).strip()
    if not body_text and body_html:
        body_text = mb.html_to_text(body_html)

    received_at = None
    if msg.get("Date"):
        try:
            received_at = email.utils.parsedate_to_datetime(msg["Date"])
            if received_at is not None and received_at.tzinfo is not None:
                received_at = received_at.astimezone(mb._send_tz()).replace(tzinfo=None)
        except Exception:
            received_at = None

    return {
        "message_id": (msg.get("Message-ID") or "").strip(),
        "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
        "refs": (msg.get("References") or "").strip(),
        "from_email": (from_email or "").strip().lower(),
        "from_name": _decode_header(from_name),
        "to_email": (to_email or "").strip().lower(),
        "subject": _decode_header(msg.get("Subject") or ""),
        "body_text": strip_quoted(body_text)[:MAX_BODY_CHARS],
        "body_html": body_html[:MAX_BODY_CHARS],
        "received_at": received_at,
        "auto_submitted": (msg.get("Auto-Submitted") or "").strip().lower(),
        "precedence": (msg.get("Precedence") or "").strip().lower(),
        "return_path": (msg.get("Return-Path") or "").strip(),
    }


def guess_kind(parsed: dict) -> str:
    """先按邮件头做一次粗分类。AI 分类跑在这之后，但退信和自动回复
    没必要送去问 AI——白花 token，而且模型对这类信容易过度解读。"""
    frm = parsed["from_email"]
    subj = (parsed["subject"] or "").lower()
    if parsed["auto_submitted"] and parsed["auto_submitted"] != "no":
        return "auto_reply"
    if parsed["precedence"] in ("bulk", "auto_reply", "junk"):
        return "auto_reply"
    if parsed["return_path"] in ("<>", ""):
        if any(k in frm for k in ("mailer-daemon", "postmaster", "noreply", "no-reply")):
            return "bounce"
    if any(k in frm for k in ("mailer-daemon", "postmaster")):
        return "bounce"
    if any(k in subj for k in ("undeliverable", "delivery status notification",
                               "returned mail", "failure notice", "退信", "无法送达")):
        return "bounce"
    if any(k in subj for k in ("out of office", "auto-reply", "autoreply", "automatic reply",
                               "自动回复", "自動回覆")):
        return "auto_reply"
    return "reply"


# --------------------------------------------------------------------------- #
# 匹配：这封回信是在回哪一封发出去的信
# --------------------------------------------------------------------------- #

_MSGID_RE = re.compile(r"<[^<>@\s]+@[^<>@\s]+>")


def match_to_item(parsed: dict) -> tuple[int | None, str, float]:
    """返回 (mb_items.id, 匹配方式, 置信度)。

    优先级刻意这样排：
    1. In-Reply-To / References → mb_items.message_id。我们自签的 Message-ID
       会被原样保留（实测阿里云企业邮如此），这是唯一的强信号。
    2. 发件地址 → 最近一封发给该地址的信。收件人换客户端回信、或服务商重写了
       Message-ID 时靠这条兜底，但同一个人被发过多批时可能挂错批次，所以置信度低。
    """
    ids = []
    if parsed["in_reply_to"]:
        ids += _MSGID_RE.findall(parsed["in_reply_to"]) or [parsed["in_reply_to"]]
    if parsed["refs"]:
        ids += _MSGID_RE.findall(parsed["refs"])
    seen = []
    for mid in ids:
        mid = mid.strip()
        if mid and mid not in seen:
            seen.append(mid)
    if seen:
        row = db.query_one(
            "SELECT id FROM mb_items WHERE message_id = ANY(%s) "
            "ORDER BY sent_at DESC NULLS LAST LIMIT 1", (seen,))
        if row:
            return row["id"], "in_reply_to", 1.0

    if parsed["from_email"]:
        row = db.query_one(
            "SELECT i.id FROM mb_items i JOIN mb_jobs j ON j.id = i.job_id "
            "WHERE i.recipient = %s AND j.mode = 'outreach' AND i.status = 'sent' "
            "ORDER BY i.sent_at DESC NULLS LAST LIMIT 1", (parsed["from_email"],))
        if row:
            return row["id"], "address", 0.6

    return None, "", 0.0


# --------------------------------------------------------------------------- #
# 会话线
# --------------------------------------------------------------------------- #

def _thread_for(kol_email: str, item_id: int | None) -> int:
    """一个 KOL 一条会话线，跨批次复用。"""
    kol_email = (kol_email or "").strip().lower()
    row = db.query_one("SELECT id FROM mb_threads WHERE kol_email = %s", (kol_email,))
    if row:
        if item_id:
            db.execute("UPDATE mb_threads SET item_id = COALESCE(item_id, %s) WHERE id = %s",
                       (item_id, row["id"]))
        return row["id"]

    name, account_id, job_id, first_sent, variables = "", None, None, None, None
    if item_id:
        it = db.query_one(
            "SELECT i.*, j.sender_account_id AS job_account FROM mb_items i "
            "JOIN mb_jobs j ON j.id = i.job_id WHERE i.id = %s", (item_id,))
        if it:
            account_id = it["sender_account_id"] or it["job_account"]
            job_id, first_sent = it["job_id"], it["sent_at"]
            variables = it["vars_json"]
            try:
                v = json.loads(it["vars_json"] or "{}")
                name = next((str(v[k]) for k in ("达人名字", "name", "达人", "KOL", "kol")
                             if v.get(k)), "")
            except Exception:
                name = ""
    return db.execute_and_fetch_id("""
        INSERT INTO mb_threads (kol_email, kol_name, account_id, job_id, item_id,
                                vars_json, first_sent_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (kol_email, name, account_id, job_id, item_id, variables, first_sent))


# 状态机。won / lost 是终态，AI 不能把它们改回去——成交与否只有人说了算。
_TERMINAL = ("won", "lost")
_INTENT_TO_STATUS = {
    "quoted": "negotiating",
    "asking_price": "replied",
    "interested": "replied",
    "declined": "lost",
    "unsubscribe": "lost",
}


def advance_thread(thread_id: int, intent: str, *, reply_at=None,
                   next_action: str = "") -> None:
    cur = db.query_one("SELECT status FROM mb_threads WHERE id = %s", (thread_id,))
    status = (cur or {}).get("status") or "pending"
    if status not in _TERMINAL:
        status = _INTENT_TO_STATUS.get(intent, "replied" if intent else status)
    db.execute("""
        UPDATE mb_threads SET status = %s, last_intent = %s, next_action = %s,
            last_reply_at = COALESCE(%s, last_reply_at), updated_at = NOW()
        WHERE id = %s
    """, (status, intent or None, next_action or None, reply_at, thread_id))


def add_quote(thread_id: int, *, amount, currency="USD", status="proposed",
              source_inbox_id=None, note="") -> int:
    """追加一个报价版本。永不改历史——改价是加新版本，
    这样任何时候都能回溯「当时对方报了多少、我们还了多少」。"""
    row = db.query_one("SELECT COALESCE(MAX(version), 0) AS v FROM mb_quotes WHERE thread_id = %s",
                       (thread_id,))
    version = (row["v"] if row else 0) + 1
    return db.execute_and_fetch_id("""
        INSERT INTO mb_quotes (thread_id, version, status, amount, currency,
                               source_inbox_id, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (thread_id, version, status, amount, currency, source_inbox_id, note))


def quotes_of(thread_id: int) -> list[dict]:
    return [dict(r) for r in db.query_all(
        "SELECT * FROM mb_quotes WHERE thread_id = %s ORDER BY version ASC", (thread_id,))]


def negotiation_round(thread_id: int) -> int:
    """已经还过几轮价。从表里数，不单独存字段——存了迟早和账本对不上。"""
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM mb_quotes WHERE thread_id = %s AND status = 'countered'",
        (thread_id,))
    return row["c"] if row else 0


# --------------------------------------------------------------------------- #
# IMAP 抓取
# --------------------------------------------------------------------------- #

def _cursor(account_id: int, folder: str = "INBOX") -> dict:
    row = db.query_one("SELECT * FROM mb_imap_cursors WHERE account_id = %s AND folder = %s",
                       (account_id, folder))
    if row:
        return dict(row)
    db.execute("INSERT INTO mb_imap_cursors (account_id, folder) VALUES (%s, %s) "
               "ON CONFLICT DO NOTHING", (account_id, folder))
    return {"account_id": account_id, "folder": folder, "uidvalidity": None,
            "last_uid": 0, "last_sync_at": None, "last_error": None}


def _store_message(account_id: int, folder: str, uid: int, parsed: dict) -> int | None:
    """入库并返回新行 id；已经存过则返回 None。"""
    dedupe = parsed["message_id"] or f"{account_id}:{folder}:{uid}"
    item_id, method, confidence = match_to_item(parsed)
    kind = guess_kind(parsed)
    # 只有匹配上「我们确实发过的那封」才建会话线。
    # 否则收件箱里每封垃圾邮件都会生成一条 KOL 会话线，列表很快就没法看了。
    # 匹配不上的留在收件箱里，页面上可以人工认领。
    thread_id = None
    if kind == "reply" and item_id and parsed["from_email"]:
        thread_id = _thread_for(parsed["from_email"], item_id)
    return db.execute_and_fetch_id("""
        INSERT INTO mb_inbox_messages
            (account_id, folder, uid, dedupe_key, message_id, in_reply_to, refs,
             from_email, from_name, to_email, subject, body_text, body_html,
             received_at, matched_item_id, thread_id, match_method, match_confidence, kind)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING id
    """, (account_id, folder, uid, dedupe[:512], parsed["message_id"] or None,
          parsed["in_reply_to"] or None, parsed["refs"] or None,
          parsed["from_email"], parsed["from_name"][:255], parsed["to_email"],
          parsed["subject"], parsed["body_text"], parsed["body_html"],
          parsed["received_at"], item_id, thread_id, method or None, confidence, kind))


def fetch_account(account_id: int, folder: str = "INBOX", limit: int = FETCH_BATCH) -> dict:
    """拉一个账号的新邮件。返回统计；异常写进游标的 last_error 但不往外抛，
    这样一个账号连不上不会拖垮整轮轮询。"""
    account = mb.get_account(account_id)
    if account is None:
        return {"account_id": account_id, "error": "账号不存在", "new": 0}
    label = account["email"]
    cur = _cursor(account_id, folder)
    stats = {"account_id": account_id, "email": label, "new": 0, "fetched": 0, "error": None}

    try:
        client = mb.open_imap(account)
    except Exception as exc:
        stats["error"] = mb.friendly_imap_error(exc)
        db.execute("UPDATE mb_imap_cursors SET last_error = %s, last_sync_at = NOW() "
                   "WHERE account_id = %s AND folder = %s", (stats["error"], account_id, folder))
        return stats

    try:
        typ, data = client.select(folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"打不开 {folder}：{data}")
        typ, uv = client.status(folder, "(UIDVALIDITY)")
        uidvalidity = None
        if typ == "OK" and uv:
            m = re.search(rb"UIDVALIDITY\s+(\d+)", uv[0])
            uidvalidity = int(m.group(1)) if m else None

        last_uid = int(cur.get("last_uid") or 0)
        if uidvalidity is not None and cur.get("uidvalidity") not in (None, uidvalidity):
            # 服务端重建了 UID 空间，旧游标失效，从头来
            logger.warning("mail-blaster: %s 的 UIDVALIDITY 变了，重新回填", label)
            last_uid = 0

        if last_uid:
            typ, res = client.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        else:
            # 首次接入只回填最近一段，别把几年的历史全拉进来
            since = (mb.now_local() - timedelta(days=BACKFILL_DAYS)).strftime("%d-%b-%Y")
            typ, res = client.uid("SEARCH", None, f'(SINCE "{since}")')
        uids = [int(u) for u in (res[0].split() if typ == "OK" and res and res[0] else [])]
        uids = [u for u in uids if u > last_uid][:limit]

        for uid in uids:
            typ, payload = client.uid("FETCH", str(uid), "(RFC822)")
            if typ != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            stats["fetched"] += 1
            parsed = parse_message(payload[0][1])
            if _store_message(account_id, folder, uid, parsed) is not None:
                stats["new"] += 1
            last_uid = max(last_uid, uid)

        db.execute("""
            INSERT INTO mb_imap_cursors (account_id, folder, uidvalidity, last_uid,
                                         last_sync_at, last_error)
            VALUES (%s, %s, %s, %s, NOW(), NULL)
            ON CONFLICT (account_id, folder) DO UPDATE SET
                uidvalidity = EXCLUDED.uidvalidity, last_uid = EXCLUDED.last_uid,
                last_sync_at = NOW(), last_error = NULL
        """, (account_id, folder, uidvalidity, last_uid))
    except Exception as exc:
        stats["error"] = mb.friendly_imap_error(exc)
        logger.exception("mail-blaster: 拉 %s 的收件箱失败", label)
        db.execute("UPDATE mb_imap_cursors SET last_error = %s, last_sync_at = NOW() "
                   "WHERE account_id = %s AND folder = %s", (stats["error"], account_id, folder))
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return stats


def receivable_accounts() -> list[dict]:
    """能收信的建联账号。素材那批 OAuth2 号收不了信，会被 can_receive 挡掉。"""
    return [a for a in mb.list_accounts(purpose="outreach") if a["can_receive"] and a["enabled"]]


def fetch_all(progress=None) -> dict:
    accounts = receivable_accounts()
    out = {"accounts": len(accounts), "new": 0, "errors": []}
    for i, a in enumerate(accounts, 1):
        s = fetch_account(a["id"])
        out["new"] += s["new"]
        if s["error"]:
            out["errors"].append(f"{s.get('email') or a['email']}：{s['error']}")
        if progress:
            progress(f"收信 {i}/{len(accounts)}　新增 {out['new']} 封")
    return out


# --------------------------------------------------------------------------- #
# AI：意图分类 → 报价抽取 → 还价建议
# --------------------------------------------------------------------------- #
# 顺序不能反。先分类，**只有分类为 quoted 才跑报价抽取**——
# 对任何回信都抽价格的话，一封「谢谢，不感兴趣」会掉进「取文中任意数字」的
# 兜底分支，凭空造出一个假报价，然后污染整条议价链路。

AI_MODEL = "qwen-plus"

INTENTS = ("interested", "asking_price", "quoted", "declined",
           "unsubscribe", "auto_reply", "bounce", "unclear")

_CLASSIFY_PROMPT = """# Role
你是红人营销的回信分类助手。

# Task
读下面这封达人回信，判断它属于哪一类，并抽取要点。只返回 JSON。

字段：
1. `intent`：必须是这几个之一
   - `interested` 有合作意向但没提价格
   - `asking_price` 反过来问我们的预算 / 想要什么
   - `quoted` **明确给出了报价数字**
   - `declined` 明确拒绝
   - `unsubscribe` 要求不要再联系（退订）
   - `auto_reply` 自动回复 / 不在办公室
   - `bounce` 退信 / 投递失败通知
   - `unclear` 看不出来
2. `summary`：一句话中文概括这封信说了什么
3. `needs_human`：布尔。语气含糊、涉及合同法务、或提到我们没法自动处理的条件时为 true
4. `needs_human_reason`：上面为 true 时说明原因，否则空串
5. `contacts`：信里出现的其它联系方式，形如 [{"kind":"whatsapp","value":"+62..."}]，没有就空数组

# Rules
- **只依据信里真实写了的内容**。绝不推测、补全、"纠正"。
- 拿不准就用 `unclear` 并把 needs_human 设为 true。空值永远好过看起来合理的错值。
- 只提到"我的价格表在附件里"而没有具体数字，**不算** quoted。

# Output Format
只返回一个 JSON 对象，不要 markdown 围栏，不要任何解释：
{"intent":"","summary":"","needs_human":false,"needs_human_reason":"","contacts":[]}

# 邮件
主题：%(subject)s
发件人：%(sender)s
正文：
%(body)s
"""

_QUOTE_PROMPT = """# Role
你是红人营销报价抽取助手。

# Task
从下面这封达人回信里抽取报价。只返回 JSON。

字段：
1. `amount`：**单条整片/视频**的报价数字（纯数字，不带货币符号和千分位）。抽不到填 null
2. `currency`：USD / EUR / GBP / CNY / JPY / IDR 等，判断不出默认 USD
3. `note`：中文说明这个价格是怎么来的，比如打包价摊算、含不含口播

# Rules
- **注意区分整片价与附加项价，不要混淆**。口播 / 专属促销码 / ads code 是附加费用，不是整片价。
- 打包报价（如"2条视频+口播共 1800"）要摊算成单条价，并在 note 里写明是摊算的、需人工确认。
- **抽不到明确数字就填 null**，绝不从文中随便取一个数字当价格。

# Output Format
只返回一个 JSON 对象，不要 markdown 围栏：
{"amount":null,"currency":"USD","note":""}

# 邮件正文
%(body)s
"""

_DRAFT_PROMPT = """# Role
你是海外红人商务，正在和达人议价。

# Task
基于整条会话，写一封**回复达人的英文邮件正文**（不要主题、不要落款）。只返回 JSON。

我方情况：
- 目标价 %(target)s %(currency)s，可接受上限 %(ceiling)s %(currency)s
- 对方最新报价 %(ask)s %(currency)s
- 已经还过 %(round)s 轮
- 建议动作：%(action)s（accept=接受对方价 / counter=还价到 %(offer)s / walk=谈不拢，转人工）

# Rules
- 礼貌、简短、给出理由，不要硬砍。
- 还价时可以提**非现金补偿**的方向：后续合作机会、内容在自有渠道的曝光、长期合作意向。
  这类让步比单纯压价有效得多。
- **但只能泛泛地提，绝对不许写成具体承诺。** 禁止出现：具体条数（"接下来 2 个档期"）、
  具体平台或账号（"发在我们的 Instagram"）、"guarantee/保证"、独家、保量、预付、
  任何带数字的兑现条件。这些运营不一定兑现得了，写进信里就是对达人的承诺。
  正确写法是 "priority consideration for future campaigns"，
  错误写法是 "we'll guarantee you our next 2 campaign slots"。
- 除了还价金额，**信里不许出现任何其它数字**。
- action 是 walk 时，写一封体面的收尾信，不要再给数字。

# Output Format
只返回 JSON，不要 markdown 围栏：
{"reply":""}

# 会话记录（从早到晚）
%(history)s
"""


class AIUnavailable(RuntimeError):
    """AI 没配好或调用失败。调用方据此把这条标成 failed 而不是写入垃圾数据。"""


def _ask(ai_call, prompt: str) -> tuple[dict, int]:
    """跑一次 LLM 并解析 JSON。

    ⚠️ call_gemini 从不抛异常——失败时把错误串放在**返回值的文本位**、tokens=0。
    不检查前缀的话，会把「⚠️ 通义千问 API 调用失败」当成模型输出存进库。
    """
    if ai_call is None:
        raise AIUnavailable("没有注入 ai_call，无法做 AI 分析")
    text, tokens = ai_call(prompt)
    text = (text or "").strip()
    if not text or text.startswith(("⚠️", "❌")):
        raise AIUnavailable(text or "AI 没有返回内容")
    # 解析不出来就抛，不要默认返回 {}——
    # 静默的空解析和「这封信确实没提价格」在下游长得一模一样。
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AIUnavailable(f"模型没有返回 JSON：{text[:200]}")
    try:
        return json.loads(text[start:end + 1]), int(tokens or 0)
    except json.JSONDecodeError as exc:
        raise AIUnavailable(f"模型返回的 JSON 解析失败：{text[:200]}") from exc


def _record_cost(inbox_id: int, stage: str, tokens: int, user_id=None) -> None:
    """接进用量统计。没接进 usage_events 的任务因为没法和账单对账，
    之前被停用过一次，这里从第一天就接上。"""
    if not tokens:
        return
    try:
        import usage_service
        usage_service.record_usage_event(
            module="mail_blaster_reply_ai", user_id=user_id,
            ai_tokens=int(tokens), source="actual",
            detail=f"inbox:{inbox_id}:{stage}",
            event_key=f"mb_reply_ai:{inbox_id}:{stage}")
    except Exception:
        logger.exception("mail-blaster: 记 AI 用量失败（不影响主流程）")


def analyze_message(inbox_id: int, ai_call=None, user_id=None) -> dict:
    """对一封回信跑完整的 AI 流程，结果落 mb_inbox_extractions 并推进会话线。"""
    row = db.query_one("SELECT * FROM mb_inbox_messages WHERE id = %s", (inbox_id,))
    if row is None:
        raise ValueError(f"收件 {inbox_id} 不存在")
    msg = dict(row)

    # 退信和自动回复在头里就认出来了，没必要花 token 问 AI，
    # 而且模型对这类信容易过度解读，反而制造噪音。
    if msg["kind"] in ("bounce", "auto_reply"):
        intent = "bounce" if msg["kind"] == "bounce" else "auto_reply"
        _save_extraction(inbox_id, {"intent": intent, "summary": "按邮件头识别，未调用 AI",
                                    "needs_human": False, "needs_human_reason": "",
                                    "contacts": []}, model="header", tokens=0)
        if msg["thread_id"]:
            advance_thread(msg["thread_id"], intent)
        return {"intent": intent, "skipped_ai": True}

    body = (msg["body_text"] or mb.html_to_text(msg["body_html"] or ""))[:6000]
    try:
        data, tokens = _ask(ai_call, _CLASSIFY_PROMPT % {
            "subject": msg["subject"], "sender": msg["from_email"], "body": body})
    except AIUnavailable as exc:
        db.execute("""
            INSERT INTO mb_inbox_extractions (inbox_id, status, error, updated_at)
            VALUES (%s, 'failed', %s, NOW())
            ON CONFLICT (inbox_id) DO UPDATE SET status='failed', error=EXCLUDED.error,
                updated_at=NOW()
        """, (inbox_id, str(exc)[:500]))
        raise
    _record_cost(inbox_id, "classify", tokens, user_id)

    intent = (data.get("intent") or "").strip()
    if intent not in INTENTS:
        intent = "unclear"
        data["needs_human"] = True
        data["needs_human_reason"] = (data.get("needs_human_reason")
                                      or "模型给了预期外的 intent，转人工")
    data["intent"] = intent

    total_tokens = tokens
    # 只有明确报价才抽数字
    if intent == "quoted":
        try:
            q, qt = _ask(ai_call, _QUOTE_PROMPT % {"body": body})
            total_tokens += qt
            _record_cost(inbox_id, "quote", qt, user_id)
            amount = q.get("amount")
            data["quote_amount"] = float(amount) if amount not in (None, "", "null") else None
            data["quote_currency"] = (q.get("currency") or "USD").strip().upper()[:8]
            data["quote_note"] = (q.get("note") or "").strip()
            if data["quote_amount"] is None:
                # 说了在报价但抽不出数字 —— 交给人看
                data["needs_human"] = True
                data["needs_human_reason"] = (data.get("needs_human_reason")
                                              or "判定为报价但没抽到明确数字")
        except AIUnavailable as exc:
            logger.warning("mail-blaster: 报价抽取失败（分类结果保留）：%s", exc)
            data["needs_human"] = True
            data["needs_human_reason"] = f"报价抽取失败：{exc}"

    _save_extraction(inbox_id, data, model=AI_MODEL, tokens=total_tokens)

    # 退订自动进抑制名单。这才真正兑现了退订出口的承诺——
    # 之前的版本让人回复 unsubscribe，然后没有任何东西读这封回信。
    if intent == "unsubscribe" and msg["from_email"]:
        try:
            mb.suppress(msg["from_email"], "回信要求退订", source="reply")
        except Exception:
            logger.exception("mail-blaster: 自动加入抑制名单失败")

    if msg["thread_id"]:
        advance_thread(msg["thread_id"], intent, reply_at=msg["received_at"])
        if data.get("quote_amount") is not None:
            add_quote(msg["thread_id"], amount=data["quote_amount"],
                      currency=data.get("quote_currency") or "USD",
                      status="proposed", source_inbox_id=inbox_id,
                      note=data.get("quote_note") or "")
    return data


def _save_extraction(inbox_id: int, data: dict, *, model: str, tokens: int) -> None:
    db.execute("""
        INSERT INTO mb_inbox_extractions
            (inbox_id, intent, quote_amount, quote_currency, contacts_json,
             needs_human, needs_human_reason, summary, raw_json, model, status,
             error, tokens, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'done',NULL,%s,NOW())
        ON CONFLICT (inbox_id) DO UPDATE SET
            intent=EXCLUDED.intent, quote_amount=EXCLUDED.quote_amount,
            quote_currency=EXCLUDED.quote_currency, contacts_json=EXCLUDED.contacts_json,
            needs_human=EXCLUDED.needs_human, needs_human_reason=EXCLUDED.needs_human_reason,
            summary=EXCLUDED.summary, raw_json=EXCLUDED.raw_json, model=EXCLUDED.model,
            status='done', error=NULL, tokens=EXCLUDED.tokens, updated_at=NOW()
    """, (inbox_id, data.get("intent"), data.get("quote_amount"),
          data.get("quote_currency"),
          json.dumps(data.get("contacts") or [], ensure_ascii=False),
          bool(data.get("needs_human")), (data.get("needs_human_reason") or "")[:500],
          (data.get("summary") or "")[:1000],
          json.dumps(data, ensure_ascii=False), model, int(tokens or 0)))


def analyze_pending(ai_call=None, limit: int = 30, progress=None, user_id=None) -> dict:
    """把还没分析过的回信跑一遍。单封失败不影响其它封。

    刻意按时间**正序**处理：同一条会话线在一轮里收到多封时，
    状态机要按时间推进、最后停在最新那封上。倒序处理会让旧信覆盖新信的状态。
    """
    rows = db.query_all("""
        SELECT m.id FROM mb_inbox_messages m
        LEFT JOIN mb_inbox_extractions e ON e.inbox_id = m.id
        WHERE e.id IS NULL OR e.status = 'pending'
        ORDER BY m.received_at ASC NULLS LAST, m.id ASC LIMIT %s
    """, (limit,))
    out = {"total": len(rows), "done": 0, "failed": 0}
    for i, r in enumerate(rows, 1):
        try:
            analyze_message(r["id"], ai_call=ai_call, user_id=user_id)
            out["done"] += 1
        except Exception as exc:
            out["failed"] += 1
            logger.warning("mail-blaster: 分析第 %s 封失败：%s", r["id"], exc)
        if progress:
            progress(f"分析 {i}/{len(rows)}　成功 {out['done']}　失败 {out['failed']}")
    return out


# --------------------------------------------------------------------------- #
# 议价：策略给数字，AI 给话术，人来按发送键
# --------------------------------------------------------------------------- #
# 策略是纯算术，刻意不交给 AI —— 还价数字必须是可解释、可复现的。
# AI 只负责把这个数字写成一封得体的信。

DEFAULT_CONCESSION = 0.5
DEFAULT_MAX_ROUNDS = 3


def decide(ask: float, *, target: float, ceiling: float, round_no: int,
           last_offer: float | None = None,
           max_rounds: int = DEFAULT_MAX_ROUNDS,
           concession: float = DEFAULT_CONCESSION) -> dict:
    """returns {action: accept|counter|walk, offer, rationale}"""
    if ask is None:
        return {"action": "walk", "offer": None, "rationale": "没有对方报价，没法决策"}
    if ask <= ceiling:
        return {"action": "accept", "offer": ask,
                "rationale": f"对方报 {ask} 已在可接受上限 {ceiling} 之内"}
    if round_no >= max_rounds:
        # 谈不拢就交给人，绝不自动继续压价
        return {"action": "walk", "offer": None,
                "rationale": f"已还价 {round_no} 轮仍高于上限 {ceiling}，转人工决定"}
    base = last_offer if last_offer is not None else target
    offer = min(base + (ask - base) * concession, ceiling)
    return {"action": "counter", "offer": round(offer, 2),
            "rationale": f"对方 {ask}，我方基准 {base}，各让一半后还价 {round(offer, 2)}"
                         f"（不超过上限 {ceiling}）"}


def thread_history(thread_id: int, limit: int = 12) -> str:
    rows = db.query_all("""
        SELECT m.from_email, m.subject, m.body_text, m.received_at, e.summary
        FROM mb_inbox_messages m LEFT JOIN mb_inbox_extractions e ON e.inbox_id = m.id
        WHERE m.thread_id = %s ORDER BY m.received_at ASC NULLS LAST LIMIT %s
    """, (thread_id, limit))
    out = []
    for r in rows:
        when = r["received_at"].strftime("%Y-%m-%d %H:%M") if r["received_at"] else ""
        out.append(f"[{when}] {r['from_email']}: {(r['body_text'] or '')[:600]}")
    return "\n\n".join(out) or "（还没有往来记录）"


def suggest_reply(thread_id: int, *, target: float, ceiling: float,
                  currency: str = "USD", ai_call=None, user_id=None) -> dict:
    """给这条会话线算出建议动作 + 生成回复草稿。

    **草稿一律不自动发送**，写进 mb_threads.next_action 等人确认。
    冷启动外联场景下 AI 误判会直接发给真实达人，且撤不回来。
    """
    thread = db.query_one("SELECT * FROM mb_threads WHERE id = %s", (thread_id,))
    if thread is None:
        raise ValueError(f"会话线 {thread_id} 不存在")
    qs = quotes_of(thread_id)
    asks = [q for q in qs if q["status"] == "proposed" and q["amount"] is not None]
    ours = [q for q in qs if q["status"] == "countered" and q["amount"] is not None]
    ask = float(asks[-1]["amount"]) if asks else None
    last_offer = float(ours[-1]["amount"]) if ours else None

    plan = decide(ask, target=target, ceiling=ceiling,
                  round_no=negotiation_round(thread_id), last_offer=last_offer)

    draft = ""
    try:
        data, tokens = _ask(ai_call, _DRAFT_PROMPT % {
            "target": target, "ceiling": ceiling, "currency": currency,
            "ask": ask if ask is not None else "（未报价）",
            "round": negotiation_round(thread_id),
            "action": plan["action"], "offer": plan["offer"] if plan["offer"] else "-",
            "history": thread_history(thread_id)})
        draft = (data.get("reply") or "").strip()
        if tokens:
            _record_cost(thread_id, "draft", tokens, user_id)
    except AIUnavailable as exc:
        draft = ""
        plan["rationale"] += f"（话术生成失败：{exc}）"

    next_action = {
        "accept": f"建议接受 {ask} {currency}",
        "counter": f"建议还价到 {plan['offer']} {currency}",
        "walk": "建议转人工：" + plan["rationale"],
    }.get(plan["action"], plan["rationale"])
    db.execute("UPDATE mb_threads SET next_action = %s, updated_at = NOW() WHERE id = %s",
               (next_action, thread_id))
    # 只有还价数字真的变了才追加版本。
    # 不判重的话，反复点「算建议」会写进一串相同的 countered 行，
    # 而 negotiation_round 数的正是这些行——点几下就把轮次刷满、提前触发 walk。
    if plan["action"] == "counter" and plan["offer"] is not None:
        same = ours and float(ours[-1]["amount"]) == float(plan["offer"])
        if not same:
            add_quote(thread_id, amount=plan["offer"], currency=currency,
                      status="countered", note=plan["rationale"])
    return {**plan, "draft": draft, "next_action": next_action,
            "ask": ask, "currency": currency}


# --------------------------------------------------------------------------- #
# 给页面用的读取接口
# --------------------------------------------------------------------------- #

THREAD_STATUS_TEXT = {
    "pending": "待回复", "replied": "已回复", "negotiating": "议价中",
    "won": "已成交", "lost": "已放弃",
}
INTENT_TEXT = {
    "interested": "有意向", "asking_price": "问预算", "quoted": "已报价",
    "declined": "拒绝", "unsubscribe": "要求退订", "auto_reply": "自动回复",
    "bounce": "退信", "unclear": "看不明白",
}


def _serialize_thread(row: dict) -> dict:
    out = dict(row)
    for k in ("first_sent_at", "last_reply_at", "updated_at"):
        if out.get(k):
            out[k] = out[k].strftime("%Y-%m-%d %H:%M")
    out["status_text"] = THREAD_STATUS_TEXT.get(out.get("status"), out.get("status"))
    out["intent_text"] = INTENT_TEXT.get(out.get("last_intent"), out.get("last_intent") or "")
    if out.get("latest_amount") is not None:
        out["latest_amount"] = float(out["latest_amount"])
    out.pop("vars_json", None)
    return out


def list_threads(status: str = "", keyword: str = "", limit: int = 200) -> list[dict]:
    sql = """
        SELECT t.*,
               (SELECT q.amount FROM mb_quotes q WHERE q.thread_id = t.id
                 AND q.status = 'proposed' ORDER BY q.version DESC LIMIT 1) AS latest_amount,
               (SELECT q.currency FROM mb_quotes q WHERE q.thread_id = t.id
                 ORDER BY q.version DESC LIMIT 1) AS latest_currency,
               (SELECT COUNT(*) FROM mb_inbox_messages m WHERE m.thread_id = t.id) AS msg_count,
               (SELECT COUNT(*) FROM mb_inbox_messages m
                 WHERE m.thread_id = t.id AND m.handled = FALSE) AS unhandled
        FROM mb_threads t WHERE 1=1
    """
    args: list = []
    if status in THREAD_STATUS_TEXT:
        sql += " AND t.status = %s"
        args.append(status)
    if keyword:
        sql += " AND (t.kol_email ILIKE %s OR t.kol_name ILIKE %s)"
        args += [f"%{keyword}%"] * 2
    sql += " ORDER BY t.updated_at DESC LIMIT %s"
    args.append(int(limit))
    return [_serialize_thread(dict(r)) for r in db.query_all(sql, tuple(args))]


def thread_detail(thread_id: int) -> dict:
    row = db.query_one("SELECT * FROM mb_threads WHERE id = %s", (thread_id,))
    if row is None:
        raise ValueError(f"会话线 {thread_id} 不存在")
    msgs = db.query_all("""
        SELECT m.*, e.intent, e.summary, e.needs_human, e.needs_human_reason,
               e.quote_amount, e.quote_currency, e.status AS ai_status, e.error AS ai_error
        FROM mb_inbox_messages m LEFT JOIN mb_inbox_extractions e ON e.inbox_id = m.id
        WHERE m.thread_id = %s ORDER BY m.received_at ASC NULLS LAST, m.id ASC
    """, (thread_id,))
    out_msgs = []
    for m in msgs:
        d = dict(m)
        d["received_at"] = (d["received_at"].strftime("%Y-%m-%d %H:%M")
                            if d.get("received_at") else "")
        d["intent_text"] = INTENT_TEXT.get(d.get("intent"), d.get("intent") or "")
        if d.get("quote_amount") is not None:
            d["quote_amount"] = float(d["quote_amount"])
        d.pop("body_html", None)      # 页面只展示纯文本，省流量也避免注入
        out_msgs.append(d)
    qs = []
    for q in quotes_of(thread_id):
        q = dict(q)
        q["amount"] = float(q["amount"]) if q["amount"] is not None else None
        q["created_at"] = q["created_at"].strftime("%Y-%m-%d %H:%M") if q["created_at"] else ""
        qs.append(q)
    return {"thread": _serialize_thread(dict(row)), "messages": out_msgs,
            "quotes": qs, "round": negotiation_round(thread_id)}


def set_thread_status(thread_id: int, status: str) -> None:
    if status not in THREAD_STATUS_TEXT:
        raise ValueError(f"未知状态：{status}")
    db.execute("UPDATE mb_threads SET status = %s, updated_at = NOW() WHERE id = %s",
               (status, thread_id))


def mark_handled(inbox_id: int, handled: bool = True) -> None:
    db.execute("UPDATE mb_inbox_messages SET handled = %s WHERE id = %s", (handled, inbox_id))


def inbox_stats() -> dict:
    row = db.query_one("""
        SELECT
          (SELECT COUNT(*) FROM mb_threads WHERE status = 'pending')     AS pending,
          (SELECT COUNT(*) FROM mb_threads WHERE status = 'replied')     AS replied,
          (SELECT COUNT(*) FROM mb_threads WHERE status = 'negotiating') AS negotiating,
          (SELECT COUNT(*) FROM mb_threads WHERE status = 'won')         AS won,
          (SELECT COUNT(*) FROM mb_threads WHERE status = 'lost')        AS lost,
          (SELECT COUNT(*) FROM mb_inbox_messages WHERE handled = FALSE
             AND kind = 'reply')                                         AS unhandled,
          (SELECT COUNT(*) FROM mb_inbox_extractions WHERE needs_human)  AS needs_human
    """)
    return dict(row) if row else {}


def unmatched_messages(limit: int = 50) -> list[dict]:
    """匹配不上任何一封发出去的信的来件，页面上可以人工认领。"""
    rows = db.query_all("""
        SELECT id, from_email, from_name, subject, received_at, kind
        FROM mb_inbox_messages
        WHERE thread_id IS NULL AND kind = 'reply'
        ORDER BY received_at DESC NULLS LAST LIMIT %s
    """, (limit,))
    out = []
    for r in rows:
        d = dict(r)
        d["received_at"] = (d["received_at"].strftime("%Y-%m-%d %H:%M")
                            if d.get("received_at") else "")
        out.append(d)
    return out


def claim_message(inbox_id: int, kol_email: str = "") -> int:
    """人工把一封没匹配上的来件挂到某个 KOL 的会话线上。"""
    msg = db.query_one("SELECT * FROM mb_inbox_messages WHERE id = %s", (inbox_id,))
    if msg is None:
        raise ValueError(f"收件 {inbox_id} 不存在")
    email_addr = (kol_email or msg["from_email"] or "").strip().lower()
    if "@" not in email_addr:
        raise ValueError("没有可用的 KOL 邮箱")
    tid = _thread_for(email_addr, msg["matched_item_id"])
    db.execute("UPDATE mb_inbox_messages SET thread_id = %s, match_method = 'manual', "
               "match_confidence = 1.0 WHERE id = %s", (tid, inbox_id))
    return tid
