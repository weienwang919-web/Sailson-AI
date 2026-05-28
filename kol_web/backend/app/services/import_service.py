from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.category import normalize_category
from app.core.field_normalizer import (
    CONSUMED_HEADERS,
    build_platform_extra_fields,
    case_links as normalize_case_links,
    first_note as normalize_first_note,
    first_standard_value,
)
from app.core.pipeline import (
    clean_text,
    is_empty_value,
    link_matches_platform,
    normalize_link,
    parse_platform_blocks,
    to_float,
    to_int,
)
from app.models import ImportLog, KOLRecord, SheetTemplate

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
TEMPLATE_DIR = DATA_DIR / "templates"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

FLAT_CORE_HEADERS = {
    "",
    "类目",
    "平台",
    "Platform",
    "渠道名",
    "资源名称",
    "Creator",
    "KOL Name",
    "Channel",
    "链接",
    "Link",
    "URL",
    "Channel link",
    "国家",
    "地区",
    "国家/地区",
    "GEO",
    "GEO（国家）",
    "Market",
    "语言",
    "粉丝数\n(自然数)",
    "均观看量\n(自然数)",
    "近一个月均观看量\n(自然数)",
    "均观看量\n(自然数/CCV)",
    "均观看量\n(视频)",
    "均观看量\n(自然数)/CCV",
    "Avg View",
    "Avg View Count (3M)",
    "Expected\n30dviews/CCV",
    "Expected\n30d views/CCV",
    "Expected \n30d views/CCV",
    "Name",
    "KOL",
    "kol",
    "name",
    "Category",
    "Sub category（子类别）",
    "➡️ 地区",
    "➡️ 内容标签",
    "➡️ 平台",
    "➡️ LINK",
    "➡️ Followers",
    "➡️ Avg View",
    "➡️ 主标签",
}

PLATFORM_LABELS = {
    "tiktok": "TikTok",
    "ins": "Instagram",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "yt": "YouTube",
}

def import_workbook(db: Session, source: Path, original_name: str | None = None) -> dict[str, Any]:
    filename = original_name or source.name
    saved_path = UPLOAD_DIR / filename
    if source.resolve() != saved_path.resolve():
        shutil.copy2(source, saved_path)

    xl = pd.ExcelFile(saved_path)
    added = updated = skipped = 0
    ids: list[int] = []
    for sheet in xl.sheet_names:
        df = pd.read_excel(saved_path, sheet_name=sheet, header=None)
        if df.empty or df.shape[1] == 0:
            continue
        if _looks_like_hok(df):
            save_sheet_template(db, saved_path, sheet, df)
            result = import_hok_sheet(db, df, sheet, filename)
        else:
            result = import_flat_sheet(db, df, sheet, filename)
        added += result["added"]
        updated += result["updated"]
        skipped += result["skipped"]
        ids.extend(result.get("ids", []))

    db.add(ImportLog(filename=filename, added=added, updated=updated, skipped=skipped))
    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "ids": ids, "filename": filename}


