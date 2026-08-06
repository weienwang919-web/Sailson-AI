"""mail-blaster：素材提交子功能。

从独立的本地工具（~/mail-blaster，SQLite + 本地文件）移植过来，适配本项目：
  - SQLite  → Postgres（database.py 的连接池）
  - 本地文件 → BYTEA（Render 无持久盘，web/worker 也不共享文件系统）
  - 自建认证 → app.py 现有的 feature_required('mail_blaster')
  - 后台线程 → task_queue + worker.py（web 是 free 套餐，空闲 15 分钟休眠会把线程一起杀掉）

语义：一个收件人 ← N 个发件账号，各带 1 张图，发出 N 封信。
客户的归档流程要求「一素材一邮件」，所以是一条素材一封信，而不是一封信带 N 张图。

表全部用 mb_ 前缀，避免和主库那 30 多张表撞名。
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
import smtplib
import urllib.error
import urllib.request
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from html import escape, unescape

from openpyxl import load_workbook
from PIL import Image
import psycopg2
from psycopg2.extras import execute_values

import crypto_util
import database as db

logger = logging.getLogger(__name__)

COOLDOWN_DAYS = 7
DEFAULT_DAILY_LIMIT = 10
SMTP_TIMEOUT = 25

MAX_IMAGE_BYTES = 1_500_000
MAX_IMAGE_EDGE = 1600
JPEG_QUALITY = 85


# --------------------------------------------------------------------------- #
# 建表
# --------------------------------------------------------------------------- #

def ensure_schema() -> None:
    """幂等建表。app.py 模块级调用一次即可——worker.py 会 `from app import ...`，
    所以这段在 web 和 worker 两个进程里都会执行到。"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_sender_accounts (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            display_name VARCHAR(255),
            signature_name VARCHAR(255),
            provider VARCHAR(64) NOT NULL DEFAULT 'custom',
            smtp_host VARCHAR(255) NOT NULL,
            smtp_port INTEGER NOT NULL,
            smtp_username VARCHAR(255),
            encrypted_password TEXT,
            use_ssl BOOLEAN NOT NULL DEFAULT FALSE,
            use_tls BOOLEAN NOT NULL DEFAULT TRUE,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            daily_limit INTEGER NOT NULL DEFAULT 10,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            last_test_at TIMESTAMP,
            last_error TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # 图片存库不存盘：Render 无持久磁盘，且 web / worker 是两个独立容器，
    # web 写到本地的图 worker 根本读不到。按 sha256 去重，同一张图不重复占空间。
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_images (
            id SERIAL PRIMARY KEY,
            sha256 CHAR(64) UNIQUE NOT NULL,
            content BYTEA NOT NULL,
            mime VARCHAR(64) NOT NULL DEFAULT 'image/png',
            byte_size INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_jobs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            recipient VARCHAR(255) NOT NULL DEFAULT '',
            subject_tpl TEXT NOT NULL DEFAULT '',
            body_tpl TEXT NOT NULL DEFAULT '',
            signature_tpl TEXT NOT NULL DEFAULT '',
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            paused_reason TEXT,
            task_id VARCHAR(100),
            created_at TIMESTAMP DEFAULT NOW(),
            finished_at TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_items (
            id SERIAL PRIMARY KEY,
            job_id INTEGER NOT NULL REFERENCES mb_jobs(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL DEFAULT 0,
            sender_account_id INTEGER REFERENCES mb_sender_accounts(id) ON DELETE SET NULL,
            recipient VARCHAR(255),
            image_id INTEGER REFERENCES mb_images(id) ON DELETE SET NULL,
            image_name VARCHAR(255),
            vars_json TEXT,
            from_display VARCHAR(255),
            signature_name VARCHAR(255),
            subject TEXT,
            body_html TEXT,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            error TEXT,
            smtp_response TEXT,
            message_id TEXT,
            sent_at TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_templates (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) UNIQUE NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            body_html TEXT NOT NULL DEFAULT '',
            signature_html TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # 跨批次留存：既是发信账，也是「这条素材发过没」和「这个账号还在冷却期没」的依据
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_history (
            id SERIAL PRIMARY KEY,
            recipient VARCHAR(255) NOT NULL,
            material_id VARCHAR(255) NOT NULL DEFAULT '',
            material_name VARCHAR(255) NOT NULL DEFAULT '',
            sender_account_id INTEGER,
            sender_email VARCHAR(255) NOT NULL DEFAULT '',
            job_id INTEGER,
            item_id INTEGER,
            subject TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            sent_at TIMESTAMP DEFAULT NOW()
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_entity_phones (
            entity_key VARCHAR(255) PRIMARY KEY,
            company_name VARCHAR(512) NOT NULL DEFAULT '',
            country_code VARCHAR(4) NOT NULL DEFAULT '',
            phone VARCHAR(64) NOT NULL DEFAULT '',
            source VARCHAR(16) NOT NULL DEFAULT 'pool',
            assigned_at TIMESTAMP DEFAULT NOW()
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_phone_pool (
            id SERIAL PRIMARY KEY,
            country_code VARCHAR(4) NOT NULL,
            phone VARCHAR(64) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (country_code, phone)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS mb_license_ocr (
            id SERIAL PRIMARY KEY,
            image_sha256 CHAR(64) UNIQUE NOT NULL,
            company_name VARCHAR(512) NOT NULL DEFAULT '',
            registration_id VARCHAR(255) NOT NULL DEFAULT '',
            registered_address TEXT NOT NULL DEFAULT '',
            country_code VARCHAR(4) NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '',
            model VARCHAR(64) NOT NULL DEFAULT '',
            status VARCHAR(16) NOT NULL DEFAULT 'done',
            error TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    # 老库补列：表建好之后新增的字段走这里。Postgres 支持 ADD COLUMN IF NOT EXISTS，
    # 反复执行是幂等的。
    for stmt in (
        "ALTER TABLE mb_jobs ADD COLUMN IF NOT EXISTS ocr_status VARCHAR(20) NOT NULL DEFAULT 'none'",
        "ALTER TABLE mb_jobs ADD COLUMN IF NOT EXISTS ocr_report TEXT",
        "ALTER TABLE mb_jobs ADD COLUMN IF NOT EXISTS ocr_task_id VARCHAR(100)",
        # OAuth2（XOAUTH2）：微软已对部分账号禁用密码直连 SMTP，只能走 OAuth
        "ALTER TABLE mb_sender_accounts ADD COLUMN IF NOT EXISTS auth_mode VARCHAR(16) NOT NULL DEFAULT 'password'",
        "ALTER TABLE mb_sender_accounts ADD COLUMN IF NOT EXISTS encrypted_client_id TEXT",
        "ALTER TABLE mb_sender_accounts ADD COLUMN IF NOT EXISTS encrypted_refresh_token TEXT",
    ):
        db.execute(stmt)

    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_mb_items_job ON mb_items(job_id, seq)",
        "CREATE INDEX IF NOT EXISTS idx_mb_history_dedupe ON mb_history(recipient, material_id)",
        "CREATE INDEX IF NOT EXISTS idx_mb_history_sender ON mb_history(recipient, sender_account_id, sent_at)",
        "CREATE INDEX IF NOT EXISTS idx_mb_history_sent_at ON mb_history(sent_at DESC)",
    ):
        db.execute(stmt)


# --------------------------------------------------------------------------- #
# 发件账号池
# --------------------------------------------------------------------------- #

PROVIDERS = [
    {"key": "aliyun_qiye", "label": "阿里云企业邮箱", "smtp_host": "smtp.qiye.aliyun.com",
     "smtp_port": 465, "use_ssl": True, "use_tls": False, "domains": []},
    {"key": "tencent_exmail", "label": "腾讯企业邮箱", "smtp_host": "smtp.exmail.qq.com",
     "smtp_port": 465, "use_ssl": True, "use_tls": False, "domains": []},
    {"key": "feishu", "label": "飞书邮箱", "smtp_host": "smtp.feishu.cn",
     "smtp_port": 465, "use_ssl": True, "use_tls": False, "domains": []},
    {"key": "outlook", "label": "Outlook / Hotmail", "smtp_host": "smtp-mail.outlook.com",
     "smtp_port": 587, "use_ssl": False, "use_tls": True,
     "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"]},
    {"key": "gmail", "label": "Gmail", "smtp_host": "smtp.gmail.com",
     "smtp_port": 587, "use_ssl": False, "use_tls": True,
     "domains": ["gmail.com", "googlemail.com"]},
    {"key": "qq", "label": "QQ 邮箱", "smtp_host": "smtp.qq.com",
     "smtp_port": 465, "use_ssl": True, "use_tls": False, "domains": ["qq.com", "foxmail.com"]},
    {"key": "163", "label": "网易 163", "smtp_host": "smtp.163.com",
     "smtp_port": 465, "use_ssl": True, "use_tls": False, "domains": ["163.com"]},
    {"key": "custom", "label": "自定义", "smtp_host": "", "smtp_port": 587,
     "use_ssl": False, "use_tls": True, "domains": []},
]
_BY_KEY = {p["key"]: p for p in PROVIDERS}
_BY_DOMAIN = {d: p for p in PROVIDERS for d in p["domains"]}


def guess_provider(email: str) -> dict:
    domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""
    return _BY_DOMAIN.get(domain, _BY_KEY["custom"])


def _titleize(word: str) -> str:
    if word.islower() or word.isupper():
        return word.capitalize()
    return word[:1].upper() + word[1:]


def name_from_email(email: str) -> str:
    """amy.chen01@x.com -> Amy Chen"""
    local = (email or "").split("@", 1)[0].strip()
    if not local:
        return email or ""
    cleaned = re.sub(r"\d+$", "", local) or local
    parts = [p for p in re.split(r"[._\-+]+", cleaned) if p] or [cleaned]
    return " ".join(_titleize(p) for p in parts)


def derive_names(account: dict) -> tuple[str, str]:
    display = (account.get("display_name") or "").strip() or name_from_email(account.get("email") or "")
    signature = (account.get("signature_name") or "").strip() or display
    return display, signature


def serialize_account(row: dict) -> dict:
    """给前端：绝不含密码明文或密文。"""
    display, signature = derive_names(row)
    return {
        "id": row["id"], "email": row["email"],
        "display_name": row.get("display_name") or "",
        "signature_name": row.get("signature_name") or "",
        "effective_display_name": display,
        "effective_signature_name": signature,
        "provider": row.get("provider") or "custom",
        "smtp_host": row["smtp_host"], "smtp_port": row["smtp_port"],
        "smtp_username": row.get("smtp_username") or "",
        "use_ssl": bool(row["use_ssl"]), "use_tls": bool(row["use_tls"]),
        "enabled": bool(row["enabled"]), "sort_order": row.get("sort_order") or 0,
        "daily_limit": row.get("daily_limit") or DEFAULT_DAILY_LIMIT,
        "auth_mode": row.get("auth_mode") or "password",
        "has_client_id": bool(row.get("encrypted_client_id")),
        "has_refresh_token": bool(row.get("encrypted_refresh_token")),
        "status": row.get("status") or "draft",
        "last_test_at": row["last_test_at"].isoformat() if row.get("last_test_at") else None,
        "last_error": row.get("last_error"),
        "has_password": bool(row.get("encrypted_password")),
    }


def list_accounts(only_sendable: bool = False) -> list[dict]:
    sql = "SELECT * FROM mb_sender_accounts"
    if only_sendable:
        # 密码模式要有密码，OAuth 模式要有 refresh_token
        sql += (" WHERE enabled = TRUE AND status = 'ready'"
                " AND (encrypted_password IS NOT NULL OR encrypted_refresh_token IS NOT NULL)")
    sql += " ORDER BY sort_order ASC, id ASC"
    return [serialize_account(dict(r)) for r in db.query_all(sql)]


def get_account(account_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM mb_sender_accounts WHERE id = %s", (account_id,))
    return dict(row) if row else None


def _normalize_account(payload: dict) -> dict:
    email = (payload.get("email") or "").strip().lower()
    if "@" not in email:
        raise ValueError(f"邮箱格式不正确：{email or '(空)'}")
    key = (payload.get("provider") or "").strip() or guess_provider(email)["key"]
    preset = _BY_KEY.get(key, _BY_KEY["custom"])
    host = (payload.get("smtp_host") or "").strip() or preset["smtp_host"]
    if not host:
        raise ValueError(f"{email}：没有 SMTP 服务器地址，请手动填写")
    try:
        port = int(payload.get("smtp_port") or preset["smtp_port"])
    except (TypeError, ValueError):
        raise ValueError(f"{email}：SMTP 端口不是数字") from None
    if "use_ssl" in payload or "use_tls" in payload:
        use_ssl, use_tls = bool(payload.get("use_ssl")), bool(payload.get("use_tls"))
    else:
        use_ssl, use_tls = preset["use_ssl"], preset["use_tls"]
    # 带了 refresh_token 就走 OAuth2，否则密码直连
    auth_mode = (payload.get("auth_mode") or "").strip()
    if not auth_mode:
        has_token = bool((payload.get("refresh_token") or "").strip()) or bool(
            payload.get("encrypted_refresh_token"))
        auth_mode = "xoauth2" if has_token else "password"

    return {
        "email": email, "provider": key, "smtp_host": host, "smtp_port": port,
        "auth_mode": auth_mode,
        "display_name": (payload.get("display_name") or "").strip() or None,
        "signature_name": (payload.get("signature_name") or "").strip() or None,
        "smtp_username": (payload.get("smtp_username") or "").strip() or None,
        "use_ssl": use_ssl, "use_tls": use_tls,
        "enabled": bool(payload.get("enabled", True)),
        "sort_order": int(payload.get("sort_order") or 0),
        "daily_limit": int(payload.get("daily_limit") or DEFAULT_DAILY_LIMIT),
    }


def create_account(payload: dict) -> dict:
    data = _normalize_account(payload)
    if db.query_one("SELECT id FROM mb_sender_accounts WHERE email = %s", (data["email"],)):
        raise ValueError(f"账号已存在：{data['email']}")
    secrets = {
        "pwd": crypto_util.encrypt(payload["app_password"].strip())
               if (payload.get("app_password") or "").strip() else None,
        "cid": crypto_util.encrypt(payload["client_id"].strip())
               if (payload.get("client_id") or "").strip() else None,
        "rtok": crypto_util.encrypt(payload["refresh_token"].strip())
                if (payload.get("refresh_token") or "").strip() else None,
    }
    new_id = db.execute_and_fetch_id("""
        INSERT INTO mb_sender_accounts
            (email, display_name, signature_name, provider, smtp_host, smtp_port,
             smtp_username, use_ssl, use_tls, enabled, sort_order, daily_limit, auth_mode,
             encrypted_password, encrypted_client_id, encrypted_refresh_token)
        VALUES (%(email)s, %(display_name)s, %(signature_name)s, %(provider)s, %(smtp_host)s,
                %(smtp_port)s, %(smtp_username)s, %(use_ssl)s, %(use_tls)s, %(enabled)s,
                %(sort_order)s, %(daily_limit)s, %(auth_mode)s, %(pwd)s, %(cid)s, %(rtok)s)
        RETURNING id
    """, {**data, **secrets})
    return serialize_account(get_account(new_id))


def update_account(account_id: int, payload: dict) -> dict:
    current = get_account(account_id)
    if current is None:
        raise ValueError(f"账号 {account_id} 不存在")
    merged = {**current, **{k: v for k, v in payload.items() if v is not None}}
    for k in ("display_name", "signature_name", "smtp_username"):
        if k in payload:
            merged[k] = payload[k]
    data = _normalize_account(merged)

    sets = ", ".join(f"{k} = %({k})s" for k in data)
    args = dict(data)
    changed = False
    for field, column, key in (("app_password", "encrypted_password", "pwd"),
                               ("client_id", "encrypted_client_id", "cid"),
                               ("refresh_token", "encrypted_refresh_token", "rtok")):
        value = (payload.get(field) or "").strip()
        if value:
            sets += f", {column} = %({key})s"
            args[key] = crypto_util.encrypt(value)
            changed = True
    if changed:
        # 换了凭据就要重测，不能继续顶着 ready 的状态
        sets += ", status = 'draft', last_error = NULL"
    args["aid"] = account_id
    db.execute(f"UPDATE mb_sender_accounts SET {sets} WHERE id = %(aid)s", args)
    return serialize_account(get_account(account_id))


def delete_account(account_id: int) -> None:
    db.execute("DELETE FROM mb_sender_accounts WHERE id = %s", (account_id,))


_CLIENT_ID_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"|[\w.\-]+\.apps\.googleusercontent\.com)$")


def parse_import_line(line: str) -> dict:
    """把一行拆成 create_account 的 payload。

    四段格式（买来的 Outlook 号常见）：
        邮箱----密码----clientid----refreshtoken
    填了后两段的自动走 OAuth2；只填 邮箱----密码 就是密码直连。
    第五段可选，是发件人显示名。用 ---- 时按位置取值；用逗号等分隔符时，
    只有第三段长得像 client_id 才当 OAuth，否则仍按老规矩当显示名。
    """
    positional = "----" in line
    if positional:
        parts = line.split("----")
    elif re.search(r"[,\t|;]", line):
        parts = re.split(r"[,\t|;]", line)
    else:
        parts = line.split()
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return {}

    email, rest = parts[0], parts[1:]
    password = rest[0] if rest else ""
    tail = rest[1:]

    client_id = refresh_token = ""
    if len(tail) >= 2 and (positional or _CLIENT_ID_RE.match(tail[0])):
        client_id, refresh_token, tail = tail[0], tail[1], tail[2:]

    return {
        "email": email, "app_password": password,
        "client_id": client_id, "refresh_token": refresh_token,
        "display_name": " ".join(tail),
        "provider": guess_provider(email)["key"],
    }


def bulk_import(text: str) -> dict:
    created, skipped, errors = [], [], []
    for lineno, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        payload = parse_import_line(line)
        if not payload:
            continue
        try:
            created.append(create_account(payload))
        except ValueError as exc:
            (skipped if "已存在" in str(exc) else errors).append(
                payload["email"] if "已存在" in str(exc) else f"第 {lineno} 行：{exc}")
        except Exception as exc:
            errors.append(f"第 {lineno} 行：{exc}")
    return {"created": created, "skipped": skipped, "errors": errors}


def open_smtp(account: dict) -> smtplib.SMTP:
    """建立并登录 SMTP。密码模式和 XOAUTH2 模式都走这里，由 _do_login 分发。"""
    host, port = account["smtp_host"], account["smtp_port"]
    client = (smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT) if account["use_ssl"]
              else smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT))
    try:
        client.ehlo()
        if account["use_tls"] and not account["use_ssl"]:
            client.starttls()
            client.ehlo()
        _do_login(client, account)
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        raise
    return client


def friendly_smtp_error(exc: Exception) -> str:
    """把 SMTP 报错翻成人话，附上原文。错误原文一律保留，不吞。"""
    raw = str(exc)
    low = raw.lower()
    hint = ""
    if "5.7.139" in raw or "basic authentication is disabled" in low:
        hint = "微软已对该账号禁用密码直连 SMTP，需改用 OAuth2 或换企业邮箱。"
    elif "535" in raw and "5.7.8" in raw:
        hint = ("Gmail 已永久关闭「登录密码直连 SMTP」。需先开两步验证，"
                "再到 myaccount.google.com/apppasswords 生成 16 位应用专用密码（去掉空格）。")
    elif "535" in raw and "5.7.3" in raw:
        hint = ("OAuth2 登录被拒：access_token 换到了但服务端不认。多半是 client_id 对应的应用"
                "没申请 SMTP.Send 权限，或该账号所在租户没开 Authenticated SMTP。")
    elif "535" in raw:
        hint = "认证失败：密码错了，或该邮箱还没开启 SMTP 服务 / 生成授权码。"
    elif isinstance(exc, TimeoutError) or "timed out" in low:
        hint = "连接超时：检查 SMTP 地址端口，或服务器出站是否被挡。"
    elif "getaddrinfo" in low:
        hint = "SMTP 服务器地址解析不了，检查有没有写错。"
    elif "550" in raw and ("spam" in low or "频率" in raw):
        hint = "触发了服务商频控，建议降低每日配额、隔日再发。"
    return f"{raw}\n\n提示：{hint}" if hint else raw


def test_account(account_id: int) -> dict:
    account = get_account(account_id)
    if account is None:
        raise ValueError(f"账号 {account_id} 不存在")
    status, error = "ready", None
    try:
        client = open_smtp(account)
        try:
            client.quit()
        except Exception:
            pass
    except Exception as exc:
        status, error = "failed", friendly_smtp_error(exc)
    db.execute(
        "UPDATE mb_sender_accounts SET status = %s, last_error = %s, last_test_at = NOW() WHERE id = %s",
        (status, error, account_id))
    return serialize_account(get_account(account_id))


# --------------------------------------------------------------------------- #
# 图片：存 BYTEA，按 sha256 去重
# --------------------------------------------------------------------------- #

_MAGIC = [(b"\x89PNG\r\n\x1a\n", "png", "image/png"), (b"\xff\xd8\xff", "jpg", "image/jpeg"),
          (b"GIF87a", "gif", "image/gif"), (b"GIF89a", "gif", "image/gif"),
          (b"RIFF", "webp", "image/webp"), (b"BM", "bmp", "image/bmp")]


def sniff_image(blob: bytes) -> tuple[str, str]:
    for magic, ext, mime in _MAGIC:
        if blob.startswith(magic):
            return ext, mime
    return "png", "image/png"


def store_image(blob: bytes) -> int:
    """存图并返回 image_id。同样的字节只存一份。"""
    digest = hashlib.sha256(blob).hexdigest()
    row = db.query_one("SELECT id FROM mb_images WHERE sha256 = %s", (digest,))
    if row:
        return row["id"]
    _, mime = sniff_image(blob)
    return db.execute_and_fetch_id(
        "INSERT INTO mb_images (sha256, content, mime, byte_size) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (sha256) DO UPDATE SET sha256 = EXCLUDED.sha256 RETURNING id",
        (digest, psycopg2.Binary(blob), mime, len(blob)))


def load_image(image_id: int) -> tuple[bytes, str] | None:
    row = db.query_one("SELECT content, mime FROM mb_images WHERE id = %s", (image_id,))
    if not row:
        return None
    return bytes(row["content"]), row["mime"]


def prepare_image(blob: bytes) -> tuple[bytes, str]:
    """太大就压缩，避免被服务商拒信。返回 (二进制, MIME 子类型)。"""
    try:
        with Image.open(io.BytesIO(blob)) as im:
            fmt = (im.format or "JPEG").upper()
            needs_resize = max(im.size) > MAX_IMAGE_EDGE
            needs_recompress = len(blob) > MAX_IMAGE_BYTES
            if not needs_resize and not needs_recompress:
                return blob, {"JPEG": "jpeg", "PNG": "png", "GIF": "gif",
                              "WEBP": "webp"}.get(fmt, "jpeg")
            if fmt == "GIF":                       # 动图别动，压了就没了
                return blob, "gif"
            im.load()
            if needs_resize:
                im.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)
            buf = io.BytesIO()
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            if fmt == "PNG" and has_alpha:
                im.save(buf, format="PNG", optimize=True)
                return buf.getvalue(), "png"
            im.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buf.getvalue(), "jpeg"
    except Exception:
        return blob, "jpeg"                        # 认不出的格式原样发，交给收件端


# --------------------------------------------------------------------------- #
# 模板渲染 + 组装邮件
# --------------------------------------------------------------------------- #

DEFAULT_SUBJECT_TPL = "{{sender_name}} - 素材提交"
DEFAULT_BODY_TPL = "Hi,\n\n这是本次的素材，请查收。\n\n{{image}}\n\n{{signature}}"
DEFAULT_SIGNATURE_TPL = "Best regards,\n{{sender_name}}\n{{sender_email}}"

# 配对表里逐行手填的自定义参数，也是 Excel 要识别的列。
# 加字段：这里加一项 + COLUMN_ALIASES 里补别名，前端表格列和占位符会自动跟上。
ITEM_FIELDS = [("name", "name"), ("id", "id"), ("number", "number")]

PLACEHOLDERS = [
    ("{{sender_name}}", "发件账号的显示名（自动推导，可手改）"),
    ("{{sender_email}}", "发件邮箱地址"),
    ("{{recipient}}", "收件邮箱地址"),
    ("{{name}}", "自定义参数，配对表里逐行手填"),
    ("{{id}}", "自定义参数，配对表里逐行手填"),
    ("{{number}}", "自定义参数，配对表里逐行手填"),
    ("{{index}}", "第几封（从 1 开始）"),
    ("{{total}}", "本批共几封"),
    ("{{image_name}}", "图片文件名"),
    ("{{image}}", "图片插入位置（不写则自动放正文末尾）"),
    ("{{signature}}", "落款签名（不写则自动放正文末尾）"),
]


def render(text: str, variables: dict) -> str:
    """简单的 {{key}} 替换。刻意不用 Jinja——正文里出现 {% 就会炸。"""
    out = text or ""
    for key, value in variables.items():
        out = out.replace("{{" + key + "}}", str(value if value is not None else ""))
    return out


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<(p|br|div|table|img|a|span|h[1-6]|ul|ol)\b", text or "", re.I))


def to_html(text: str) -> str:
    if _looks_like_html(text):
        return text
    return escape(text or "").replace("\n", "<br>\n")


def html_to_text(html: str) -> str:
    """给 text/plain 降级版用的粗略去标签。"""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html or "")
    text = re.sub(r"(?i)<br\s*/?>[ \t]*\n?", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h[1-6]|li)>[ \t]*\n?", "\n", text)
    text = re.sub(r"(?i)<img[^>]*alt=\"([^\"]*)\"[^>]*>", r"[图片: \1]", text)
    text = re.sub(r"(?i)<img[^>]*>", "[图片]", text)
    text = re.sub(r"<[^>]+>", "", text)
    # 正文进 to_html() 时被 escape 过，这里必须还原，
    # 否则纯文本版里会出现 &#x27; &quot; 这种给机器看的东西
    text = unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def image_html(src: str, alt: str) -> str:
    return (f'<div style="margin:16px 0"><img src="{escape(src, quote=True)}" '
            f'alt="{escape(alt or "", quote=True)}" '
            'style="max-width:600px;width:100%;height:auto;display:block;border:0"></div>')


def compose(*, subject_tpl, body_tpl, signature_tpl, sender_name, sender_email,
            signature_name, recipient, index, total, image_name="", image_src="",
            extra_vars=None) -> tuple[str, str]:
    """渲染出 (主题, 正文 HTML)。

    image_src 传空表示这封信没有图，此时不自动补 {{image}}，模板里写了也替换成空。
    extra_vars 是该行的自定义参数；与内置变量同名时以内置的为准，
    避免名单里一列叫 recipient 就把收件人顶掉。
    """
    has_image = bool(image_src)
    base_vars = {**(extra_vars or {}), "sender_name": sender_name, "sender_email": sender_email,
                 "recipient": recipient, "index": index, "total": total, "image_name": image_name}
    subject = render(subject_tpl or "", base_vars).strip()
    signature_html = to_html(render(signature_tpl or "",
                                    {**base_vars, "sender_name": signature_name}))
    raw = body_tpl or ""
    if has_image and "{{image}}" not in raw:
        if "{{signature}}" in raw:
            raw = raw.replace("{{signature}}", "{{image}}\n\n{{signature}}", 1)
        else:
            raw = raw.rstrip() + "\n\n{{image}}\n\n{{signature}}"
    elif "{{signature}}" not in raw:
        raw = raw.rstrip() + "\n\n{{signature}}"

    body = to_html(render(raw, base_vars))
    body = body.replace("{{image}}", image_html(image_src, image_name) if has_image else "")
    body = body.replace("{{signature}}", signature_html)
    html = ('<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
            "Helvetica,Arial,sans-serif;font-size:15px;line-height:1.7;color:#1d1d1f\">"
            f"{body}</div>")
    return subject, html


class _RecordsDataResponse:
    """记下服务器对 DATA 的最终应答。sendmail() 会把它丢掉，
    但排查「回了 250 却没人收到」时它是唯一线索——里面一般带 queue id。"""
    last_data_response = None

    def data(self, msg):
        result = super().data(msg)
        self.last_data_response = result
        return result


class _SMTP(_RecordsDataResponse, smtplib.SMTP):
    pass


class _SMTP_SSL(_RecordsDataResponse, smtplib.SMTP_SSL):
    pass


def _open_smtp_recording(account: dict):
    """和 open_smtp 同逻辑，但用能记录 DATA 应答的子类。"""
    host, port = account["smtp_host"], account["smtp_port"]
    client = (_SMTP_SSL(host, port, timeout=SMTP_TIMEOUT) if account["use_ssl"]
              else _SMTP(host, port, timeout=SMTP_TIMEOUT))
    try:
        client.ehlo()
        if account["use_tls"] and not account["use_ssl"]:
            client.starttls()
            client.ehlo()
        _do_login(client, account)
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        raise
    return client


def send_one_email(*, account: dict, to_email: str, subject_tpl: str, body_tpl: str,
                   signature_tpl: str, from_display: str, signature_name: str,
                   image_bytes: bytes | None, image_name: str, index: int, total: int,
                   extra_vars: dict | None = None) -> dict:
    """真正发一封。异常往外抛，由调用方决定怎么记。"""
    has_image = bool(image_bytes)
    cid = make_msgid(domain="mailblaster.local")[1:-1] if has_image else ""
    subject, html = compose(
        subject_tpl=subject_tpl, body_tpl=body_tpl, signature_tpl=signature_tpl,
        sender_name=from_display, sender_email=account["email"], signature_name=signature_name,
        recipient=to_email, index=index, total=total, image_name=image_name,
        image_src=f"cid:{cid}" if has_image else "", extra_vars=extra_vars)

    root = MIMEMultipart("related") if has_image else MIMEMultipart("alternative")
    root["Subject"] = subject
    root["From"] = formataddr((from_display, account["email"]))
    root["To"] = to_email
    root["Date"] = formatdate(localtime=True)
    # 自签 Message-ID：回信/对账时拿它比对。已实测阿里云企业邮会原样保留。
    root["Message-ID"] = make_msgid(domain=account["email"].rsplit("@", 1)[-1])

    plain = MIMEText(html_to_text(html), "plain", "utf-8")
    html_part = MIMEText(html, "html", "utf-8")
    if has_image:
        alt = MIMEMultipart("alternative")
        alt.attach(plain)
        alt.attach(html_part)
        root.attach(alt)
        data, subtype = prepare_image(image_bytes)
        img = MIMEImage(data, _subtype=subtype)
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=image_name or f"image.{subtype}")
        root.attach(img)
    else:
        root.attach(plain)
        root.attach(html_part)

    client = _open_smtp_recording(account)
    try:
        client.sendmail(account["email"], [to_email], root.as_string())
        answer = getattr(client, "last_data_response", None)
        if answer:
            code, text = answer
            smtp_response = f"{code} {text.decode('utf-8', 'replace').strip()}"
        else:
            smtp_response = ""
    finally:
        try:
            client.quit()
        except Exception:
            pass
    return {"subject": subject, "body_html": html,
            "message_id": root["Message-ID"], "smtp_response": smtp_response}


