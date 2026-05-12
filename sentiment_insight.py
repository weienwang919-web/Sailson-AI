"""舆情洞察 v2：多平台 Apify 抓取 + AI 翻译/情感/分类。

支持平台：
  - FB  : apify/facebook-comments-scraper（评论时间通常缺失，留空）
  - IG  : apify/instagram-comment-scraper
  - TT  : clockworks/tiktok-comments-scraper
  - YTB : streamers/youtube-comments-scraper（可由环境变量覆盖）

对外暴露：
  - detect_platform(url) -> 'FB' | 'IG' | 'TT' | 'YTB' | 'UNKNOWN'
  - run_insight_pipeline(urls, apify_token, ai_call, prompt_template, progress=None)
      -> dict(structured=[...], html=str, total_comments=int, total_tokens=int)
  - build_excel(structured) -> openpyxl Workbook
  - INSIGHT_HEADERS / SCHEMA_VERSION
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import time
from typing import Callable, Iterable
from urllib.parse import urlparse

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "insight_v2"

# Excel/HTML 表头（图中样式）
INSIGHT_HEADERS = [
    "平台",
    "发布时间",
    "主贴链接",
    "主贴标题",
    "评论时间",
    "归属时间段",
    "评论人",
    "内容原文",
    "内容翻译",
    "内容判断（正向/中立/负面）",
    "人工复核（正向/中立/负面）",
    "内容分类",
]

# Apify Actor 配置（可通过环境变量覆盖）
DEFAULT_ACTORS = {
    "FB": os.environ.get("APIFY_FB_COMMENTS_ACTOR_ID", "apify/facebook-comments-scraper"),
    "IG": os.environ.get("APIFY_IG_COMMENTS_ACTOR_ID", "apify/instagram-comment-scraper"),
    "TT": os.environ.get("APIFY_TT_COMMENTS_ACTOR_ID", "clockworks/tiktok-comments-scraper"),
    "YTB": os.environ.get("APIFY_YT_COMMENTS_ACTOR_ID", "streamers/youtube-comments-scraper"),
}

# 单个 actor 最长等待时间（秒）
ACTOR_TIMEOUT_SECS = int(os.environ.get("INSIGHT_ACTOR_TIMEOUT_SECS", "420"))
ACTOR_POLL_INTERVAL = int(os.environ.get("INSIGHT_ACTOR_POLL_INTERVAL", "5"))
COMMENTS_PER_POST_LIMIT = int(os.environ.get("INSIGHT_COMMENTS_PER_POST", "500"))
AI_BATCH_SIZE = int(os.environ.get("INSIGHT_AI_BATCH_SIZE", "15"))
MAX_AI_COMMENTS = int(os.environ.get("INSIGHT_MAX_AI_COMMENTS", "1500"))

BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))

# 允许的标签（白名单，用于校验 AI 输出）
SENTIMENT_LABELS = {"正向", "中立", "负面"}
CATEGORY_LABELS = {
    "产品体验",
    "功能建议",
    "账号&充值",
    "外挂作弊",
    "活动运营",
    "客服投诉",
    "其他",
}

# 用于预处理：剥离 [Sticker] / [贴图] 等元标记
_STICKER_PREFIX_RE = re.compile(r"^\s*\[(?:sticker|贴图|表情|emoji)\]\s*", re.IGNORECASE)
# 用于识别"无实质内容"评论（纯 emoji / 重复字符 / 单符号）
_EMOJI_OR_PUNCT_RE = re.compile(
    r"[\u2600-\u27BF\U0001F300-\U0001FAFF\U0001F000-\U0001F2FF\u2300-\u23FF\u2B00-\u2BFF\s\W_]+",
    re.UNICODE,
)


def _preprocess_comment(text: str) -> tuple[str, str]:
    """评论预处理。返回 (送给 AI 的净化文本, 调用方应直接使用的翻译占位)。

    - 占位为非空时，跳过 AI 翻译，直接落库。
    - 净化文本去除了 [Sticker] 这类元标记。
    """
    if not text:
        return "", ""
    cleaned = text.strip()
    # 剥离前缀的元标记，比如 "[Sticker] 真好玩" -> "真好玩"
    cleaned = _STICKER_PREFIX_RE.sub("", cleaned).strip()

    # 如果剥离后是空的，说明原文本身只是个表情贴图
    if not cleaned:
        return "", "（贴图/无文字内容）"

    # 纯 emoji / 标点 / 单字母重复（长度短 + 没有有意义字母字数）
    letters = re.sub(r"[\W_0-9]+", "", cleaned, flags=re.UNICODE)
    if not letters:
        return cleaned, "（仅表情/符号）"
    # 灌水识别：同一个字母连续重复 ≥5 次（aaaaa / pubgggggg / hahahaha）
    if re.search(r"(.)\1{4,}", cleaned, flags=re.IGNORECASE | re.UNICODE):
        return cleaned, cleaned
    # 极端单字母重复：去重后只有 1-2 个字符
    if len(set(letters.lower())) <= 2 and len(cleaned) <= 60:
        return cleaned, cleaned
    return cleaned, ""


# ============================================
# 平台识别
# ============================================

_FB_HOSTS = ("facebook.com", "fb.com", "fb.watch", "m.facebook.com", "web.facebook.com")
_IG_HOSTS = ("instagram.com",)
_TT_HOSTS = ("tiktok.com", "vm.tiktok.com", "m.tiktok.com")
_YT_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com")


def detect_platform(url: str) -> str:
    if not url:
        return "UNKNOWN"
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except Exception:
        host = url.strip().lower()
    if any(host == h or host.endswith("." + h) or host == h.split(".", 1)[-1] for h in _FB_HOSTS):
        # 兼容 m./web. 子域
        pass
    # 简单匹配
    u = url.lower()
    if any(h in u for h in _FB_HOSTS):
        return "FB"
    if any(h in u for h in _IG_HOSTS):
        return "IG"
    if any(h in u for h in _TT_HOSTS):
        return "TT"
    if any(h in u for h in _YT_HOSTS):
        return "YTB"
    return "UNKNOWN"


# ============================================
# Apify 调用
# ============================================


def _start_actor(actor_id: str, run_input: dict, apify_token: str) -> dict:
    """启动 Apify actor，返回 run 数据（含 id）。"""
    actor_path = actor_id.replace("/", "~")
    api_url = f"https://api.apify.com/v2/acts/{actor_path}/runs"
    headers = {"Authorization": f"Bearer {apify_token}", "Content-Type": "application/json"}
    resp = requests.post(api_url, json=run_input, headers=headers, timeout=30)
    if resp.status_code != 201:
        raise RuntimeError(f"启动 {actor_id} 失败 status={resp.status_code} body={resp.text[:200]}")
    return resp.json().get("data", {})


def _wait_actor(run_id: str, apify_token: str, timeout: int = ACTOR_TIMEOUT_SECS) -> dict:
    headers = {"Authorization": f"Bearer {apify_token}"}
    api_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    start = time.time()
    while True:
        if time.time() - start > timeout:
            raise TimeoutError(f"actor run {run_id} 等待超时（{timeout}s）")
        resp = requests.get(api_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"查询 run {run_id} 失败 status={resp.status_code}")
        data = resp.json().get("data", {})
        status = data.get("status")
        if status in {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}:
            return data
        time.sleep(ACTOR_POLL_INTERVAL)


def _fetch_dataset(dataset_id: str, apify_token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {apify_token}"}
    api_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    resp = requests.get(api_url, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"获取 dataset {dataset_id} 失败 status={resp.status_code}")
    items = resp.json()
    return items if isinstance(items, list) else []


def _call_actor(actor_id: str, candidate_inputs: list[dict], apify_token: str) -> list[dict]:
    """依次尝试多个 input schema 直到一个成功返回数据；任一非空就返回。"""
    last_err = None
    for run_input in candidate_inputs:
        try:
            run = _start_actor(actor_id, run_input, apify_token)
            run_id = run.get("id")
            if not run_id:
                raise RuntimeError("actor 返回缺少 run id")
            final = _wait_actor(run_id, apify_token)
            if final.get("status") != "SUCCEEDED":
                raise RuntimeError(f"run 结束状态={final.get('status')}")
            dataset_id = final.get("defaultDatasetId")
            if not dataset_id:
                raise RuntimeError("run 缺少 defaultDatasetId")
            items = _fetch_dataset(dataset_id, apify_token)
            return items
        except Exception as e:
            last_err = e
            logger.warning(f"⚠️ actor {actor_id} 调用失败（input={list(run_input.keys())}）: {e}")
            continue
    raise RuntimeError(f"actor {actor_id} 所有候选 input 均失败: {last_err}")


# ============================================
# 字段提取（通用 + 平台特化）
# ============================================


def _first_str(item: dict, keys: Iterable[str]) -> str:
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for sub in ("name", "username", "text", "title"):
                vv = v.get(sub)
                if isinstance(vv, str) and vv.strip():
                    return vv.strip()
    return ""


def _to_beijing_dt(value) -> datetime.datetime | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            # 秒/毫秒兼容
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(BEIJING_TZ)
        s = str(value).strip()
        if not s:
            return None
        if s.isdigit():
            ts = float(s)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).astimezone(BEIJING_TZ)
        # ISO 8601
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(BEIJING_TZ)
    except Exception:
        return None


def _fmt_dt(dt: datetime.datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def _bucket_for(dt: datetime.datetime | None) -> str:
    """评论时间归属时段。"""
    if not dt:
        return ""
    h = dt.hour
    if 0 <= h < 6:
        return "凌晨"
    if 6 <= h < 12:
        return "上午"
    if 12 <= h < 14:
        return "中午"
    if 14 <= h < 18:
        return "下午"
    return "晚上"


def _extract_post_meta(items: list[dict], platform: str, fallback_url: str) -> dict:
    """从 dataset items 里尽量抽出主贴标题/发布时间。"""
    title = ""
    post_dt = None
    for it in items:
        if not isinstance(it, dict):
            continue
        if not title:
            title = _first_str(
                it,
                [
                    "postTitle",
                    "postText",
                    "postCaption",
                    "caption",
                    "videoCaption",
                    "videoDescription",
                    "videoTitle",
                    "title",
                    "description",
                    "postContent",
                ],
            )
        if not post_dt:
            for k in (
                "postPublishedAt",
                "postDate",
                "postPublishedTime",
                "videoCreateTime",
                "videoCreateTimeISO",
                "publishedTime",
                "publishedAt",
                "publishedTimeText",
                "createTimeOfPost",
            ):
                cand = _to_beijing_dt(it.get(k))
                if cand:
                    post_dt = cand
                    break
        if title and post_dt:
            break
    # 折行去多余空白，限长
    title = re.sub(r"\s+", " ", title or "").strip()
    if len(title) > 180:
        title = title[:180] + "…"
    return {
        "post_title": title,
        "post_date": _fmt_dt(post_dt),
        "post_url": fallback_url,
    }


def _extract_comment(item: dict, platform: str) -> dict | None:
    """统一抽出评论字段。返回 None 表示这条不是评论。"""
    if not isinstance(item, dict):
        return None

    text = _first_str(item, ["text", "content", "comment", "message", "commentText"])
    if not text:
        return None

    author = _first_str(
        item,
        ["authorName", "ownerUsername", "username", "userName", "uniqueId", "name", "author", "profileName"],
    )

    # 评论时间
    created_dt = None
    if platform == "FB":
        # FB 评论几乎没时间戳；尝试多个键，没有就 None
        pass
    for k in (
        "commentDate",
        "commentTime",
        "createdAtTimestamp",
        "createdAtTimestampSeconds",
        "createTime",
        "createTimeISO",
        "createdAt",
        "created_at",
        "createdTime",
        "timestamp",
        "publishedAt",
        "publishedTimeText",
        "time",
        "date",
    ):
        cand = _to_beijing_dt(item.get(k))
        if cand:
            created_dt = cand
            break

    return {
        "text": text,
        "author": author or "",
        "created_dt": created_dt,
        "created_str": _fmt_dt(created_dt),
        "bucket": _bucket_for(created_dt),
    }


# ============================================
# 平台抓取入口
# ============================================


def _scrape_facebook(url: str, apify_token: str) -> dict:
    actor = DEFAULT_ACTORS["FB"]
    candidates = [
        {
            "startUrls": [{"url": url}],
            "resultsLimit": COMMENTS_PER_POST_LIMIT,
            "includeNestedComments": True,
            "viewOption": "RANKED_UNFILTERED",
        },
        {
            "startUrls": [{"url": url}],
            "maxComments": COMMENTS_PER_POST_LIMIT,
            "maxPostCount": 1,
            "maxCommentsPerPost": COMMENTS_PER_POST_LIMIT,
            "scrapeCommentReplies": False,
        },
    ]
    items = _call_actor(actor, candidates, apify_token)
    return {"items": items, "platform": "FB", "url": url}


def _scrape_instagram(url: str, apify_token: str) -> dict:
    actor = DEFAULT_ACTORS["IG"]
    candidates = [
        {"directUrls": [url], "resultsLimit": COMMENTS_PER_POST_LIMIT},
        {"postUrls": [url], "resultsLimit": COMMENTS_PER_POST_LIMIT},
        {"startUrls": [{"url": url}], "resultsLimit": COMMENTS_PER_POST_LIMIT},
    ]
    items = _call_actor(actor, candidates, apify_token)
    return {"items": items, "platform": "IG", "url": url}


def _scrape_tiktok(url: str, apify_token: str) -> dict:
    actor = DEFAULT_ACTORS["TT"]
    candidates = [
        {"postURLs": [url], "commentsPerPost": COMMENTS_PER_POST_LIMIT, "maxRepliesPerComment": 0},
        {"postUrls": [url], "commentsPerPost": COMMENTS_PER_POST_LIMIT},
        {"startUrls": [{"url": url}], "maxItems": COMMENTS_PER_POST_LIMIT},
    ]
    items = _call_actor(actor, candidates, apify_token)
    return {"items": items, "platform": "TT", "url": url}


def _scrape_youtube(url: str, apify_token: str) -> dict:
    actor = DEFAULT_ACTORS["YTB"]
    candidates = [
        {"startUrls": [{"url": url}], "maxComments": COMMENTS_PER_POST_LIMIT, "includeReplies": False},
        {"videoUrls": [url], "maxComments": COMMENTS_PER_POST_LIMIT},
        {"startUrls": [{"url": url}], "maxResults": COMMENTS_PER_POST_LIMIT},
    ]
    items = _call_actor(actor, candidates, apify_token)
    return {"items": items, "platform": "YTB", "url": url}


PLATFORM_SCRAPERS: dict[str, Callable[[str, str], dict]] = {
    "FB": _scrape_facebook,
    "IG": _scrape_instagram,
    "TT": _scrape_tiktok,
    "YTB": _scrape_youtube,
}


# ============================================
# AI 分析（翻译 + 情感 + 分类）
# ============================================


def _safe_json_array(text: str) -> list:
    if not text:
        return []
    # 去掉可能的 ```json fenced block
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M | re.I)
    # 取第一个 [ 到最后一个 ] 的子串，保险
    lb = cleaned.find("[")
    rb = cleaned.rfind("]")
    if lb != -1 and rb != -1 and rb > lb:
        cleaned = cleaned[lb : rb + 1]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"⚠️ AI 输出 JSON 解析失败: {e} | head={text[:200]}")
        return []


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _post_check_translation(translation: str, original: str) -> str:
    """检测 AI 摆烂：当原文非中文，但译文与原文几乎一致（AI 没翻就照抄），
    返回空字符串以暴露问题，避免误导。
    """
    tr = (translation or "").strip()
    orig = (original or "").strip()
    if not tr:
        return ""
    # 原文已有中文 → 直接返回
    if _CJK_RE.search(orig):
        return tr
    # 译文没有任何中文字符 → 视为没翻
    if not _CJK_RE.search(tr):
        # 但如果原文+译文完全一致（比如 "Pubgggg" 这种灌水），允许
        if tr == orig:
            return tr
        # 否则记一次警告并清空，让导出表里那一格留空，比错位/原样照抄要好
        logger.warning(
            f"⚠️ 译文疑似未翻译（无中文字符）：原文={orig[:60]!r} 译文={tr[:60]!r}"
        )
        return ""
    return tr


def _normalize_ai_label(value: str, allowed: set[str], fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    v = value.strip()
    if v in allowed:
        return v
    # 兼容旧标签
    mapping = {
        "正面": "正向",
        "积极": "正向",
        "中性": "中立",
        "中间": "中立",
        "消极": "负面",
        "负向": "负面",
    }
    if v in mapping and mapping[v] in allowed:
        return mapping[v]
    return fallback


def _build_ai_prompt(batch_lines: str, expected_count: int) -> str:
    return (
        "你是中国头部手游社区的资深玩家与舆情分析师，同时负责把海外社媒评论翻成"
        "「中国玩家圈口语化中文」。请逐条处理下面的评论，并输出 JSON 数组（不要 markdown）。\n\n"
        f"⚠ 极重要：输入正好 {expected_count} 条评论（编号 0 ~ {expected_count - 1}），\n"
        f"   你必须严格输出 {expected_count} 个对象，按输入顺序排列，一条不能漏、一条不能加，\n"
        "   idx 必须等于输入编号，且 translation_zh 必须是中文（非中文原文绝对不要原样照抄到译文）。\n\n"
        "每个对象字段：\n"
        "  - idx: 整数，等于输入编号\n"
        "  - translation_zh: 中文译文，按下面《翻译规范》执行；若原文已是中文，直接照搬，不要再润色\n"
        "  - sentiment: 严格三选一：正向 / 中立 / 负面\n"
        "  - category: 严格七选一：产品体验 / 功能建议 / 账号&充值 / 外挂作弊 / 活动运营 / 客服投诉 / 其他\n\n"
        "《翻译规范（重要，全部遵守）》\n"
        "1. 目标语感：像中国玩家在贴吧/B站/微博/TapTap 上聊天，自然口语、可短可碎，不要书面腔。\n"
        "2. 严禁逐字直译。要把英语/阿拉伯语/西语等的语序、虚词重组成符合中文习惯的表达。\n"
        "3. 专有名词保留英文：PUBG / PUBG Mobile / UC / Royale Pass / Erangle / Miramar / Rondo 等不要翻成中文俗称。\n"
        "4. 玩家黑话本土化：bruh→老哥/兄弟、pls/please→求/拜托、reply me→求回复/官方回个话、"
        "thx→谢谢/感谢、compensation→补偿/赔偿、skin→皮肤、event→活动、bug→BUG/卡顿、laggy→卡爆/掉帧。\n"
        "5. 抱怨/讽刺/反话要传达情绪，不要翻成中性。例：'nice, now give us 1000UC as compensation' "
        "→ '行行行，那赔1000UC吧'；'where is the update bruh😭' → '更新呢老哥😭'。\n"
        "6. 请求类要保留请求感：'please add X' → '求加X'；'we want X back' → '把X弄回来吧'。\n"
        "7. emoji / 表情贴纸照抄原位，不要描述。\n"
        "8. 评论里出现的话题标签（如 #cancel_card）需译成中文话题：'#取消该卡牌'。\n"
        "9. 极短或灌水内容（'Hi'、'Reply'、'Pubgggg'、纯 emoji）：分类一律「其他」，情感按字面取中立"
        "（除非含明显情绪词）。\n"
        "10. 文本里不要出现「我作为AI…」「以下是翻译…」之类说明语，只给纯净的中文译文。\n\n"
        "《翻译示例（务必按此风格）》\n"
        "  原: Give me the glacier and we Are all good Bro\n"
        "  译: 把冰川效果还给我们就行，老哥\n"
        "  原: please pubg mobile add tha flag\n"
        "  译: 求 PUBG Mobile 加一下那个国旗\n"
        "  原: nice,now give us 1000uc as compensation\n"
        "  译: 行行行，那赔我们 1000UC 吧\n"
        "  原: where is the update bruh 😭😭\n"
        "  译: 更新呢老哥 😭😭\n"
        "  原: low device\n"
        "  译: 我这设备配置低\n"
        "  原: i want 50 material and i will apologise\n"
        "  译: 给我 50 个材料我就当没事\n"
        "  原: PUBG Mobile please make a good one emulator for pc player 🙏🏻\n"
        "  译: 求 PUBG Mobile 给 PC 玩家做一个好用的模拟器 🙏🏻\n"
        "  原: Thanks for listening to us !\n"
        "  译: 谢谢你们能听玩家的意见！\n"
        "  原: Remove the promotion thing\n"
        "  译: 把那个推广的东西撤了吧\n\n"
        "《分类口径》\n"
        "  - 产品体验：画质、卡顿、闪退、操作手感、平衡性、设备兼容\n"
        "  - 功能建议：希望新增/调整模式、地图、英雄、玩法\n"
        "  - 账号&充值：登录、充值、退款、UC、皮肤、礼包、商城\n"
        "  - 外挂作弊：外挂、脚本、代练、开挂、举报无果\n"
        "  - 活动运营：联动、节日、福利、赛事、宣传、推广物料\n"
        "  - 客服投诉：客服响应、官方处理态度、申诉、邮件无回复\n"
        "  - 其他：无法归类、纯灌水、纯表情、宗教/文化敏感诉求等\n\n"
        f"《待处理评论》\n{batch_lines}\n\n"
        '只输出 JSON 数组，例如：[{"idx":0,"translation_zh":"...","sentiment":"正向","category":"产品体验"}]\n'
    )


def _run_ai_for_comments(
    comments: list[dict],
    ai_call: Callable[[str, int], tuple[str, int]],
) -> tuple[list[dict], int]:
    """对评论调用 AI；返回 (按原顺序对齐的 AI 结果列表, 累计 token 数)。

    会先做预处理：剥离 [Sticker] 标记 + 识别灌水/纯表情类评论直接给占位翻译，
    剩余的才发给 AI，能显著降低机翻噪声 + 节省 token。
    """
    n = len(comments)
    results: list[dict] = [None] * n  # type: ignore
    total_tokens = 0
    if not comments:
        return [], 0

    # 预处理：为每条评论计算 (净化文本, 占位翻译, 是否需要 AI)
    preprocessed: list[tuple[str, str, bool]] = []
    for c in comments:
        cleaned, placeholder = _preprocess_comment(c.get("text", ""))
        # 占位非空 → 不送 AI，直接用占位作为译文
        needs_ai = not bool(placeholder)
        preprocessed.append((cleaned, placeholder, needs_ai))

    # 把需要 AI 的部分按 batch 处理
    ai_indexes = [i for i, p in enumerate(preprocessed) if p[2]]
    for start in range(0, len(ai_indexes), AI_BATCH_SIZE):
        idx_chunk = ai_indexes[start : start + AI_BATCH_SIZE]
        expected = len(idx_chunk)
        lines = "\n".join(
            f"{k}. {preprocessed[i][0].replace(chr(10), ' ')[:1000]}"
            for k, i in enumerate(idx_chunk)
        )
        prompt = _build_ai_prompt(lines, expected)
        try:
            text, tokens = ai_call(prompt, 90)
            total_tokens += int(tokens or 0)
        except Exception as e:
            logger.error(f"❌ AI 调用失败: {e}")
            text = ""
        parsed = _safe_json_array(text)
        parsed_objs = [o for o in parsed if isinstance(o, dict)]
        if len(parsed_objs) != expected:
            logger.warning(
                f"⚠️ AI 返回数量不匹配：期望 {expected}，实际 {len(parsed_objs)}；将按位置对齐+空缺留白"
            )

        for k, i in enumerate(idx_chunk):
            # 主对齐方式：位置；若 AI 返回 idx 且与 k 一致 → 用之；否则用同位置项
            obj = {}
            if k < len(parsed_objs):
                cand = parsed_objs[k]
                try:
                    cand_idx = int(cand.get("idx"))
                except Exception:
                    cand_idx = None
                # idx 不一致时，仍以位置对齐为准（避免 AI 错号导致整批后段错位）
                obj = cand
                if cand_idx is not None and cand_idx != k:
                    logger.debug(
                        f"AI 返回 idx={cand_idx} 与位置 {k} 不一致，按位置对齐"
                    )

            tr = obj.get("translation_zh") or obj.get("translation") or ""
            original = preprocessed[i][0]
            tr_clean = _post_check_translation(tr, original)
            sent = _normalize_ai_label(obj.get("sentiment"), SENTIMENT_LABELS, "中立")
            cat = _normalize_ai_label(obj.get("category"), CATEGORY_LABELS, "其他")
            results[i] = {
                "translation_zh": tr_clean,
                "sentiment": sent,
                "category": cat,
            }

    # 填充不需要 AI 的占位结果（灌水、纯表情等）
    for i, (cleaned, placeholder, needs_ai) in enumerate(preprocessed):
        if needs_ai and results[i] is not None:
            continue
        if results[i] is not None:
            continue
        results[i] = {
            "translation_zh": placeholder or "",
            "sentiment": "中立",
            "category": "其他",
        }
    return results, total_tokens


# ============================================
# 主管线
# ============================================


def run_insight_pipeline(
    urls: list[str],
    apify_token: str,
    ai_call: Callable[[str, int], tuple[str, int]],
    progress: Callable[[str], None] | None = None,
) -> dict:
    """跑完整的：多平台抓取 → AI 翻译/分类 → 结构化结果 + HTML 表格。

    Args:
        urls: 帖子链接列表
        apify_token: Apify Token
        ai_call: 用于调用大模型的函数 (prompt, timeout) -> (text, tokens)
        progress: 可选，进度回调
    Returns:
        dict 含 structured / html / total_comments / total_tokens
    """
    if not apify_token:
        raise RuntimeError("APIFY_TOKEN 未配置")

    structured: list[dict] = []
    total_tokens = 0

    def _p(msg: str):
        logger.info(f"[insight] {msg}")
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    # 1. 抓取各平台
    per_url_payloads: list[dict] = []
    for idx, url in enumerate(urls, 1):
        platform = detect_platform(url)
        _p(f"抓取 {idx}/{len(urls)} [{platform}] {url[:60]}")
        if platform == "UNKNOWN" or platform not in PLATFORM_SCRAPERS:
            logger.warning(f"⚠️ 不支持的平台，跳过: {url}")
            per_url_payloads.append({"platform": platform or "UNKNOWN", "url": url, "items": []})
            continue
        try:
            payload = PLATFORM_SCRAPERS[platform](url, apify_token)
        except Exception as e:
            logger.error(f"❌ 抓取失败 {url}: {e}")
            payload = {"platform": platform, "url": url, "items": [], "error": str(e)}
        per_url_payloads.append(payload)

    # 2. 收集评论列表（带主贴元信息），准备给 AI
    all_comments_for_ai: list[dict] = []
    for payload in per_url_payloads:
        platform = payload.get("platform", "UNKNOWN")
        url = payload.get("url", "")
        items = payload.get("items") or []
        if not items:
            continue
        meta = _extract_post_meta(items, platform, url)
        for it in items:
            c = _extract_comment(it, platform)
            if not c:
                continue
            all_comments_for_ai.append(
                {
                    "platform": platform,
                    "post_url": url,
                    "post_title": meta["post_title"],
                    "post_date": meta["post_date"],
                    "author": c["author"],
                    "text": c["text"],
                    "created_str": c["created_str"],
                    "bucket": c["bucket"],
                }
            )

    if len(all_comments_for_ai) > MAX_AI_COMMENTS:
        logger.warning(
            f"⚠️ 评论数 {len(all_comments_for_ai)} 超过上限 {MAX_AI_COMMENTS}，仅分析前 {MAX_AI_COMMENTS} 条"
        )
        all_comments_for_ai = all_comments_for_ai[:MAX_AI_COMMENTS]

    _p(f"AI 翻译/分类中（共 {len(all_comments_for_ai)} 条评论）")
    ai_results, ai_tokens = _run_ai_for_comments(all_comments_for_ai, ai_call)
    total_tokens += ai_tokens

    # 3. 合并结构化输出
    for c, r in zip(all_comments_for_ai, ai_results):
        structured.append(
            {
                "_schema": SCHEMA_VERSION,
                "platform": c["platform"],
                "post_date": c["post_date"],
                "post_url": c["post_url"],
                "post_title": c["post_title"],
                "comment_time": c["created_str"],
                "time_bucket": c["bucket"],
                "author": c["author"],
                "content": c["text"],
                "translation_zh": r.get("translation_zh", ""),
                "sentiment_ai": r.get("sentiment", "中立"),
                "sentiment_manual": "",
                "category": r.get("category", "其他"),
            }
        )

    html_table = build_html_table(structured)

    return {
        "structured": structured,
        "html": html_table,
        "total_comments": len(structured),
        "total_tokens": total_tokens,
        "per_url": per_url_payloads,  # 调试用
    }


# ============================================
# HTML / Excel 输出
# ============================================


def _html_escape(s: str) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _sentiment_color(label: str) -> str:
    return {"正向": "#2e7d32", "中立": "#616161", "负面": "#c62828"}.get(label, "#616161")


def build_html_table(rows: list[dict]) -> str:
    if not rows:
        return (
            "<div style='padding:24px;text-align:center;color:#666;'>未抓取到可分析的评论。</div>"
        )

    head_cells = "".join(
        f"<th>{_html_escape(h)}</th>" for h in INSIGHT_HEADERS
    )

    body_parts: list[str] = []
    for r in rows:
        link = _html_escape(r.get("post_url", ""))
        link_cell = (
            f"<a href=\"{link}\" target=\"_blank\" rel=\"noopener\">{link}</a>" if link else ""
        )
        sentiment = _html_escape(r.get("sentiment_ai", ""))
        color = _sentiment_color(r.get("sentiment_ai", ""))
        cells = [
            _html_escape(r.get("platform", "")),
            _html_escape(r.get("post_date", "")),
            link_cell,
            _html_escape(r.get("post_title", "")),
            _html_escape(r.get("comment_time", "")),
            _html_escape(r.get("time_bucket", "")),
            _html_escape(r.get("author", "")),
            _html_escape(r.get("content", "")),
            _html_escape(r.get("translation_zh", "")),
            f"<span style='color:{color};font-weight:600;'>{sentiment}</span>",
            _html_escape(r.get("sentiment_manual", "")),
            _html_escape(r.get("category", "")),
        ]
        body_parts.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")

    return (
        "<table class='insight-table'>"
        f"<thead><tr>{head_cells}</tr></thead>"
        f"<tbody>{''.join(body_parts)}</tbody>"
        "</table>"
    )


_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def _sanitize_sheet_name(name: str, used: set[str]) -> str:
    """Excel sheet 名约束：≤31 字符，且不含 [ ] : * ? / \\，且全工作簿唯一。"""
    cleaned = _INVALID_SHEET_CHARS.sub("·", name or "").strip()
    if not cleaned:
        cleaned = "Sheet"
    cleaned = cleaned[:31]
    base = cleaned
    suffix = 2
    while cleaned in used:
        tail = f" ({suffix})"
        cleaned = (base[: 31 - len(tail)] + tail)
        suffix += 1
    used.add(cleaned)
    return cleaned


def _write_insight_sheet(ws, rows: list[dict]) -> None:
    """把若干行写入一个 worksheet：表头 + 内容 + 样式。"""
    header_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    header_font = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    wrap = Alignment(vertical="top", wrap_text=True)

    ws.append(INSIGHT_HEADERS)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center

    for r in rows:
        ws.append([
            r.get("platform", ""),
            r.get("post_date", ""),
            r.get("post_url", ""),
            r.get("post_title", ""),
            r.get("comment_time", ""),
            r.get("time_bucket", ""),
            r.get("author", ""),
            r.get("content", ""),
            r.get("translation_zh", ""),
            r.get("sentiment_ai", ""),
            r.get("sentiment_manual", ""),
            r.get("category", ""),
        ])

    widths = [8, 18, 50, 30, 18, 12, 18, 50, 50, 18, 18, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = wrap

    ws.freeze_panes = "A2"


def build_excel(rows: list[dict]) -> Workbook:
    """生成 Excel：每个帖文一个 sheet，按出现顺序排列。

    Sheet 命名：`{平台}-{序号} {标题前几个字}`，自动截到 31 字符且唯一。
    """
    wb = Workbook()
    wb.remove(wb.active)

    if not rows:
        ws = wb.create_sheet("舆情洞察")
        _write_insight_sheet(ws, [])
        return wb

    # 按帖子分组，保留首次出现的顺序
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        key = r.get("post_url") or f"_no_url_{len(order)}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    used_names: set[str] = set()
    platform_counter: dict[str, int] = {}
    for key in order:
        post_rows = groups[key]
        first = post_rows[0]
        platform = (first.get("platform") or "POST").strip() or "POST"
        platform_counter[platform] = platform_counter.get(platform, 0) + 1
        seq = platform_counter[platform]
        title = (first.get("post_title") or "").strip()
        # 去掉换行/制表，截一段当 sheet 名后缀
        short_title = re.sub(r"\s+", " ", title)[:18]
        base = f"{platform}-{seq}"
        raw_name = f"{base} {short_title}".strip() if short_title else base
        sheet_name = _sanitize_sheet_name(raw_name, used_names)
        ws = wb.create_sheet(sheet_name)
        _write_insight_sheet(ws, post_rows)

    return wb


def parse_urls_text(text: str) -> list[str]:
    """从用户输入（多行/逗号分隔）解析出 URL 列表，去重去空。"""
    if not text:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for token in re.split(r"[\s,;\n]+", text.strip()):
        token = token.strip()
        if not token:
            continue
        if not (token.startswith("http://") or token.startswith("https://")):
            continue
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result