def _looks_like_hok(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return False
    row0 = {clean_text(x).lower() for x in df.iloc[0].tolist()}
    row1 = {clean_text(x).lower() for x in df.iloc[1].tolist()}
    return bool(row0 & {"tiktok", "ins", "youtube"}) and "link" in row1


def save_sheet_template(db: Session, workbook_path: Path, sheet: str, df: pd.DataFrame) -> None:
    blocks = parse_platform_blocks(df.iloc[0].tolist(), df.iloc[1].tolist())
    metadata = {
        "template_file": str(workbook_path),
        "sheet": sheet,
        "row0": [_cell(x) for x in df.iloc[0].tolist()],
        "row1": [_cell(x) for x in df.iloc[1].tolist()],
        "platform_blocks": blocks,
        "max_cols": int(df.shape[1]),
    }
    existing = db.query(SheetTemplate).filter(SheetTemplate.category == sheet).one_or_none()
    if existing:
        existing.source_file = str(workbook_path)
        existing.metadata_json = json.dumps(metadata, ensure_ascii=False)
    else:
        db.add(
            SheetTemplate(
                category=sheet,
                source_file=str(workbook_path),
                metadata_json=json.dumps(metadata, ensure_ascii=False),
            )
        )


def import_hok_sheet(db: Session, df: pd.DataFrame, sheet: str, filename: str) -> dict[str, Any]:
    blocks = parse_platform_blocks(df.iloc[0].tolist(), df.iloc[1].tolist())
    added = updated = skipped = 0
    ids: list[int] = []
    for row_idx in range(2, len(df)):
        name = clean_text(df.iloc[row_idx, 0])
        if not name:
            skipped += 1
            continue
        category = clean_text(df.iloc[row_idx, 1]) or sheet
        payload: dict[str, Any] = {
            "name": name,
            "category": category,
            "normalized_category": normalize_category(category),
            "source_file": filename,
            "extra_fields": json.dumps(_hok_extra_fields(df, row_idx, blocks), ensure_ascii=False),
            "raw_json": json.dumps(_hok_extra_fields(df, row_idx, blocks), ensure_ascii=False),
        }
        for platform, info in blocks.items():
            cols = info.get("cols", {})
            link_col = cols.get("link")
            link = _read(df, row_idx, link_col)
            if link and link_matches_platform(link, platform):
                _assign_link(payload, platform, link)
            _assign_metrics_and_prices(payload, platform, df, row_idx, cols)

        if not any(payload.get(x) for x in ("tt_link", "ins_link", "yt_link")):
            skipped += 1
            continue
        was_update, record_id = upsert_record(db, payload)
        ids.append(record_id)
        if was_update:
            updated += 1
        else:
            added += 1
    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "ids": ids}


def import_flat_sheet(db: Session, df: pd.DataFrame, sheet: str, filename: str) -> dict[str, Any]:
    if df.empty:
        return {"added": 0, "updated": 0, "skipped": 0, "ids": []}
    headers: list[str] | None = None
    last_category = ""
    added = updated = skipped = 0
    ids: list[int] = []
    for row_idx in range(len(df)):
        values = df.iloc[row_idx].tolist()
        if _looks_like_flat_header(values):
            headers = [clean_text(x) for x in values]
            continue
        if headers is None:
            skipped += 1
            continue
        row = {headers[col]: df.iloc[row_idx, col] for col in range(min(len(headers), len(values)))}
        payload = flat_row_to_payload(row, sheet, filename)
        if payload.get("category") == sheet and last_category:
            payload["category"] = last_category
            payload["normalized_category"] = normalize_category(last_category)
        elif payload.get("category") and payload.get("category") != sheet:
            last_category = clean_text(payload["category"])
        if not payload.get("name") or not any(payload.get(x) for x in ("tt_link", "ins_link", "yt_link")):
            skipped += 1
            continue
        was_update, record_id = upsert_record(db, payload)
        ids.append(record_id)
        if was_update:
            updated += 1
        else:
            added += 1
    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "ids": ids}