# --------------------------------------------------------------------------- #
# 模板库
# --------------------------------------------------------------------------- #

def list_templates() -> list[dict]:
    return [dict(r) for r in db.query_all(
        "SELECT * FROM mb_templates ORDER BY updated_at DESC")]


def save_template(name: str, subject: str, body: str, signature: str) -> list[dict]:
    name = (name or "").strip()
    if not name:
        raise ValueError("给模板起个名字")
    db.execute("""
        INSERT INTO mb_templates (name, subject, body_html, signature_html, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (name) DO UPDATE SET subject = EXCLUDED.subject,
            body_html = EXCLUDED.body_html, signature_html = EXCLUDED.signature_html,
            updated_at = NOW()
    """, (name, subject or "", body or "", signature or ""))
    return list_templates()


def delete_template(template_id: int) -> list[dict]:
    db.execute("DELETE FROM mb_templates WHERE id = %s", (template_id,))
    return list_templates()


def defaults_for_page() -> dict:
    """页面初始值：有存过的模板就用最近改的那个，没有才退回内置默认。"""
    saved = list_templates()
    if saved:
        t = saved[0]
        return {"subject": t["subject"], "body": t["body_html"],
                "signature": t["signature_html"], "loaded_from": t["name"]}
    return {"subject": DEFAULT_SUBJECT_TPL, "body": DEFAULT_BODY_TPL,
            "signature": DEFAULT_SIGNATURE_TPL, "loaded_from": ""}


