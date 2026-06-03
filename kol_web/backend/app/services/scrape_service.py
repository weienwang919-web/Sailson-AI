from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.pipeline import (
    aggregate_instagram,
    aggregate_tiktok,
    aggregate_youtube,
    build_apify_inputs,
    call_apify,
    instagram_username,
    tiktok_username,
    youtube_channel_seed,
)
from app.database import SessionLocal
from app.models import KOLRecord, ScrapeJob

BASE_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = BASE_DIR / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)
USD_TO_CNY = 7.2
CRAWLER_USD_PER_1000 = 3.0


def create_scrape_job(db: Session, ids: list[int] | None = None) -> ScrapeJob:
    query = db.query(KOLRecord)
    if ids:
        query = query.filter(KOLRecord.id.in_(ids))
    total = query.count()
    job = ScrapeJob(status="pending", total=total, done=0)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def run_scrape_job(job_id: int, ids: list[int] | None = None, videos_per_profile: int = 10) -> None:
    db = SessionLocal()
    try:
        job = db.get(ScrapeJob, job_id)
        if not job:
            return
        job.status = "running"
        db.commit()

        query = db.query(KOLRecord)
        if ids:
            query = query.filter(KOLRecord.id.in_(ids))
        records = query.all()

        inputs = build_apify_inputs(records, videos_per_profile)
        raw: dict[str, list[dict]] = {}
        for platform, config in inputs.items():
            run_input = config["input"]
            if not _has_platform_targets(platform, run_input):
                raw[platform] = []
                continue
            raw_items = call_apify(config["actor"], run_input)
            raw[platform] = raw_items
            (RAW_DIR / f"{platform}_{job_id}.json").write_text(
                json.dumps(raw_items, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        metrics = {
            "tiktok": aggregate_tiktok(raw.get("tiktok", []), videos_per_profile),
            "ins": aggregate_instagram(raw.get("ins", []), videos_per_profile),
            "youtube": aggregate_youtube(raw.get("youtube", []), videos_per_profile),
        }
        update_records_with_metrics(db, records, metrics)
        item_count = sum(len(items or []) for items in raw.values())
        if not item_count:
            item_count = len(records) * max(1, int(videos_per_profile or 1))
        crawler_cost_usd = item_count * CRAWLER_USD_PER_1000 / 1000
        job.status = "completed"
        job.done = len(records)
        job.item_count = item_count
        job.crawler_items = item_count
        job.api_calls = sum(1 for items in raw.values() if items)
        job.crawler_cost_usd = crawler_cost_usd
        job.crawler_cost_cny = crawler_cost_usd * USD_TO_CNY
        job.total_cost_cny = job.crawler_cost_cny
        job.usage_detail_json = json.dumps(
            {"pricing": "crawler USD 3 / 1000 rows", "videos_per_profile": videos_per_profile},
            ensure_ascii=False,
        )
        job.updated_at = datetime.utcnow()
        db.commit()
    except Exception as exc:  # noqa: BLE001 - job error is returned to UI
        job = db.get(ScrapeJob, job_id)
        if job:
            job.status = "failed"
            job.error = str(exc)
            job.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def update_records_with_metrics(
    db: Session, records: list[KOLRecord], metrics: dict[str, dict[str, dict]]
) -> None:
    now = datetime.utcnow()
    for record in records:
        tt = _lookup_tiktok(record, metrics.get("tiktok", {}))
        if tt:
            if tt.get("followers") is not None:
                record.tt_follower = tt["followers"]
            if tt.get("avv") is not None:
                record.tt_avv = tt["avv"]
        ins = _lookup_instagram(record, metrics.get("ins", {}))
        if ins and ins.get("followers") is not None:
            record.ins_follower = ins["followers"]
        yt = _lookup_youtube(record, metrics.get("youtube", {}))
        if yt:
            if yt.get("followers") is not None:
                record.yt_follower = yt["followers"]
            if yt.get("avv") is not None:
                record.yt_avv = yt["avv"]
        record.last_scraped_at = now
    db.commit()


def _lookup_tiktok(record: KOLRecord, metrics: dict[str, dict]) -> dict | None:
    user = tiktok_username(record.tt_link or "").lower()
    return metrics.get(user) if user else None


def _lookup_instagram(record: KOLRecord, metrics: dict[str, dict]) -> dict | None:
    user = instagram_username(record.ins_link or "").lower()
    return metrics.get(user) if user else None


def _lookup_youtube(record: KOLRecord, metrics: dict[str, dict]) -> dict | None:
    link = record.yt_link or ""
    candidates = [youtube_channel_seed(link).lower()]
    if "/@" in link:
        candidates.append(link.split("/@")[-1].split("/")[0].lower())
    if "/c/" in link:
        candidates.append(link.split("/c/")[-1].split("/")[0].lower())
    if "/user/" in link:
        candidates.append(link.split("/user/")[-1].split("/")[0].lower())
    for key in candidates:
        if key in metrics:
            return metrics[key]
    for key, value in metrics.items():
        if any(c and (c in key or key in c) for c in candidates):
            return value
    return None


def _has_platform_targets(platform: str, run_input: dict) -> bool:
    if platform == "tiktok":
        return bool(run_input.get("profiles"))
    if platform == "ins":
        return bool(run_input.get("directUrls"))
    return bool(run_input.get("startUrls"))
