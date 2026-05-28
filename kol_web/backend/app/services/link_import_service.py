from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.category import normalize_category
from app.core.pipeline import instagram_username, normalize_link, tiktok_username, youtube_channel_seed
from app.models import KOLRecord

URL_RE = re.compile(r"https?://[^\s,;，；]+", re.I)


def import_links(db: Session, text: str) -> dict[str, Any]:
    added = 0
    updated = 0
    skipped = 0
    ids: list[int] = []
    seen: set[tuple[str, str]] = set()

    for raw_link in _extract_links(text):
        platform = detect_link_platform(raw_link)
        if not platform:
            skipped += 1
            continue
        normalized = normalize_link(platform, raw_link)
        if not normalized:
            skipped += 1
            continue
        dedupe_key = (platform, normalized)
        if dedupe_key in seen:
            skipped += 1
            continue
        seen.add(dedupe_key)

        record = find_record_by_platform_link(db, platform, normalized)
        if record:
            _assign_link(record, platform, normalized)
            record.raw_json = json.dumps({"link_import": raw_link, "normalized": normalized}, ensure_ascii=False)
            updated += 1
        else:
            record = KOLRecord(
                name=display_name_for_link(platform, normalized),
                category="Link Import",
                normalized_category=normalize_category("Link Import"),
                source_file="Link Upload",
                platform_text=platform_label(platform),
                raw_json=json.dumps({"link_import": raw_link, "normalized": normalized}, ensure_ascii=False),
            )
            _assign_link(record, platform, normalized)
            db.add(record)
            db.flush()
            added += 1
        ids.append(record.id)

    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped, "ids": ids}


def _extract_links(text: str) -> list[str]:
    return [x.rstrip(").]}>\"'") for x in URL_RE.findall(text or "")]


def detect_link_platform(link: str) -> str | None:
    host = urlparse(link).netloc.lower()
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "ins"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    return None


def find_record_by_platform_link(db: Session, platform: str, normalized: str) -> KOLRecord | None:
    if platform == "tiktok":
        return db.query(KOLRecord).filter(KOLRecord.tt_link == normalized).order_by(KOLRecord.id.asc()).first()
    if platform == "ins":
        return db.query(KOLRecord).filter(KOLRecord.ins_link == normalized).order_by(KOLRecord.id.asc()).first()
    return (
        db.query(KOLRecord)
        .filter(or_(KOLRecord.yt_link == normalized, KOLRecord.yt_link == youtube_channel_seed(normalized)))
        .order_by(KOLRecord.id.asc())
        .first()
    )


def _assign_link(record: KOLRecord, platform: str, normalized: str) -> None:
    if platform == "tiktok":
        record.tt_link = normalized
    elif platform == "ins":
        record.ins_link = normalized
    else:
        record.yt_link = normalized


def display_name_for_link(platform: str, link: str) -> str:
    if platform == "tiktok":
        user = tiktok_username(link)
        return f"TikTok @{user}" if user else link
    if platform == "ins":
        user = instagram_username(link)
        return f"Instagram @{user}" if user else link
    value = youtube_channel_seed(link).rstrip("/")
    handle = value.split("/")[-1]
    return f"YouTube {handle}" if handle else value


def platform_label(platform: str) -> str:
    return {"tiktok": "TikTok", "ins": "Instagram", "youtube": "YouTube"}.get(platform, platform)
