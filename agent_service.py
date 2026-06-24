"""Business task agent helpers.

The model is allowed to suggest intent and parameters, but execution stays in
server-side allow-listed tools.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

import sentiment_insight


URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>'\"，。；、]+", re.I)
DATE_RE = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")
MAX_DIRECT_LINKS = 20

INTENT_LABELS = {
    "sentiment_comments": "舆情评论分析",
    "video_metrics": "拉视频数据",
    "profile_video_sync": "主页视频同步",
    "kol_data_refresh_links": "达人数据更新",
    "kol_data_refresh_excel": "达人数据更新（Excel）",
    "task_query": "任务查询",
    "unknown": "需要补充信息",
}


@dataclass
class AgentDraft:
    intent: str
    params: dict[str, Any]
    reply: str
    card: dict[str, Any] | None
    needs_confirmation: bool


def build_draft(message: str, qwen_client=None) -> AgentDraft:
    """Create a first-pass action draft from user text."""
    msg = (message or "").strip()
    urls = _extract_urls(msg)
    llm_payload = _llm_extract_intent(msg, qwen_client) if qwen_client else {}
    intent = _normalize_intent(llm_payload.get("intent"))
    if intent == "unknown":
        intent = _heuristic_intent(msg, urls)

    params = _heuristic_params(msg, urls)
    if isinstance(llm_payload.get("params"), dict):
        params.update(_clean_llm_params(llm_payload["params"]))
    params["urls"] = urls or params.get("urls") or []

    if intent == "kol_data_refresh_excel" and not params.get("attachment_id"):
        reply = "可以做达人数据更新，请先上传 Excel 附件，或直接粘贴达人主页链接。"
        return AgentDraft(intent=intent, params=params, reply=reply, card=None, needs_confirmation=False)

    if intent == "task_query":
        card = _task_query_card()
        return AgentDraft(
            intent=intent,
            params={},
            reply="我会汇总最近的任务状态，包括舆情任务和达人数据更新任务。",
            card=card,
            needs_confirmation=False,
        )

    if intent == "unknown":
        return AgentDraft(
            intent=intent,
            params=params,
            reply="我还没识别出要调用哪个工具。你可以粘贴链接，并说明要做舆情分析、拉视频数据、主页同步或达人数据更新。",
            card=None,
            needs_confirmation=False,
        )

    card = build_action_card(intent, params)
    needs_confirmation = _requires_confirmation(intent, params, card)
    reply = _reply_for_card(card, needs_confirmation)
    return AgentDraft(intent=intent, params=params, reply=reply, card=card, needs_confirmation=needs_confirmation)


def build_action_card(intent: str, params: dict[str, Any]) -> dict[str, Any]:
    urls = list(dict.fromkeys(params.get("urls") or []))
    platforms = [_detect_platform(url, intent) for url in urls]
    unsupported = [url for url, platform in zip(urls, platforms) if platform == "UNKNOWN"]
    supported_count = max(0, len(urls) - len(unsupported))
    platform_counts: dict[str, int] = {}
    for platform in platforms:
        platform_counts[platform] = platform_counts.get(platform, 0) + 1

    estimated_minutes = _estimate_minutes(intent, supported_count, params)
    warnings = _warnings_for(intent, params, urls, unsupported)
    card = {
        "intent": intent,
        "task_type": INTENT_LABELS.get(intent, intent),
        "link_count": len(urls),
        "supported_count": supported_count,
        "platform_counts": platform_counts,
        "unsupported_links": unsupported,
        "time_range": _time_range_label(params),
        "write_target": _write_target_label(intent, params),
        "estimated_text": estimated_minutes,
        "risks": warnings,
        "params_preview": _params_preview(intent, params),
    }
    return card


def is_profile_url(url: str) -> bool:
    normalized = normalize_url(url)
    try:
        parsed = urlparse(normalized)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/")
    if "tiktok.com" in host:
        return bool(re.fullmatch(r"@[^/]+", path))
    if "instagram.com" in host:
        return bool(path and "/" not in path and not path.startswith(("p/", "reel/", "tv/")))
    if "youtube.com" in host:
        return path.startswith("@") or path.startswith(("channel/", "c/", "user/"))
    return False


def parse_llm_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S | re.I)
    if fenced:
        raw = fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _llm_extract_intent(message: str, qwen_client) -> dict[str, Any]:
    prompt = f"""你是业务任务路由器。请从用户输入中识别要调用的平台工具，只输出 JSON。