# --------------------------------------------------------------------------- #
# 发信历史 / 重传去重 / 账号轮换
# --------------------------------------------------------------------------- #

def record_send(*, recipient, material_id="", material_name="", sender_account_id=None,
                sender_email="", job_id=None, item_id=None, subject="", message_id="") -> None:
    db.execute("""
        INSERT INTO mb_history (recipient, material_id, material_name, sender_account_id,
                                sender_email, job_id, item_id, subject, message_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, ((recipient or "").strip().lower(), material_id or "", material_name or "",
          sender_account_id, sender_email or "", job_id, item_id, subject or "", message_id or ""))


def already_sent(pairs: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """哪些 (收件人, 素材id) 已经发过。素材 id 为空的不参与去重
    ——没有 id 就没法判断是不是同一条素材，宁可重发也不误杀。"""
    wanted = {(r.strip().lower(), (m or "").strip()) for r, m in pairs if r and (m or "").strip()}
    if not wanted:
        return set()
    rows = db.query_all(
        "SELECT DISTINCT recipient, material_id FROM mb_history WHERE material_id <> ''")
    have = {(r["recipient"], r["material_id"]) for r in rows}
    return wanted & have


def list_history(limit: int = 500, keyword: str = "") -> list[dict]:
    sql = "SELECT * FROM mb_history WHERE 1=1"
    args: list = []
    if keyword:
        sql += (" AND (material_id ILIKE %s OR material_name ILIKE %s OR subject ILIKE %s"
                " OR sender_email ILIKE %s OR recipient ILIKE %s)")
        args.extend([f"%{keyword}%"] * 5)
    sql += " ORDER BY sent_at DESC, id DESC LIMIT %s"
    args.append(int(limit))
    return [dict(r) for r in db.query_all(sql, tuple(args))]


def cooldown_map(recipients: list[str], days: int = COOLDOWN_DAYS) -> dict[str, dict[int, str]]:
    """{收件人: {账号id: 最近一次发给它的时间}}，只含冷却期内的。一次查完。"""
    targets = tuple({r.strip().lower() for r in recipients if r})
    if not targets:
        return {}
    rows = db.query_all("""
        SELECT recipient, sender_account_id, MAX(sent_at) AS last_at FROM mb_history
        WHERE recipient = ANY(%s) AND sender_account_id IS NOT NULL
          AND sent_at >= NOW() - (%s || ' days')::INTERVAL
        GROUP BY recipient, sender_account_id
    """, (list(targets), str(int(days))))
    out: dict[str, dict[int, str]] = {}
    for r in rows:
        out.setdefault(r["recipient"], {})[r["sender_account_id"]] = (
            r["last_at"].strftime("%Y-%m-%d %H:%M") if r["last_at"] else "")
    return out


def assign_accounts(pool: list[dict], targets: list[str],
                    days: int = COOLDOWN_DAYS) -> list[dict]:
    """给每一行挑发件账号，避开该收件人冷却期内用过的。

    池子不够时**不留空**——留空等于这行发不出去。回退到最久没用过的那个并标出来，
    让人自己决定换不换。
    """
    cooldowns = cooldown_map(targets, days)
    used_this_batch: dict[str, set[int]] = {}
    result = []
    for target in targets:
        key = (target or "").strip().lower()
        blocked = dict(cooldowns.get(key, {}))
        taken = used_this_batch.setdefault(key, set())
        fresh = [a for a in pool if a["id"] not in blocked and a["id"] not in taken]
        if fresh:
            chosen, note = fresh[0], ""
        else:
            candidates = [a for a in pool if a["id"] not in taken] or pool
            if candidates:
                chosen = sorted(candidates, key=lambda a: blocked.get(a["id"], ""))[0]
                last = blocked.get(chosen["id"], "")
                note = (f"可用账号不够，这个 {days} 天内给 {key} 发过"
                        + (f"（{last}）" if last else "") + "，可在下拉里手动换")
            else:
                chosen, note = None, "账号池是空的"
        if chosen:
            taken.add(chosen["id"])
        result.append({"account": chosen, "note": note})
    return result


def quota_state(account_id: int, daily_limit: int) -> dict:
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM mb_history WHERE sender_account_id = %s "
        "AND sent_at >= date_trunc('day', NOW())", (account_id,))
    used = row["c"] if row else 0
    return {"used": used, "limit": daily_limit, "remaining": max(0, daily_limit - used)}


# --------------------------------------------------------------------------- #
# Excel 解析：图片嵌在单元格里
# --------------------------------------------------------------------------- #

COLUMN_ALIASES = {
    "image":     ["图片", "图", "素材", "素材图", "图片素材", "image", "img", "picture", "photo"],
    "name":      ["name", "名字", "姓名", "名称", "kol名字", "koc名字", "达人名字", "达人", "昵称"],
    "id":        ["id", "编号", "素材编号", "koc编号", "kol编号", "合约号", "编码"],
    "number":    ["number", "数量", "号码", "序号", "期数", "条数", "num", "no"],
    "recipient": ["收件邮箱", "收件人", "收件邮件", "邮箱", "对方邮箱", "客户邮箱",
                  "recipient", "email", "mail", "to"],
    "status":    ["发送状态", "状态", "发送", "已发送", "是否发送", "status", "sent"],
}
_SPECIAL = {"image", "recipient", "status"}
DATA_FIELDS = [f for f in COLUMN_ALIASES if f not in _SPECIAL]
_EMAIL_RE = re.compile(r"[^@\s,;、]+@[^@\s,;、]+\.[^@\s,;、]+")
MAX_SCAN_ROWS = 10


def _norm(text) -> str:
    return re.sub(r"[\s_\-·:：()（）]+", "", str(text or "")).strip().lower()


def _match_column(cell_value) -> str | None:
    key = _norm(cell_value)
    if not key:
        return None
    for field, aliases in COLUMN_ALIASES.items():
        if key in {_norm(a) for a in aliases}:
            return field
    return None


def _image_bytes_of(image) -> bytes | None:
    """openpyxl 的 Image.ref 可能是 BytesIO / 文件对象 / PIL 图 / 裸 bytes，逐个试。"""
    ref = getattr(image, "ref", None)
    if ref is None:
        return None
    if isinstance(ref, bytes):
        return ref
    if hasattr(ref, "getvalue"):
        return ref.getvalue()
    if hasattr(ref, "read"):
        try:
            ref.seek(0)
        except Exception:
            pass
        return ref.read()
    if hasattr(ref, "save"):
        buf = io.BytesIO()
        ref.save(buf, format=getattr(ref, "format", None) or "PNG")
        return buf.getvalue()
    return None


def _anchor_row(image) -> int | None:
    """图片锚定在第几行（1 基）。OneCellAnchor 和 TwoCellAnchor 都有 _from。"""
    frm = getattr(getattr(image, "anchor", None), "_from", None)
    return int(frm.row) + 1 if frm is not None and hasattr(frm, "row") else None


def _find_header(ws):
    """判定条件是「认出至少两列」而不是写死某两个字段：用户不一定所有列都有，
    但只认出一列就当表头会把普通数据行误判成表头。"""
    for row_idx in range(1, min(MAX_SCAN_ROWS, ws.max_row or 1) + 1):
        mapping = {}
        for col_idx, cell in enumerate(ws[row_idx], start=1):
            field = _match_column(cell.value)
            if field and field not in mapping:
                mapping[field] = col_idx
        if len(mapping) >= 2:
            return row_idx, mapping
    return None, None


def parse_material_xlsx(data: bytes) -> dict:
    """解析素材 Excel。缺图/缺邮箱/已发过的行都会保留并注明原因，**不静默丢行**
    ——否则用户会以为漏发是工具吞了数据。"""
    try:
        wb = load_workbook(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"这个文件打不开，确认是 .xlsx 格式（不是 .xls / .csv）：{exc}") from exc

    ws = wb.active
    header_row, columns = _find_header(ws)
    if header_row is None:
        raise ValueError(
            f"没找到表头。需要有一行至少认出两列（图片 / {' / '.join(DATA_FIELDS)} / 收件邮箱 / 发送状态）。"
            f"已扫描前 {MAX_SCAN_ROWS} 行。")

    by_row: dict[int, list[bytes]] = {}
    orphans = 0
    for image in getattr(ws, "_images", []):
        row = _anchor_row(image)
        blob = _image_bytes_of(image)
        if blob is None:
            continue
        if row is None or row <= header_row:
            orphans += 1
            continue
        by_row.setdefault(row, []).append(blob)

    present = [f for f in DATA_FIELDS if f in columns]
    rows, errors = [], []
    if orphans:
        errors.append(f"有 {orphans} 张图没有锚定到数据行（可能浮在表头上方或悬空），已忽略")

    def cell(row_idx, field):
        if field not in columns:
            return ""
        v = ws.cell(row=row_idx, column=columns[field]).value
        return "" if v is None else str(v).strip()

    for row_idx in range(header_row + 1, (ws.max_row or header_row) + 1):
        variables = {f: cell(row_idx, f) for f in present}
        recipient_raw, status_raw = cell(row_idx, "recipient"), cell(row_idx, "status")
        blobs = by_row.get(row_idx, [])
        if not any(variables.values()) and not blobs and not recipient_raw:
            continue

        label = next((v for v in variables.values() if v), "") or str(row_idx)

        # 发送状态列非空 = 发过了，丢弃。空才入队。
        if status_raw:
            rows.append({"vars": variables, "image_bytes": None, "image_name": "",
                         "recipient": recipient_raw.lower(), "excel_row": row_idx,
                         "skip_reason": f"发送状态列已填「{status_raw}」，视为发过了"})
            continue

        emails = _EMAIL_RE.findall(recipient_raw)
        if recipient_raw and not emails:
            errors.append(f"第 {row_idx} 行（{label}）收件邮箱格式不对：{recipient_raw!r}")
        if len(emails) > 1:
            errors.append(f"第 {row_idx} 行有 {len(emails)} 个邮箱，只用第一个（{emails[0]}）。"
                          "一行一个收件人，要发给多个人请拆成多行。")
        if not blobs:
            errors.append(f"第 {row_idx} 行（{label}）没有图片，这一行不会发信")
            rows.append({"vars": dict(variables), "recipient": (emails[0].lower() if emails else ""),
                         "image_bytes": None, "image_name": "",
                         "excel_row": row_idx, "skip_reason": ""})
            continue

        # 一行叠了多张图 = 多份素材。全部展开成独立的信，而不是只取第一张。
        # 实测用户的表里第 2 行叠了 10 份不同的执照——只取第一张会静默丢掉 9 份。
        # 每份素材一封信，本来就是这个功能的语义。
        if len(blobs) > 1:
            errors.append(
                f"第 {row_idx} 行叠了 {len(blobs)} 张图，已展开成 {len(blobs)} 封"
                "（每份素材一封信）。如果其中有误放的，在下面的表里删掉对应行即可。")

        recipient = emails[0].lower() if emails else ""
        for k, blob in enumerate(blobs):
            ext = sniff_image(blob)[0]
            suffix = f"-{k + 1}" if len(blobs) > 1 else ""
            rows.append({"vars": dict(variables), "recipient": recipient,
                         "image_bytes": blob, "image_name": f"{label}{suffix}.{ext}",
                         "excel_row": row_idx, "skip_reason": ""})

    return {"rows": rows, "errors": errors, "header_row": header_row,
            "fields": present, "has_recipient": "recipient" in columns,
            "has_status": "status" in columns, "sheet": ws.title}


# --------------------------------------------------------------------------- #
# 号码池 → 主体的固定映射
# --------------------------------------------------------------------------- #

COUNTRY_BY_HEADER = {"thailand": "TH", "philippines": "PH", "singapore": "SG",
                     "malaysia": "MY", "vietnam": "VN", "indonesia": "ID", "china": "CN"}


def seed_phone_pool_from_csv(path: str) -> dict:
    """把 CSV 号码池导进库。重复导入不会产生重复行（UNIQUE 约束 + ON CONFLICT）。"""
    if not os.path.exists(path):
        return {"inserted": 0, "error": f"找不到文件 {path}"}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return {"inserted": 0, "error": "文件是空的"}

    picked: dict[str, str] = {}     # 国家 -> 用哪个表头列（E.164 优先）
    for header in rows[0]:
        if not header:
            continue
        low = header.lower()
        country = next((c for k, c in COUNTRY_BY_HEADER.items() if k in low), None)
        if not country:
            continue
        if country not in picked or "e.164" in low or "e164" in low:
            picked[country] = header

    pairs = []
    for country, header in picked.items():
        for r in rows:
            phone = (r.get(header) or "").strip()
            if phone:
                pairs.append((country, phone))
    if not pairs:
        return {"inserted": 0, "total_in_csv": 0, "countries": sorted(picked)}

    # 一次批量插进去。逐条 INSERT 的话 600 个号码就是 600 次往返远程库，
    # 在 Render 上要跑一两分钟。
    with db.get_db_cursor(commit=True) as cur:
        execute_values(cur,
                       "INSERT INTO mb_phone_pool (country_code, phone) VALUES %s "
                       "ON CONFLICT (country_code, phone) DO NOTHING", pairs)
        inserted = max(0, cur.rowcount)
    return {"inserted": inserted, "total_in_csv": len(pairs),
            "countries": sorted(picked)}


def _entity_key(registration_id: str = "", company_name: str = "") -> str:
    """主体标识：信用代码优先，它才唯一；企业名称可能有全半角/空格差异。"""
    key = re.sub(r"\s+", "", (registration_id or "").strip().upper())
    return key or re.sub(r"\s+", "", (company_name or "").strip()).upper()


def assign_phone(*, registration_id="", company_name="", country_code="") -> dict:
    """给主体分配联系电话。已分配过就原样返回，不重新挑——号码每次变来变去，
    对账时同一家子公司会被当成两家。挑不出来就留空 + 说明，**不编号码**。"""
    key = _entity_key(registration_id, company_name)
    if not key:
        return {"phone": "", "reused": False,
                "reason": "没有企业名称也没有信用代码，无法确定是哪个主体"}

    existing = db.query_one("SELECT * FROM mb_entity_phones WHERE entity_key = %s", (key,))
    if existing:
        return {"phone": existing["phone"], "reused": True, "reason": ""}

    country = (country_code or "").upper()[:2]
    free = db.query_one("""
        SELECT p.phone FROM mb_phone_pool p
        WHERE p.country_code = %s
          AND NOT EXISTS (SELECT 1 FROM mb_entity_phones e
                          WHERE e.country_code = p.country_code AND e.phone = p.phone)
        ORDER BY p.id LIMIT 1
    """, (country,))
    if not free:
        have = db.query_all("SELECT DISTINCT country_code FROM mb_phone_pool ORDER BY 1")
        names = "、".join(r["country_code"] for r in have) or "（号码池是空的）"
        total = db.query_one("SELECT COUNT(*) AS c FROM mb_phone_pool WHERE country_code = %s",
                             (country,))
        reason = (f"{country} 的号码已全部分配完，需要补充号码池" if total and total["c"]
                  else f"号码池里没有 {country or '未知地区'} 的号码，现有：{names}")
        return {"phone": "", "reused": False, "reason": reason}

    db.execute("""
        INSERT INTO mb_entity_phones (entity_key, company_name, country_code, phone, source)
        VALUES (%s, %s, %s, %s, 'pool') ON CONFLICT (entity_key) DO NOTHING
    """, (key, (company_name or "").strip(), country, free["phone"]))
    return {"phone": free["phone"], "reused": False, "reason": ""}


def list_phone_assignments() -> list[dict]:
    return [dict(r) for r in db.query_all(
        "SELECT * FROM mb_entity_phones ORDER BY country_code, company_name, entity_key")]


def set_phone(*, registration_id="", company_name="", phone="", country_code="") -> None:
    key = _entity_key(registration_id, company_name)
    if not key:
        raise ValueError("要指定号码，至少得有企业名称或统一社会信用代码")
    db.execute("""
        INSERT INTO mb_entity_phones (entity_key, company_name, country_code, phone, source)
        VALUES (%s, %s, %s, %s, 'manual')
        ON CONFLICT (entity_key) DO UPDATE SET phone = EXCLUDED.phone,
            country_code = EXCLUDED.country_code, company_name = EXCLUDED.company_name,
            source = 'manual', assigned_at = NOW()
    """, (key, (company_name or "").strip(), (country_code or "").upper()[:2], phone.strip()))


# --------------------------------------------------------------------------- #
# 营业执照 OCR（千问 / DashScope，复用项目已有的 DASHSCOPE_API_KEY）
# --------------------------------------------------------------------------- #

OCR_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
OCR_MODEL = "qwen-vl-max"
OCR_TIMEOUT = 60

OCR_PROMPT = """# Role
You extract registry data from Southeast Asian company registration documents.

# Documents you will see
These are NOT Chinese 营业执照. Two formats dominate — find the labelled field, never
the surrounding prose:

**Thailand — DBD หนังสือรับรอง (Certificate of Incorporation)**
- company_name: the value of numbered item `1. ชื่อบริษัท` (e.g. "บริษัท จอร์นนี่ บางกอก จำกัด").
  ⚠️ The sentence beginning `ขอรับรองว่าบริษัทนี้ ได้จดทะเบียนเป็นนิติบุคคล...` is boilerplate
  legal text that appears on EVERY such certificate. It is NEVER the company name.
  If you are about to return a sentence containing `ขอรับรอง` or `ประมวลกฎหมาย`, you have
  grabbed the wrong block — go back and read item 1.
- registration_id: the digits after `ทะเบียนนิติบุคคลเลขที่` (13 digits, e.g. 0115566030652).
  Not the `ที่ E...` document number in the top-left corner.
- registered_address: the value of numbered item `5. สำนักงานแห่งใหญ่ ตั้งอยู่เลขที่ ...`
- detected_country_code: TH

**Indonesia — OSS IZIN USAHA / IUMK / NIB**
- company_name: value after `Nama Pemilik Usaha` (or `Nama Perusahaan`), e.g. "PT DAMAR SEJAHTERA"
- registration_id: value after `Nomor Induk Berusaha` (NIB)
- registered_address: value after `Alamat Rumah` (or `Alamat Usaha` / `Alamat Perusahaan`),
  including city and province
- detected_country_code: ID

**Other formats** (Philippines SEC/DTI, Singapore ACRA, Malaysia SSM, Vietnam, Chinese 营业执照):
find the field explicitly labelled as the entity name / registration number / registered address.

# Hard rules
1. Return the VALUE of a labelled field, never the label, never a full sentence, never
   boilerplate that would appear identically on another company's document.
2. NEVER invent, guess, complete, or "correct" a value. If a field is missing, illegible,
   redacted, or you are not certain, return "" for it. An empty string is always better than
   a plausible-looking wrong value — a wrong registration number is worse than no number.
3. Transcribe digits one by one. Do not normalise, reformat, or drop leading zeros.
4. company_name: keep the original script and the legal form
   (บริษัท ... จำกัด / PT ... / ... Pte Ltd / ... Sdn Bhd). Do not translate.
5. If the image is not a company registration document at all, return all fields as "".

# Output
Return ONLY a raw JSON object. No markdown fences, no commentary.

{"company_name": "string", "registration_id": "string",
 "registered_address": "string", "detected_country_code": "string"}"""

# 统一社会信用代码带校验位。OCR 认错一位数字时值看起来完全正常，
# 只有校验位能发现——而错的代码比空值危险得多。
_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def check_uscc(code: str):
    """True/False = 校验通过与否；None = 不是 18 位 USCC（境外注册号没有统一校验规则）。"""
    code = (code or "").strip().upper()
    if len(code) != 18 or any(c not in _USCC_CHARS for c in code):
        return None
    total = sum(_USCC_CHARS.index(c) * w for c, w in zip(code[:17], _USCC_WEIGHTS))
    expect = 31 - (total % 31)
    return _USCC_CHARS[0 if expect == 31 else expect] == code[17]


class OCRError(RuntimeError):
    pass


def _ocr_call(image_bytes: bytes) -> dict:
    key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not key:
        raise OCRError("未配置 DASHSCOPE_API_KEY，无法做营业执照识别")
    _, mime = sniff_image(image_bytes)
    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    payload = {"model": OCR_MODEL, "temperature": 0, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": OCR_PROMPT}]}]}
    req = urllib.request.Request(
        OCR_ENDPOINT, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=OCR_TIMEOUT) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        hint = ""
        if exc.code in (401, 403):
            hint = "　DASHSCOPE_API_KEY 不对或没权限"
        elif exc.code == 429:
            hint = "　被限流，稍后重试"
        raise OCRError(f"千问接口返回 {exc.code}：{raw[:300]}{hint}") from exc
    except urllib.error.URLError as exc:
        raise OCRError(f"连不上千问接口：{exc.reason}") from exc

    try:
        text = body["choices"][0]["message"]["content"]
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    except (KeyError, IndexError, TypeError) as exc:
        raise OCRError(f"接口返回的结构不认识：{json.dumps(body, ensure_ascii=False)[:300]}") from exc

    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise OCRError(f"模型没有返回 JSON：{text[:200]}")
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise OCRError(f"模型返回的 JSON 解析失败：{text[:200]}") from exc

    return {"company_name": str(parsed.get("company_name") or "").strip(),
            "registration_id": str(parsed.get("registration_id") or "").strip(),
            "registered_address": str(parsed.get("registered_address") or "").strip(),
            "country_code": str(parsed.get("detected_country_code") or "").strip().upper()[:2],
            "_raw": json.dumps(parsed, ensure_ascii=False)}


def extract_license(image_bytes: bytes) -> dict:
    """识别一张营业执照。同一张图（按 sha256）只调一次接口。"""
    digest = hashlib.sha256(image_bytes).hexdigest()
    cached = db.query_one(
        "SELECT * FROM mb_license_ocr WHERE image_sha256 = %s AND status = 'done'", (digest,))
    if cached:
        return {k: cached[k] for k in
                ("company_name", "registration_id", "registered_address", "country_code")}
    try:
        result = _ocr_call(image_bytes)
    except OCRError as exc:
        db.execute("""
            INSERT INTO mb_license_ocr (image_sha256, model, status, error)
            VALUES (%s, %s, 'failed', %s)
            ON CONFLICT (image_sha256) DO UPDATE SET status = 'failed', error = EXCLUDED.error
        """, (digest, OCR_MODEL, str(exc)))
        raise
    db.execute("""
        INSERT INTO mb_license_ocr (image_sha256, company_name, registration_id,
            registered_address, country_code, raw_json, model, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'done')
        ON CONFLICT (image_sha256) DO UPDATE SET company_name = EXCLUDED.company_name,
            registration_id = EXCLUDED.registration_id,
            registered_address = EXCLUDED.registered_address,
            country_code = EXCLUDED.country_code, raw_json = EXCLUDED.raw_json,
            status = 'done', error = NULL
    """, (digest, result["company_name"], result["registration_id"],
          result["registered_address"], result["country_code"], result["_raw"], OCR_MODEL))
    result.pop("_raw", None)
    return result


# --------------------------------------------------------------------------- #
# 批次：建、读、发
# --------------------------------------------------------------------------- #

MIN_GAP_SECONDS = 3.0
MAX_GAP_SECONDS = 8.0


def _parse_ocr_report(raw) -> dict:
    """统一成 {total, notes}。老格式是裸的 notes 列表，兼容一下。"""
    if not raw:
        return {"total": 0, "notes": []}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {"total": 0, "notes": []}
    if isinstance(data, list):
        return {"total": len(data), "notes": data}
    return {"total": data.get("total") or 0, "notes": data.get("notes") or []}


def _serialize_item(row: dict) -> dict:
    try:
        variables = json.loads(row["vars_json"]) if row.get("vars_json") else {}
    except (TypeError, ValueError):
        variables = {}
    return {
        "id": row["id"], "seq": row["seq"], "sender_account_id": row["sender_account_id"],
        "recipient": row.get("recipient") or "", "vars": variables,
        "image_id": row.get("image_id"), "image_name": row.get("image_name") or "",
        "image_url": f"/api/mail-blaster/images/{row['image_id']}" if row.get("image_id") else None,
        "from_display": row.get("from_display") or "",
        "signature_name": row.get("signature_name") or "",
        "subject": row.get("subject"), "status": row["status"], "error": row.get("error"),
        "smtp_response": row.get("smtp_response") or "", "message_id": row.get("message_id") or "",
        "sent_at": row["sent_at"].isoformat() if row.get("sent_at") else None,
    }


def load_job(job_id: int) -> dict:
    job = db.query_one("SELECT * FROM mb_jobs WHERE id = %s", (job_id,))
    if job is None:
        raise ValueError(f"批次 {job_id} 不存在")
    items = db.query_all("SELECT * FROM mb_items WHERE job_id = %s ORDER BY seq ASC", (job_id,))
    counts = {"total": len(items), "sent": 0, "failed": 0, "pending": 0, "sending": 0, "skipped": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {
        "job": {"id": job["id"], "recipient": job["recipient"], "status": job["status"],
                "paused_reason": job["paused_reason"], "task_id": job["task_id"],
                "ocr_status": job.get("ocr_status") or "none",
                "ocr_report": _parse_ocr_report(job.get("ocr_report")),
                "subject_tpl": job["subject_tpl"], "body_tpl": job["body_tpl"],
                "signature_tpl": job["signature_tpl"]},
        "items": [_serialize_item(dict(r)) for r in items],
        "counts": counts,
    }


def sync_job(job_id: int, data: dict) -> None:
    """把前端改过的收件人 / 模板 / 配对写回。

    只写 payload 里**出现过**的键：无条件覆盖的话，一个空 body 的请求
    就能把整批的模板和收件人清成空串。
    """
    cleaners = {"recipient": lambda v: (v or "").strip(),
                "subject_tpl": lambda v: v or "", "body_tpl": lambda v: v or "",
                "signature_tpl": lambda v: v or ""}
    sets, args = [], {}
    for key, clean in cleaners.items():
        if key in data:
            sets.append(f"{key} = %({key})s")
            args[key] = clean(data[key])
    if sets:
        args["jid"] = job_id
        db.execute(f"UPDATE mb_jobs SET {', '.join(sets)} WHERE id = %(jid)s", args)

    for item in data.get("items") or []:
        fields = ["sender_account_id = %(acc)s", "from_display = %(disp)s",
                  "signature_name = %(sig)s"]
        params = {"acc": item.get("sender_account_id") or None,
                  "disp": (item.get("from_display") or "").strip(),
                  "sig": (item.get("signature_name") or "").strip(),
                  "iid": item.get("id"), "jid": job_id}
        if "vars" in item:
            fields.append("vars_json = %(vars)s")
            params["vars"] = json.dumps(item.get("vars") or {}, ensure_ascii=False)
        if "recipient" in item:
            fields.append("recipient = %(rcpt)s")
            params["rcpt"] = (item.get("recipient") or "").strip().lower()
        db.execute(
            f"UPDATE mb_items SET {', '.join(fields)} "
            "WHERE id = %(iid)s AND job_id = %(jid)s AND status <> 'sent'", params)


def _mark_failed(item_id: int, message: str) -> None:
    db.execute("UPDATE mb_items SET status = 'failed', error = %s WHERE id = %s",
               (message, item_id))


def _mark_skipped(item_id: int, reason: str) -> None:
    """跳过 ≠ 失败：不是发送出了问题，是我们主动决定不发。别混进「失败」计数，
    否则用户会去点重发。"""
    db.execute("UPDATE mb_items SET status = 'skipped', error = %s WHERE id = %s",
               (reason, item_id))


def _mark_sent(item_id: int, result: dict) -> None:
    db.execute("""
        UPDATE mb_items SET status = 'sent', error = NULL, sent_at = NOW(), subject = %s,
            body_html = %s, smtp_response = %s, message_id = %s WHERE id = %s
    """, (result["subject"], result["body_html"], result.get("smtp_response") or "",
          result.get("message_id") or "", item_id))


def send_item(job_id: int, item_id: int) -> str:
    """发一封。返回结果状态字符串，异常都记进该行不往外抛。

    投递与记账刻意分成两段：SMTP 一旦收下这封信，它就已经在对方队列里、不可撤回，
    此后任何写库失败都不能把这行标成 failed —— 用户看到红色会去点重发，
    收件人就收到两封。宁可让状态停在 sending 由人来判断。
    """
    row = db.query_one("SELECT * FROM mb_items WHERE id = %s AND job_id = %s", (item_id, job_id))
    job = db.query_one("SELECT * FROM mb_jobs WHERE id = %s", (job_id,))
    if row is None or job is None:
        return "missing"
    total = (db.query_one("SELECT COUNT(*) AS c FROM mb_items WHERE job_id = %s",
                          (job_id,)) or {}).get("c", 0)
    item = dict(row)
    recipient = (item["recipient"] or job["recipient"] or "").strip()

    db.execute("UPDATE mb_items SET status = 'sending', error = NULL WHERE id = %s", (item_id,))

    # 第一段：投递。这里失败才算这封信没发出去。
    try:
        if "@" not in recipient:
            raise ValueError("收件邮箱没填或格式不对")
        account = get_account(item["sender_account_id"]) if item["sender_account_id"] else None
        if account is None:
            raise ValueError("还没选发件账号")

        blob = None
        if item.get("image_id"):
            loaded = load_image(item["image_id"])
            blob = loaded[0] if loaded else None

        try:
            extra_vars = json.loads(item["vars_json"]) if item["vars_json"] else {}
        except (TypeError, ValueError):
            extra_vars = {}

        auto_display, auto_signature = derive_names(account)
        result = send_one_email(
            account=account, to_email=recipient,
            subject_tpl=job["subject_tpl"], body_tpl=job["body_tpl"],
            signature_tpl=job["signature_tpl"],
            from_display=item["from_display"] or auto_display,
            signature_name=item["signature_name"] or auto_signature,
            image_bytes=blob, image_name=item["image_name"] or "",
            index=item["seq"] + 1, total=total, extra_vars=extra_vars)
    except Exception as exc:
        _mark_failed(item_id, friendly_smtp_error(exc))
        return "failed"

    # 第二段：记账。信已经发出去了，这里再失败也绝不能标 failed。
    try:
        _mark_sent(item_id, result)
        record_send(recipient=recipient, material_id=extra_vars.get("id") or "",
                    material_name=extra_vars.get("name") or "",
                    sender_account_id=account["id"], sender_email=account["email"],
                    job_id=job_id, item_id=item_id, subject=result.get("subject") or "",
                    message_id=result.get("message_id") or "")
    except Exception:
        logger.exception("mail-blaster: 第 %s 封已投递但记账失败", item_id)
    return "sent"


def run_job(job_id: int, progress=None) -> dict:
    """整批发送。由 worker 调用——web 是 free 套餐会休眠，后台线程会被连同进程杀掉。"""
    import random
    import time

    db.execute("UPDATE mb_jobs SET status = 'sending', paused_reason = NULL WHERE id = %s",
               (job_id,))
    pending = [r["id"] for r in db.query_all(
        "SELECT id FROM mb_items WHERE job_id = %s AND status NOT IN ('sent','skipped') "
        "ORDER BY seq ASC", (job_id,))]

    sent = failed = skipped = 0
    for i, item_id in enumerate(pending):
        outcome = send_item(job_id, item_id)
        if outcome == "sent":
            sent += 1
        elif outcome == "failed":
            failed += 1
        elif outcome == "skipped":
            skipped += 1
        if progress:
            progress(f"已处理 {i + 1}/{len(pending)}　成功 {sent}　失败 {failed}")
        if i < len(pending) - 1:
            time.sleep(random.uniform(MIN_GAP_SECONDS, MAX_GAP_SECONDS))

    db.execute("UPDATE mb_jobs SET status = 'done', finished_at = NOW() WHERE id = %s", (job_id,))
    return {"total": len(pending), "sent": sent, "failed": failed, "skipped": skipped}


def reset_stuck_items() -> int:
    """复位上次进程被杀时卡在 sending 的行。

    刻意标成 failed 而不是 pending：那封信可能**已经送达**，自动重发会让收件人收到两封。
    """
    n = db.execute("""
        UPDATE mb_items SET status = 'failed',
            error = '进程在发送途中退出，这封信是否已投递未知。请先到发件箱确认再决定是否重发——直接重发可能让对方收到两封。'
        WHERE status = 'sending'
    """)
    db.execute("UPDATE mb_jobs SET status = 'done', finished_at = NOW() WHERE status = 'sending'")
    return n or 0


def build_previews(job_id: int) -> list[dict]:
    """逐封渲染预览。图片走 /api/mail-blaster/images/<id>，不用把 BYTEA 塞进 JSON。"""
    state = load_job(job_id)
    job, items = state["job"], state["items"]
    previews = []
    for idx, item in enumerate(items, 1):
        account = get_account(item["sender_account_id"]) if item["sender_account_id"] else None
        if account is None:
            previews.append({**item, "error": "还没选发件账号"})
            continue
        recipient = item["recipient"] or job["recipient"]
        display = item["from_display"] or derive_names(account)[0]
        signature = item["signature_name"] or derive_names(account)[1]
        subject, html = compose(
            subject_tpl=job["subject_tpl"], body_tpl=job["body_tpl"],
            signature_tpl=job["signature_tpl"], sender_name=display,
            sender_email=account["email"], signature_name=signature,
            recipient=recipient, index=idx, total=len(items),
            image_name=item["image_name"], image_src=item["image_url"] or "",
            extra_vars=item["vars"] or None)
        previews.append({**item, "from_line": f"{display} <{account['email']}>",
                         "to_line": recipient, "subject": subject, "html": html})
    return previews


def _fill_from_license(row: dict) -> dict | None:
    """name / id / number 有空缺时，拿这一行的图当营业执照识别补上。

    识别不出就留空，绝不猜。电话来自公司自有号码池，按主体做固定映射
    ——同一家子公司永远是同一个号，不是每次随机取。
    """
    variables = row["vars"]
    missing = [f for f in ("name", "id", "number") if not (variables.get(f) or "").strip()]
    if not missing or not row.get("image_bytes"):
        return None

    note = {"row": row["excel_row"], "filled": {}, "warn": "", "error": ""}
    try:
        lic = extract_license(row["image_bytes"])
    except OCRError as exc:
        note["error"] = str(exc).split("\n")[0][:160]
        return note

    if "name" in missing and lic["company_name"]:
        variables["name"] = lic["company_name"]
        note["filled"]["name"] = lic["company_name"]
    if "id" in missing and lic["registration_id"]:
        variables["id"] = lic["registration_id"]
        note["filled"]["id"] = lic["registration_id"]
        # OCR 认错一位数字时值看起来完全正常，校验位是唯一能自动发现的手段
        valid = check_uscc(lic["registration_id"])
        if valid is False:
            note["warn"] = (f"信用代码 {lic['registration_id']} 校验位不对，"
                            "大概率识别错了一位，请对着执照核一遍")
        elif valid is None:
            note["warn"] = "境外注册号没有校验规则，识别结果请人工核对"

    if "number" in missing:
        picked = assign_phone(registration_id=variables.get("id") or lic["registration_id"],
                              company_name=variables.get("name") or lic["company_name"],
                              country_code=lic["country_code"])
        if picked["phone"]:
            variables["number"] = picked["phone"]
            note["filled"]["number"] = picked["phone"] + (
                "（沿用已分配）" if picked["reused"] else "（新分配）")
        elif picked["reason"]:
            note["error"] = (note["error"] + " " + picked["reason"]).strip()

    if not lic["company_name"] and not lic["registration_id"] and not note["error"]:
        note["error"] = "这张图没识别出营业执照信息，请手填"
    return note


def create_job_from_excel(*, file_bytes: bytes, fallback_recipient: str = "",
                          subject_tpl: str = "", body_tpl: str = "", signature_tpl: str = "",
                          user_id=None, run_ocr: bool = True) -> dict:
    """从 Excel 建批次。已发过的行不入队，缺图/缺邮箱的行逐条点名。"""
    parsed = parse_material_xlsx(file_bytes)

    history_hits = already_sent(
        [(r.get("recipient") or fallback_recipient, (r["vars"].get("id") or ""))
         for r in parsed["rows"] if not r.get("skip_reason")])

    usable, skipped = [], []
    for row in parsed["rows"]:
        target = (row.get("recipient") or fallback_recipient or "").strip().lower()
        material_id = (row["vars"].get("id") or "").strip()
        if row.get("skip_reason"):
            skipped.append({"row": row["excel_row"], "reason": row["skip_reason"]})
            continue
        if (target, material_id) in history_hits:
            skipped.append({"row": row["excel_row"],
                            "reason": f"库里已有发送记录：{material_id} → {target}"})
            continue
        if not row["image_bytes"]:
            continue                                  # errors 里已经点过名了
        if not target or "@" not in target:
            skipped.append({"row": row["excel_row"], "reason": "没有收件邮箱"})
            continue
        row["_target"] = target
        usable.append(row)

    if not usable:
        detail = "\n".join(f"第 {s['row']} 行：{s['reason']}" for s in skipped[:6])
        raise ValueError("这个 Excel 里没有需要发送的行。\n"
                         + (detail or "每行需要有一张嵌在单元格里的图片和一个收件邮箱。")
                         + ("\n" + "\n".join(parsed["errors"][:4]) if parsed["errors"] else ""))

    # OCR 不在这里跑。web 是 free 套餐且 gunicorn workers=1/threads=1，
    # 一张图识别 3–8 秒，20 行就能把整个工作台堵两分钟。
    # 这里只判断需不需要识别，真正的识别交给 worker。
    needs_ocr = bool(run_ocr) and any(
        r.get("image_bytes") and any(
            not (r["vars"].get(f) or "").strip() for f in ("name", "id", "number"))
        for r in usable)

    pool = list_accounts(only_sendable=True)
    job_id = db.execute_and_fetch_id("""
        INSERT INTO mb_jobs (user_id, recipient, subject_tpl, body_tpl, signature_tpl, ocr_status)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
    """, (user_id, fallback_recipient, subject_tpl or DEFAULT_SUBJECT_TPL,
          body_tpl or DEFAULT_BODY_TPL, signature_tpl or DEFAULT_SIGNATURE_TPL,
          'pending' if needs_ocr else 'none'))

    assignments = assign_accounts(pool, [r["_target"] for r in usable])
    cooldown_notes = []
    for seq, (row, pick) in enumerate(zip(usable, assignments)):
        image_id = store_image(row["image_bytes"])
        account = pick["account"]
        if pick["note"]:
            cooldown_notes.append(f"第 {row['excel_row']} 行：{pick['note']}")
        db.execute("""
            INSERT INTO mb_items (job_id, seq, sender_account_id, recipient, image_id,
                image_name, vars_json, from_display, signature_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (job_id, seq, account["id"] if account else None, row["_target"], image_id,
              row["image_name"], json.dumps(row["vars"], ensure_ascii=False),
              account["effective_display_name"] if account else "",
              account["effective_signature_name"] if account else ""))

    payload = load_job(job_id)
    payload["pool_size"] = len(pool)
    payload["excel"] = {
        "sheet": parsed["sheet"], "header_row": parsed["header_row"],
        "fields": parsed["fields"], "has_recipient": parsed["has_recipient"],
        "has_status": parsed["has_status"], "total_rows": len(parsed["rows"]),
        "imported": len(usable), "skipped": skipped, "cooldown": cooldown_notes,
        "cooldown_days": COOLDOWN_DAYS, "needs_ocr": needs_ocr, "errors": parsed["errors"],
    }
    return payload


def run_ocr_for_job(job_id: int, progress=None) -> dict:
    """在 worker 里给整批补 name / id / number。

    和 _fill_from_license 的区别是这里从库里读 item（图片在 BYTEA 里），
    识别完写回 vars_json，报告落在 mb_jobs.ocr_report 供页面轮询。
    单行失败不影响其它行。
    """
    items = db.query_all(
        "SELECT id, seq, image_id, vars_json FROM mb_items WHERE job_id = %s ORDER BY seq",
        (job_id,))
    # 只有真正需要识别的行才计入总数，否则进度条会卡在中间不动
    todo_total = sum(
        1 for r in items
        if r["image_id"] and any(
            not ((json.loads(r["vars_json"]) if r["vars_json"] else {}).get(f) or "").strip()
            for f in ("name", "id", "number")))
    db.execute("UPDATE mb_jobs SET ocr_status = 'running', ocr_report = %s WHERE id = %s",
               (json.dumps({"total": todo_total, "notes": []}, ensure_ascii=False), job_id))

    notes = []
    for n, row in enumerate(items, 1):
        try:
            variables = json.loads(row["vars_json"]) if row["vars_json"] else {}
        except (TypeError, ValueError):
            variables = {}
        missing = [f for f in ("name", "id", "number") if not (variables.get(f) or "").strip()]
        if not missing or not row["image_id"]:
            continue

        note = {"row": row["seq"] + 1, "item_id": row["id"], "filled": {}, "warn": "", "error": ""}
        try:
            loaded = load_image(row["image_id"])
            if loaded is None:
                raise ValueError("图片读不出来")
            lic = extract_license(loaded[0])

            if "name" in missing and lic["company_name"]:
                variables["name"] = lic["company_name"]
                note["filled"]["name"] = lic["company_name"]
            if "id" in missing and lic["registration_id"]:
                variables["id"] = lic["registration_id"]
                note["filled"]["id"] = lic["registration_id"]
                # OCR 认错一位数字时值看起来完全正常，校验位是唯一能自动发现的手段
                valid = check_uscc(lic["registration_id"])
                if valid is False:
                    note["warn"] = (f"信用代码 {lic['registration_id']} 校验位不对，"
                                    "大概率识别错了一位，请对着执照核一遍")
                elif valid is None:
                    note["warn"] = "境外注册号没有校验规则，识别结果请人工核对"

            if "number" in missing:
                picked = assign_phone(
                    registration_id=variables.get("id") or lic["registration_id"],
                    company_name=variables.get("name") or lic["company_name"],
                    country_code=lic["country_code"])
                if picked["phone"]:
                    variables["number"] = picked["phone"]
                    note["filled"]["number"] = picked["phone"] + (
                        "（沿用已分配）" if picked["reused"] else "（新分配）")
                elif picked["reason"]:
                    note["error"] = picked["reason"]

            if not lic["company_name"] and not lic["registration_id"] and not note["error"]:
                note["error"] = "这张图没识别出营业执照信息，请手填"

            if note["filled"]:
                db.execute("UPDATE mb_items SET vars_json = %s WHERE id = %s",
                           (json.dumps(variables, ensure_ascii=False), row["id"]))
        except Exception as exc:                       # noqa: BLE001 单行失败不毁整批
            note["error"] = f"{type(exc).__name__}: {exc}"[:160]

        notes.append(note)
        # 每行立刻落盘，前端轮询才能一行一行地看到结果填进去
        db.execute("UPDATE mb_jobs SET ocr_report = %s WHERE id = %s",
                   (json.dumps({"total": todo_total, "notes": notes}, ensure_ascii=False), job_id))
        if progress:
            progress(f"识别中 {len(notes)}/{todo_total}")

    db.execute("UPDATE mb_jobs SET ocr_status = 'done', ocr_report = %s WHERE id = %s",
               (json.dumps({"total": todo_total, "notes": notes}, ensure_ascii=False), job_id))
    return {"processed": len(notes),
            "filled": sum(1 for x in notes if x["filled"]),
            "failed": sum(1 for x in notes if x["error"])}


# --------------------------------------------------------------------------- #
# OAuth2 / XOAUTH2
#
# 微软已对很多 outlook.com 账号关闭「密码直连 SMTP」（报 535 5.7.139），
# 这类账号只能拿 refresh_token 现换 access_token 再用 SASL XOAUTH2 登录。
# 只用标准库，不引第三方依赖。
# --------------------------------------------------------------------------- #

MICROSOFT_SCOPE = "https://outlook.office.com/SMTP.Send offline_access"
GOOGLE_SCOPE = "https://mail.google.com/"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_TIMEOUT = 20
TOKEN_EXPIRY_MARGIN = 300      # 提前 5 分钟当过期，避免拿到手就正好失效

_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_token_lock = __import__("threading").Lock()


class OAuthError(RuntimeError):
    """换令牌失败。消息里带上端点返回的原文，不吞错。"""


def _oauth_flavor(provider: str | None) -> str:
    """按服务商决定用哪家的令牌端点。默认微软——这个格式就是 Outlook 号在用。"""
    return "google" if (provider or "") == "gmail" else "microsoft"


def _token_url(flavor: str) -> str:
    if flavor == "google":
        return GOOGLE_TOKEN_URL
    tenant = (os.environ.get("MAIL_BLASTER_MS_TENANT") or "common").strip() or "common"
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


def _explain_oauth(body: str) -> str:
    low = body.lower()
    if "invalid_grant" in low:
        return "refresh_token 已失效或被撤销，需要重新授权拿一个新的"
    if "invalid_client" in low:
        return "client_id 不对，或该应用要求 client_secret"
    if "unauthorized_client" in low:
        return "该 client_id 没有被授权使用这个 scope（SMTP.Send）"
    if "invalid_scope" in low:
        return "scope 不被接受，检查应用的 API 权限配置"
    return ""


def _request_access_token(flavor: str, client_id: str, refresh_token: str) -> tuple[str, float]:
    import time
    form = {"grant_type": "refresh_token", "client_id": client_id,
            "refresh_token": refresh_token,
            "scope": GOOGLE_SCOPE if flavor == "google" else MICROSOFT_SCOPE}
    secret = (os.environ.get("MAIL_BLASTER_GOOGLE_CLIENT_SECRET") or "").strip()
    if flavor == "google" and secret:
        form["client_secret"] = secret

    req = urllib.request.Request(
        _token_url(flavor), data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=OAUTH_TIMEOUT) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        hint = _explain_oauth(raw)
        raise OAuthError(f"换 access_token 失败（{exc.code}）：{raw[:300]}"
                         + (f"\n\n提示：{hint}" if hint else "")) from exc
    except urllib.error.URLError as exc:
        raise OAuthError(f"连不上令牌端点：{exc.reason}") from exc

    token = payload.get("access_token")
    if not token:
        raise OAuthError(f"令牌端点没有返回 access_token：{json.dumps(payload)[:300]}")
    return token, time.time() + float(payload.get("expires_in") or 3600)


def get_access_token(*, provider: str | None, client_id: str, refresh_token: str) -> str:
    """带缓存地换 access_token。群发时不会每封都换。

    缓存键用 (client_id, refresh_token) 而不是邮箱——同一个 client_id 配不同
    refresh_token 也能正确区分。网络请求刻意放在锁外，不阻塞其它账号。
    """
    import time
    key = (client_id, refresh_token)
    now = time.time()
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached[1] - TOKEN_EXPIRY_MARGIN > now:
            return cached[0]
    token, expires_at = _request_access_token(_oauth_flavor(provider), client_id, refresh_token)
    with _token_lock:
        _token_cache[key] = (token, expires_at)
    return token


def invalidate_token(client_id: str, refresh_token: str) -> None:
    with _token_lock:
        _token_cache.pop((client_id, refresh_token), None)


def build_xoauth2(username: str, access_token: str) -> str:
    """SASL XOAUTH2 明文，smtplib 会代做 base64。"""
    return f"user={username}\1auth=Bearer {access_token}\1\1"


def _login_xoauth2(client, username: str, account: dict) -> None:
    client_id = crypto_util.decrypt(account.get("encrypted_client_id"))
    refresh_token = crypto_util.decrypt(account.get("encrypted_refresh_token"))
    if not client_id or not refresh_token:
        raise ValueError("该账号是 OAuth2 模式，但缺 client_id 或 refresh_token")
    token = get_access_token(provider=account.get("provider"),
                             client_id=client_id, refresh_token=refresh_token)
    auth_string = build_xoauth2(username, token)
    try:
        # 服务端回 334 挑战时按 XOAUTH2 约定回一个空行，让它把失败原因落到 535
        client.auth("XOAUTH2", lambda challenge=None: "" if challenge else auth_string)
    except smtplib.SMTPAuthenticationError:
        # 被拒的 token 不留在缓存里；这条连接多半已被服务端关掉，不在上面重试
        invalidate_token(client_id, refresh_token)
        raise


def _login_password(client, username: str, account: dict) -> None:
    password = crypto_util.decrypt(account.get("encrypted_password"))
    if not password:
        raise ValueError("该账号还没有填密码")
    client.login(username, password)


def _do_login(client, account: dict) -> None:
    username = (account.get("smtp_username") or "").strip() or account["email"]
    if (account.get("auth_mode") or "password") == "xoauth2":
        _login_xoauth2(client, username, account)
    else:
        _login_password(client, username, account)