def flat_row_to_payload(row: dict[str, Any], sheet: str, filename: str) -> dict[str, Any]:
    def first(*names: str) -> Any:
        for name in names:
            if name in row and not is_empty_value(row[name]):
                return row[name]
        return None

    name = clean_text(first("渠道名", "资源名称", "Creator", "KOL Name", "Name", "KOL", "kol", "name"))
    category = clean_text(first("类目", "➡️ 主标签", "➡️ 内容标签", "Category", "Sub category（子类别）", "子类目")) or sheet
    platform = clean_text(first("平台", "➡️ 平台", "Platform"))
    link = clean_text(first("链接", "➡️ LINK", "Link", "URL", "Channel link", "Channel"))
    detected = detect_platform(platform, link)
    extra_fields = _flat_extra_fields(row, detected)
    payload: dict[str, Any] = {
        "name": name,
        "category": category,
        "normalized_category": normalize_category(category),
        "source_file": filename,
        "country": clean_text(first("国家", "地区", "国家/地区", "GEO", "GEO（国家）", "Market", "➡️ 地区")),
        "language": clean_text(first("语言")),
        "platform_text": platform,
        "content_tags": clean_text(first("➡️ 内容标签")),
        "main_tag": clean_text(first("➡️ 主标签")),
        "channel_content": clean_text(first("➡️ 频道主内容")),
        "recommendation": clean_text(first("推荐理由", "➡️ 推荐理由")),
        "notes": normalize_first_note(row),
        "audience_gender": first_standard_value(row, "受众性别"),
        "audience_gender_pct": clean_text(first("⬆️ 受众性别%")),
        "audience_region": first_standard_value(row, "受众地区"),
        "audience_age": first_standard_value(row, "受众年龄"),
        "case_links": normalize_case_links(row),
        "email": clean_text(first("📮Email")),
        "extra_fields": json.dumps(extra_fields, ensure_ascii=False),
        "raw_json": json.dumps({k: _cell(v) for k, v in row.items()}, ensure_ascii=False),
    }

    _assign_link(payload, detected, link)
    follower = to_int(first("粉丝数\n(自然数)", "➡️ Followers", "Followers"))
    avv = to_int(
        first(
            "均观看量\n(自然数)",
            "近一个月均观看量\n(自然数)",
            "均观看量\n(自然数/CCV)",
            "均观看量\n(视频)",
            "均观看量\n(自然数)/CCV",
            "➡️ Avg View",
            "Avg View",
            "Avg View Count (3M)",
            "Expected\n30dviews/CCV",
            "Expected\n30d views/CCV",
            "Expected \n30d views/CCV",
        )
    )
    if detected == "tiktok":
        payload["tt_follower"] = follower
        payload["tt_avv"] = avv
        payload["tt_short_video_price"] = to_float(first("💰1*Short Video (15s-60s)", "Short Video", "报价", "报价（$）", "Expected\nPrice（USD）", "Expected \nPrice", "迈蒂报价", "迈蒂报价$", "视频报价（$）"))
    elif detected == "ins":
        payload["ins_follower"] = follower
        payload["ins_post_price"] = to_float(first("💰1*Photo Post", "Post", "报价", "报价（$）", "Expected\nPrice（USD）", "Expected \nPrice", "迈蒂报价", "迈蒂报价$"))
    else:
        payload["yt_follower"] = follower
        payload["yt_avv"] = avv
        payload["yt_full_video_price"] = to_float(first("💰1*Full Video(8-10min)", "Full Video", "定制长视频报价（10-15min）", "定制长视频报价"))
        payload["yt_live_2hr_price"] = to_float(first("💰1h*Livestream", "Livestream", "直播报价", "直播1h报价", "直播1h"))
        payload["yt_pre_roll_price"] = to_float(first("💰1*Tie-in (Pre/Mid roll)", "Pre-roll", "贴片报价(90s)", "贴片", "发布CPM", "Integration (Tie-in) USD"))
        payload["yt_short_video_price"] = to_float(first("💰1*Short Video (15s-60s)", "Short Video", "定制短视频/shorts（1-3min）", "定制短视频/shorts报价（1-3min）"))
    return payload


def detect_platform(platform_text: str, link: str) -> str:
    text = platform_text.lower()
    url = link.lower()
    tokens = {token for token in text.replace("/", " ").replace(",", " ").replace("&", " ").split() if token}
    if "tiktok" in text or "tik tok" in text or "tt" in tokens or "tiktok.com" in url:
        return "tiktok"
    if "ins" in tokens or "ig" in tokens or "instagram" in text or "instagram.com" in url:
        return "ins"
    if "youtube" in text or "ytb" in tokens or "yt" in tokens or "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    return ""


def upsert_record(db: Session, payload: dict[str, Any]) -> tuple[bool, int]:
    existing = find_record_by_link(db, payload)
    if existing:
        for key, value in payload.items():
            if value not in (None, ""):
                setattr(existing, key, value)
        db.flush()
        return True, existing.id
    record = KOLRecord(**{k: v for k, v in payload.items() if hasattr(KOLRecord, k)})
    db.add(record)
    db.flush()
    return False, record.id


def find_record_by_link(db: Session, payload: dict[str, Any]) -> KOLRecord | None:
    clauses = []
    if payload.get("tt_link"):
        clauses.append(KOLRecord.tt_link == payload["tt_link"])
    if payload.get("ins_link"):
        clauses.append(KOLRecord.ins_link == payload["ins_link"])
    if payload.get("yt_link"):
        clauses.append(KOLRecord.yt_link == payload["yt_link"])
    if not clauses:
        return None
    return db.query(KOLRecord).filter(or_(*clauses)).order_by(KOLRecord.id.asc()).first()