可选 intent:
- sentiment_comments: 对社媒帖子/视频评论做舆情分析
- video_metrics: 拉单条视频/帖子基础数据、播放量、VV
- profile_video_sync: 同步达人主页/账号主页的视频数据，可定时/写飞书
- kol_data_refresh_links: 更新达人主页粉丝、AVV、ACV，输入是链接
- kol_data_refresh_excel: 更新达人主页粉丝、AVV、ACV，输入是 Excel
- task_query: 查询任务状态、失败原因、下载入口、重试
- unknown

字段:
{{
  "intent": "...",
  "params": {{
    "sync_to_pool": false,
    "include_acv": true,
    "write_feishu": false,
    "schedule": false,
    "schedule_hour": 9,
    "sync_scope": "recent|range|all",
    "recent_days": 7,
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "max_videos": 50,
    "videos_per_profile": 10
  }}
}}

用户输入:
{message}
"""
    try:
        response = qwen_client.chat.completions.create(
            model="qwen-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return parse_llm_json(response.choices[0].message.content)
    except Exception:
        return {}


def _extract_urls(text: str) -> list[str]:
    urls = []
    for match in URL_RE.findall(text or ""):
        cleaned = match.strip().rstrip(").,，。；;]")
        normalized = normalize_url(cleaned)
        if normalized:
            urls.append(normalized)
    return list(dict.fromkeys(urls))


def _normalize_intent(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in INTENT_LABELS else "unknown"


def _heuristic_intent(message: str, urls: list[str]) -> str:
    lower = (message or "").lower()
    has_profile = any(is_profile_url(url) for url in urls)
    has_video = any(not is_profile_url(url) for url in urls)
    if any(k in message for k in ("任务", "进度", "失败", "下载", "重试", "刚才")) and not urls:
        return "task_query"
    mentions_excel_input = (
        "xlsx" in lower
        or "上传" in message
        or "附件" in message
        or "文件" in message
        or "表格里" in message
        or "表内" in message
    )
    if mentions_excel_input:
        if any(k in lower for k in ("kol", "达人", "avv", "acv", "粉丝")):
            return "kol_data_refresh_excel"
    if any(k in lower for k in ("kol", "达人", "avv", "acv", "粉丝")):
        return "kol_data_refresh_links"
    if any(k in message for k in ("主页", "账号", "定时", "每天", "飞书")) and has_profile:
        return "profile_video_sync"
    if any(k in lower for k in ("vv", "播放", "播放量", "视频数据", "基础数据", "view")) and has_video:
        return "video_metrics"
    if any(k in message for k in ("评论", "舆情", "情绪", "翻译", "负面", "正向")) and urls:
        return "sentiment_comments"
    if has_profile:
        return "profile_video_sync"
    if urls:
        return "sentiment_comments"
    return "unknown"


def _heuristic_params(message: str, urls: list[str]) -> dict[str, Any]:
    sync_to_pool = _truthy_text(message, ("同步写入", "入库", "写入kol", "写入 KOL", "资源池"))
    if _truthy_text(message, ("不入库", "不要入库", "不同步", "不写入kol", "不写入 KOL", "只导出")):
        sync_to_pool = False
    params: dict[str, Any] = {
        "sync_to_pool": sync_to_pool,
        "include_acv": not _truthy_text(message, ("不抓acv", "不要acv", "不需要acv")),
        "write_feishu": _truthy_text(message, ("飞书", "多维表格", "bitable")),
        "schedule": _truthy_text(message, ("定时", "每天", "每日", "自动")),
        "sync_scope": "recent",
        "recent_days": 7,
        "max_videos": 50,
        "videos_per_profile": 10,
    }
    if "全部" in message or "全量" in message:
        params["sync_scope"] = "all"
    days_match = re.search(r"近\s*(\d{1,3})\s*天", message)
    if days_match:
        params["recent_days"] = max(1, min(int(days_match.group(1)), 365))
        params["sync_scope"] = "recent"
    hour_match = re.search(r"(?:每天|每日|定时)?\s*(\d{1,2})\s*[点:时]", message)
    if hour_match:
        params["schedule_hour"] = max(0, min(int(hour_match.group(1)), 23))
        params["schedule"] = True
    dates = [d.replace("/", "-").replace(".", "-") for d in DATE_RE.findall(message)]
    if len(dates) >= 2:
        params["sync_scope"] = "range"
        params["start_date"] = _normalize_date_text(dates[0])
        params["end_date"] = _normalize_date_text(dates[1])
    max_match = re.search(r"(?:每主页|每个主页|每账号|上限)\D{0,8}(\d{1,4})", message)
    if max_match:
        params["max_videos"] = max(1, min(int(max_match.group(1)), 500))
    videos_match = re.search(r"(?:视频数|每主页视频数)\D{0,8}(\d{1,3})", message)
    if videos_match:
        params["videos_per_profile"] = max(1, min(int(videos_match.group(1)), 50))
    params["urls"] = urls
    return params


def _clean_llm_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "sync_to_pool",
        "include_acv",
        "write_feishu",
        "schedule",
        "sync_scope",
        "recent_days",
        "start_date",
        "end_date",
        "schedule_hour",
        "max_videos",
        "videos_per_profile",
        "attachment_id",
    ):
        if key not in params:
            continue
        value = params[key]
        if key in {"sync_to_pool", "include_acv", "write_feishu", "schedule"}:
            out[key] = bool(value)
        elif key in {"recent_days", "schedule_hour", "max_videos", "videos_per_profile"}:
            try:
                out[key] = int(value)
            except Exception:
                continue
        elif key == "sync_scope":
            out[key] = str(value) if str(value) in {"recent", "range", "all"} else "recent"
        else:
            out[key] = value
    return out


def _truthy_text(text: str, keys: tuple[str, ...]) -> bool:
    lower = (text or "").lower()
    return any(key.lower() in lower for key in keys)


def _normalize_date_text(text: str) -> str:
    parts = re.split(r"[-/.]", text)
    if len(parts) != 3:
        return text
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def _detect_platform(url: str, intent: str) -> str:
    if intent == "sentiment_comments":
        return sentiment_insight.detect_platform(url)
    return detect_platform(url)


def detect_platform(url: str) -> str:
    if not url:
        return "UNKNOWN"
    u = url.lower()
    if "tiktok.com" in u:
        return "TT"
    if "instagram.com" in u:
        return "IG"
    if "youtube.com" in u or "youtu.be" in u:
        return "YTB"
    if "facebook.com" in u or "fb.watch" in u or "fb.com" in u:
        return "FB"
    return "UNKNOWN"


def normalize_url(url: str) -> str:
    s = (url or "").strip()
    if not s or s.lower() == "nan":
        return ""
    lower = s.lower()
    if not lower.startswith(("http://", "https://")):
        if lower.startswith("www.") or any(d in lower for d in ("tiktok.com", "instagram.com", "youtube.com", "youtu.be", "facebook.com", "fb.watch")):
            s = "https://" + s
    try:
        p = urlparse(s)
        host = (p.netloc or "").lower()
        path = (p.path or "").rstrip("/")
        if not host:
            return s.rstrip("/")
        query = f"?{p.query}" if p.query else ""
        return f"{p.scheme.lower()}://{host}{path}{query}"
    except Exception:
        return s.rstrip("/")


def _estimate_minutes(intent: str, count: int, params: dict[str, Any]) -> str:
    if count <= 0:
        return "无法估算"
    if intent == "sentiment_comments":
        return f"约 {max(1, count)}-{max(2, count * 2)} 分钟"
    if intent == "video_metrics":
        return f"约 {max(1, (count + 19) // 20)}-{max(2, (count + 9) // 10)} 分钟"
    if intent == "profile_video_sync":
        return f"约 {max(2, count * 2)}-{max(4, count * 5)} 分钟"
    if intent.startswith("kol_data_refresh"):
        return f"约 {max(1, count)}-{max(2, count * 3)} 分钟"
    return "约 1-3 分钟"


def _warnings_for(intent: str, params: dict[str, Any], urls: list[str], unsupported: list[str]) -> list[str]:
    warnings = []
    if unsupported:
        warnings.append(f"{len(unsupported)} 条链接暂无法识别，会被跳过或失败。")
    if len(urls) > MAX_DIRECT_LINKS:
        warnings.append("链接数量较多，可能产生较长爬取耗时。")
    if params.get("write_feishu"):
        warnings.append("将写入飞书多维表格，执行前需要确认。")
    if params.get("sync_to_pool"):
        warnings.append("将同步写入 KOL 资源池，执行前需要确认。")
    if params.get("schedule"):
        warnings.append("将保存定时同步配置，后续会自动执行。")
    if intent == "profile_video_sync" and not params.get("write_feishu"):
        warnings.append("未识别到飞书写入目标，将使用系统默认飞书配置；如未配置会同步失败。")
    return warnings


def _time_range_label(params: dict[str, Any]) -> str:
    scope = params.get("sync_scope") or "recent"
    if scope == "all":
        return "全部"
    if scope == "range":
        return f"{params.get('start_date') or '-'} 至 {params.get('end_date') or '-'}"
    return f"近 {int(params.get('recent_days') or 7)} 天"


def _write_target_label(intent: str, params: dict[str, Any]) -> str:
    targets = []
    if params.get("write_feishu"):
        targets.append("飞书多维表格")
    if params.get("sync_to_pool"):
        targets.append("KOL 资源池")
    if intent.startswith("kol_data_refresh") and not params.get("sync_to_pool"):
        targets.append("仅导出 Excel")
    if not targets:
        targets.append("仅生成任务结果/下载文件")
    return "、".join(targets)


def _params_preview(intent: str, params: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "链接数": len(params.get("urls") or []),
    }
    if intent == "profile_video_sync":
        preview.update({
            "同步范围": _time_range_label(params),
            "每天几点": params.get("schedule_hour", 9) if params.get("schedule") else "不启用定时",
            "每主页上限": params.get("max_videos", 50),
        })
    if intent.startswith("kol_data_refresh"):
        preview.update({
            "同步入库": "是" if params.get("sync_to_pool") else "否",
            "尝试 ACV": "是" if params.get("include_acv", True) else "否",
            "每主页视频数": params.get("videos_per_profile", 10),
        })
    return preview


def _requires_confirmation(intent: str, params: dict[str, Any], card: dict[str, Any] | None) -> bool:
    if intent == "task_query":
        return False
    if params.get("write_feishu") or params.get("sync_to_pool") or params.get("schedule"):
        return True
    if (card or {}).get("link_count", 0) > MAX_DIRECT_LINKS:
        return True
    return True


def _reply_for_card(card: dict[str, Any], needs_confirmation: bool) -> str:
    task_type = card.get("task_type") or "任务"
    link_count = card.get("link_count") or 0
    if needs_confirmation:
        return f"我识别到一个「{task_type}」任务，共 {link_count} 条链接。请确认任务卡片后我再开始执行。"
    return f"我识别到一个「{task_type}」任务，可以直接查询或展示结果。"


def _task_query_card() -> dict[str, Any]:
    return {
        "intent": "task_query",
        "task_type": INTENT_LABELS["task_query"],
        "link_count": 0,
        "supported_count": 0,
        "platform_counts": {},
        "unsupported_links": [],
        "time_range": "",
        "write_target": "只读查询",
        "estimated_text": "几秒内",
        "risks": [],
        "params_preview": {},
    }


def recent_window_dates(days: int) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=max(1, int(days)))
    return start.isoformat(), end.isoformat()
