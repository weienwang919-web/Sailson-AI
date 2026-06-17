from __future__ import annotations

import json
import re
from typing import Any

from app.models import KOLRecord

PLATFORM_PREFIX = {
    "TikTok": "TT ",
    "Instagram": "INS ",
    "YouTube": "YT ",
    "Other": "",
}

MODEL_PRICE_FIELDS: tuple[tuple[str, str], ...] = (
    ("TT 短视频报价", "tt_short_video_price"),
    ("TT Anchor Link 报价", "tt_anchor_link_price"),
    ("INS Post 报价", "ins_post_price"),
    ("INS Reels 报价", "ins_reels_price"),
    ("YT 长视频报价", "yt_full_video_price"),
    ("YT 直播报价", "yt_live_2hr_price"),
    ("YT 贴片报价", "yt_pre_roll_price"),
    ("YT 短视频报价", "yt_short_video_price"),
)


def extra_fields_dict(record: KOLRecord) -> dict[str, Any]:
    if not record.extra_fields:
        return {}
    try:
        obj = json.loads(record.extra_fields)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def is_clean_price(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return True
    text = str(value).strip()
    if not text:
        return False
    if re.search(r"定制|贴片|直播|发布|套餐|打包|授权|视频|个月|一月", text):
        if len(re.findall(r"\d[\d,.]*", text)) >= 2:
            return False
    return True


def collect_all_prices(record: KOLRecord) -> list[tuple[str, Any]]:
    extra = extra_fields_dict(record)
    rows: list[tuple[str, Any]] = []
    seen: set[str] = set()

    def add_row(label: str, value: Any) -> None:
        if value is None or value == "":
            return
        if not is_clean_price(value):
            return
        key = f"{label}:{value}"
        if key in seen:
            return
        seen.add(key)
        rows.append((label, value))

    for label, attr in MODEL_PRICE_FIELDS:
        add_row(label, getattr(record, attr, None))

    for raw_key, value in extra.items():
        trimmed = str(raw_key).strip()
        if re.search(r"cpm|合作模式|collaboration", trimmed, re.I):
            continue
        if not re.search(r"报价|授权", trimmed):
            continue
        match = re.match(r"^(TikTok|Instagram|YouTube|Other)\s*-\s*(.+)$", trimmed)
        if match:
            platform = match.group(1)
            add_row(f"{PLATFORM_PREFIX.get(platform, '')}{match.group(2)}", value)
        else:
            add_row(trimmed, value)
    return rows


def format_all_prices_cell(record: KOLRecord) -> str:
    rows = collect_all_prices(record)
    if not rows:
        return ""
    lines: list[str] = []
    for label, value in rows:
        if isinstance(value, int):
            value_text = f"{value:,}"
        elif isinstance(value, float):
            value_text = f"{value:,.2f}".rstrip("0").rstrip(".")
        else:
            value_text = str(value)
        lines.append(f"{label} {value_text}")
    return "\n".join(lines)


def has_displayable_prices(record: KOLRecord) -> bool:
    return bool(collect_all_prices(record))


def audience_value(record: KOLRecord, model_field: str, extra_key: str) -> str:
    extra = extra_fields_dict(record)
    value = getattr(record, model_field, None) or extra.get(extra_key) or ""
    return str(value).strip() if value not in (None, "") else ""


def _to_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value in (None, ""):
        return 0.0
    text = re.sub(r"[$,￥]", "", str(value))
    try:
        return float(text)
    except ValueError:
        return 0.0


def parse_platforms_from_text(text: str) -> list[str]:
    lower = text.lower()
    found: list[str] = []
    if re.search(r"\b(tt|tiktok)\b", lower) or "tiktok" in lower:
        found.append("tiktok")
    if re.search(r"\b(ins|ig|instagram)\b", lower) or "instagram" in lower:
        found.append("ins")
    if re.search(r"\b(yt|ytb|youtube|twitch)\b", lower) or "youtube" in lower or "twitch" in lower:
        found.append("youtube")
    return list(dict.fromkeys(found))


def platform_data_score(record: KOLRecord, platform: str) -> int:
    extra = extra_fields_dict(record)
    if platform == "tiktok":
        link = record.tt_link
        follower = record.tt_follower
        avv = record.tt_avv
        model_price = record.tt_short_video_price
        main_price = extra.get("TikTok - 主报价")
        cpm = extra.get("TikTok - CPM")
    elif platform == "ins":
        link = record.ins_link
        follower = record.ins_follower
        avv = None
        model_price = record.ins_post_price
        main_price = extra.get("Instagram - 主报价")
        cpm = extra.get("Instagram - CPM")
    else:
        link = record.yt_link
        follower = record.yt_follower
        avv = record.yt_avv
        model_price = record.yt_full_video_price
        main_price = extra.get("YouTube - 主报价")
        cpm = extra.get("YouTube - CPM")

    score = 0
    if link:
        score += 10
    if follower:
        score += 5
    if avv:
        score += 2
    if _to_number(model_price) or _to_number(main_price):
        score += 3
    if _to_number(main_price):
        score += 2
    if _to_number(cpm):
        score += 1
    return score


def resolve_active_platform(record: KOLRecord) -> str | None:
    from_text = parse_platforms_from_text(record.platform_text or "")
    scored = [(platform, platform_data_score(record, platform)) for platform in ("tiktok", "ins", "youtube")]
    scored = [item for item in scored if item[1] > 0]
    if not scored:
        return from_text[0] if from_text else None
    if len(from_text) == 1:
        return from_text[0]
    if len(from_text) > 1:
        return max(from_text, key=lambda platform: platform_data_score(record, platform))
    return max(scored, key=lambda item: item[1])[0]


def unified_platform_row(record: KOLRecord) -> dict[str, Any]:
    platform = resolve_active_platform(record)
    extra = extra_fields_dict(record)
    platform_labels = {"tiktok": "TikTok", "ins": "Instagram", "youtube": "YouTube"}
    if not platform:
        return {
            "platform": record.platform_text or "",
            "link": "",
            "follower": None,
            "avv": None,
            "cpm": "",
            "collaboration": "",
        }
    if platform == "tiktok":
        return {
            "platform": platform_labels[platform],
            "link": record.tt_link or "",
            "follower": record.tt_follower,
            "avv": record.tt_avv,
            "cpm": extra.get("TikTok - CPM", ""),
            "collaboration": extra.get("TikTok - 合作模式", ""),
        }
    if platform == "ins":
        return {
            "platform": platform_labels[platform],
            "link": record.ins_link or "",
            "follower": record.ins_follower,
            "avv": None,
            "cpm": extra.get("Instagram - CPM", ""),
            "collaboration": extra.get("Instagram - 合作模式", ""),
        }
    return {
        "platform": platform_labels["youtube"],
        "link": record.yt_link or "",
        "follower": record.yt_follower,
        "avv": record.yt_avv,
        "cpm": extra.get("YouTube - CPM", ""),
        "collaboration": extra.get("YouTube - 合作模式", ""),
    }