def _looks_like_flat_header(values: list[Any]) -> bool:
    labels = {clean_text(value).lower() for value in values if clean_text(value)}
    has_link = bool(labels & {"链接", "➡️ link", "link", "url", "channel link", "channel"})
    has_name = bool(labels & {"渠道名", "资源名称", "creator", "kol name", "name"})
    has_metric = bool(labels & {"粉丝数\n(自然数)", "followers", "➡️ followers"})
    return has_link and (has_name or has_metric)


def _value_at(values: list[Any], idx: int) -> Any:
    return values[idx] if idx < len(values) else None


def _assign_link(payload: dict[str, Any], platform: str, link: str) -> None:
    normalized = normalize_link(platform, link)
    if platform == "tiktok" and normalized:
        payload["tt_link"] = normalized
    elif platform in {"ins", "instagram"} and normalized:
        payload["ins_link"] = normalized
    elif platform in {"youtube", "yt"} and normalized:
        payload["yt_link"] = normalized


def _assign_metrics_and_prices(
    payload: dict[str, Any], platform: str, df: pd.DataFrame, row_idx: int, cols: dict[str, int]
) -> None:
    follower = to_int(_read(df, row_idx, cols.get("follower")))
    avv = to_int(_read(df, row_idx, cols.get("avv")))
    if platform == "tiktok":
        payload["tt_follower"] = follower
        payload["tt_avv"] = avv
        payload["tt_short_video_price"] = to_float(_read(df, row_idx, cols.get("short video")))
        payload["tt_anchor_link_price"] = to_float(_read(df, row_idx, cols.get("anchor link")))
    elif platform == "ins":
        payload["ins_follower"] = follower
        payload["ins_post_price"] = to_float(_read(df, row_idx, cols.get("post")))
        payload["ins_reels_price"] = to_float(_read(df, row_idx, cols.get("ig reels")))
    elif platform == "youtube":
        payload["yt_follower"] = follower
        payload["yt_avv"] = avv
        payload["yt_full_video_price"] = to_float(_read(df, row_idx, cols.get("full-video")))
        payload["yt_live_2hr_price"] = to_float(_read(df, row_idx, cols.get("live 2hr")))
        payload["yt_pre_roll_price"] = to_float(_read(df, row_idx, cols.get("pre-roll")))
        payload["yt_short_video_price"] = to_float(_read(df, row_idx, cols.get("short video")))


def _read(df: pd.DataFrame, row: int, col: int | None) -> Any:
    if col is None or col >= df.shape[1]:
        return None
    value = df.iloc[row, col]
    return None if is_empty_value(value) else value


def _cell(value: Any) -> str:
    return "" if is_empty_value(value) else clean_text(value)


def _flat_extra_fields(row: dict[str, Any], platform: str) -> dict[str, str]:
    """Flat source sheets describe one platform per row, so business/audience fields belong under that platform."""
    label = PLATFORM_LABELS.get(platform, "YouTube")
    extra = build_platform_extra_fields(row, platform)
    for key, value in row.items():
        header = clean_text(key)
        if header in FLAT_CORE_HEADERS or header in CONSUMED_HEADERS:
            continue
        cell = _cell(value)
        if not cell:
            continue
        extra[f"{label} - {header}"] = cell
    return extra


def _hok_extra_fields(df: pd.DataFrame, row_idx: int, blocks: dict[str, dict[str, Any]]) -> dict[str, str]:
    extra: dict[str, str] = {
        "KOL": _cell(df.iloc[row_idx, 0]),
        "Category": _cell(df.iloc[row_idx, 1]) if df.shape[1] > 1 else "",
    }
    for platform, info in blocks.items():
        platform_label = {"tiktok": "TikTok", "ins": "INS", "youtube": "YouTube"}.get(platform, platform)
        for child, col in info.get("cols", {}).items():
            label = f"{platform_label} - {child}"
            extra[label] = _cell(_read(df, row_idx, col))
    return extra
