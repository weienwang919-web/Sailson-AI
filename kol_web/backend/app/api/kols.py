from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from app.core.category import STANDARD_CATEGORIES, normalize_category
from app.database import get_db
from app.models import KOLRecord, ScrapeJob
from app.schemas import (
    ExportRequest,
    FilterPayload,
    ImportResponse,
    JobOut,
    KOLIdsRequest,
    KOLListResponse,
    KOLRecordOut,
    KOLUpdate,
    LinkImportRequest,
    LinkImportResponse,
    ScrapeRequest,
)
from app.services.export_service import export_records_restored, export_source_workbook_updated
from app.services.import_service import import_workbook
from app.services.link_import_service import import_links
from app.services.scrape_service import create_scrape_job, run_scrape_job

router = APIRouter(prefix="/api", tags=["kols"])

EDITABLE_FIELDS = {
    col.name
    for col in KOLRecord.__table__.columns
    if col.name not in {"id", "created_at", "updated_at", "last_scraped_at"}
}
CORE_FIELD_KEYS = {
    "KOL",
    "Name",
    "Category",
    "类目",
    "平台",
    "➡️ 平台",
    "渠道名",
    "链接",
    "➡️ LINK",
    "粉丝数\n(自然数)",
    "➡️ Followers",
    "均观看量\n(自然数)",
    "➡️ Avg View",
}
BUSINESS_EXTRA_SUFFIXES = {"合作模式", "主报价", "CPM", "直播报价", "授权报价"}


@router.get("/kols", response_model=KOLListResponse)
def list_kols(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: str | None = None,
    filters: str | None = None,
) -> KOLListResponse:
    query = db.query(KOLRecord)
    if search:
        like = f"%{search}%"
        query = query.filter(
            KOLRecord.name.ilike(like)
            | KOLRecord.category.ilike(like)
            | KOLRecord.normalized_category.ilike(like)
            | KOLRecord.platform_text.ilike(like)
            | KOLRecord.source_file.ilike(like)
            | KOLRecord.notes.ilike(like)
            | KOLRecord.country.ilike(like)
            | KOLRecord.language.ilike(like)
            | KOLRecord.content_tags.ilike(like)
            | KOLRecord.main_tag.ilike(like)
            | KOLRecord.channel_content.ilike(like)
            | KOLRecord.recommendation.ilike(like)
            | KOLRecord.case_links.ilike(like)
            | KOLRecord.email.ilike(like)
            | KOLRecord.tt_link.ilike(like)
            | KOLRecord.ins_link.ilike(like)
            | KOLRecord.yt_link.ilike(like)
            | KOLRecord.extra_fields.ilike(like)
            | KOLRecord.raw_json.ilike(like)
        )
    if filters:
        query = apply_filters(query, FilterPayload.model_validate(json.loads(filters)))
    total = query.count()
    items = (
        query.order_by(desc(KOLRecord.updated_at), desc(KOLRecord.created_at), desc(KOLRecord.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return KOLListResponse(total=total, items=[serialize_kol(x) for x in items])


@router.get("/kols/categories")
def categories(db: Session = Depends(get_db)) -> list[str]:
    rows = db.query(KOLRecord.category).group_by(KOLRecord.category).order_by(KOLRecord.category).all()
    return [x[0] for x in rows if x[0]]


@router.post("/kols/by-ids", response_model=list[KOLRecordOut])
def kols_by_ids(payload: KOLIdsRequest, db: Session = Depends(get_db)) -> list[KOLRecordOut]:
    if not payload.ids:
        return []
    order = {kol_id: idx for idx, kol_id in enumerate(payload.ids)}
    records = db.query(KOLRecord).filter(KOLRecord.id.in_(payload.ids)).all()
    records.sort(key=lambda record: order.get(record.id, len(order)))
    return [serialize_kol(record) for record in records]


@router.patch("/kols/{kol_id}", response_model=KOLRecordOut)
def update_kol(kol_id: int, payload: KOLUpdate, db: Session = Depends(get_db)) -> KOLRecordOut:
    record = db.get(KOLRecord, kol_id)
    if not record:
        raise HTTPException(status_code=404, detail="KOL not found")
    for field, value in payload.values.items():
        if field.startswith("extra:"):
            extra_key = field.split(":", 1)[1]
            extra = json.loads(record.extra_fields or "{}")
            extra[extra_key] = value
            record.extra_fields = json.dumps(extra, ensure_ascii=False)
            continue
        if field not in EDITABLE_FIELDS:
            raise HTTPException(status_code=400, detail=f"Field is not editable: {field}")
        setattr(record, field, value)
    db.commit()
    db.refresh(record)
    return serialize_kol(record)


@router.post("/kols/import", response_model=ImportResponse)
async def import_kols(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    scrape: bool = Query(False),
    db: Session = Depends(get_db),
) -> ImportResponse:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx is supported")
    tmp = Path(tempfile.gettempdir()) / file.filename
    tmp.write_bytes(await file.read())
    result = import_workbook(db, tmp, file.filename)
    job_out: JobOut | None = None
    if scrape and result.get("ids"):
        job = create_scrape_job(db, result["ids"])
        videos_per_profile = int(os.getenv("VIDEOS_PER_PROFILE", "10"))
        background_tasks.add_task(run_scrape_job, job.id, result["ids"], videos_per_profile)
        db.refresh(job)
        job_out = JobOut.model_validate(job)
    return ImportResponse(**result, job=job_out)


@router.post("/kols/import-links", response_model=LinkImportResponse)
def import_kol_links(
    payload: LinkImportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> LinkImportResponse:
    result = import_links(db, payload.text)
    job_out: JobOut | None = None
    if payload.scrape and result["ids"]:
        job = create_scrape_job(db, result["ids"])
        videos_per_profile = int(os.getenv("VIDEOS_PER_PROFILE", "10"))
        background_tasks.add_task(run_scrape_job, job.id, result["ids"], videos_per_profile)
        db.refresh(job)
        job_out = JobOut.model_validate(job)
    return LinkImportResponse(**result, job=job_out)


@router.post("/kols/scrape", response_model=JobOut)
def scrape_kols(
    payload: ScrapeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> JobOut:
    job = create_scrape_job(db, payload.ids)
    videos_per_profile = int(os.getenv("VIDEOS_PER_PROFILE", "10"))
    background_tasks.add_task(run_scrape_job, job.id, payload.ids, videos_per_profile)
    db.refresh(job)
    return JobOut.model_validate(job)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobOut:
    job = db.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut.model_validate(job)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=100)) -> list[JobOut]:
    jobs = db.query(ScrapeJob).order_by(desc(ScrapeJob.created_at)).limit(limit).all()
    return [JobOut.model_validate(job) for job in jobs]


@router.post("/kols/export")
def export_kols(payload: ExportRequest, db: Session = Depends(get_db)) -> FileResponse:
    query = db.query(KOLRecord)
    if payload.ids:
        query = query.filter(KOLRecord.id.in_(payload.ids))
    elif payload.filters:
        query = apply_filters(query, payload.filters)
    records = query.order_by(KOLRecord.category, KOLRecord.name).all()
    if payload.update_metrics:
        job = create_scrape_job(db, [r.id for r in records])
        run_scrape_job(job.id, [r.id for r in records], int(os.getenv("VIDEOS_PER_PROFILE", "10")))
        records = query.order_by(KOLRecord.category, KOLRecord.name).all()
    if payload.source_file:
        out = export_source_workbook_updated(payload.source_file, records)
        filename = f"{Path(payload.source_file).stem}_metrics_updated.xlsx"
    else:
        out = export_records_restored(db, records)
        filename = "kol_export_restored.xlsx"
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


def apply_filters(query, payload: FilterPayload):
    clauses = []
    for rule in payload.rules:
        if rule.field.startswith("extra:"):
            extra_key = rule.field.split(":", 1)[1]
            col = func.json_extract(KOLRecord.extra_fields, f'$."{extra_key}"')
        elif hasattr(KOLRecord, rule.field):
            col = getattr(KOLRecord, rule.field)
        else:
            continue
        op = rule.op
        value = rule.value
        if op == "eq":
            clauses.append(col == value)
        elif op == "neq":
            clauses.append(col != value)
        elif op == "contains":
            clauses.append(col.ilike(f"%{value}%"))
        elif op == "gte":
            clauses.append(col >= value)
        elif op == "lte":
            clauses.append(col <= value)
        elif op == "between" and isinstance(value, list) and len(value) == 2:
            clauses.append(col.between(value[0], value[1]))
        elif op == "is_empty":
            clauses.append((col.is_(None)) | (col == ""))
        elif op == "is_not_empty":
            clauses.append((col.is_not(None)) & (col != ""))
    if not clauses:
        return query
    if payload.logic.lower() == "or":
        from sqlalchemy import or_

        return query.filter(or_(*clauses))
    return query.filter(and_(*clauses))


@router.get("/kols/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    total = db.query(func.count(KOLRecord.id)).scalar() or 0
    with_tt = db.query(func.count(KOLRecord.id)).filter(KOLRecord.tt_link.is_not(None)).scalar() or 0
    with_ins = db.query(func.count(KOLRecord.id)).filter(KOLRecord.ins_link.is_not(None)).scalar() or 0
    with_yt = db.query(func.count(KOLRecord.id)).filter(KOLRecord.yt_link.is_not(None)).scalar() or 0
    return {"total": total, "tiktok": with_tt, "instagram": with_ins, "youtube": with_yt}


def serialize_kol(record: KOLRecord) -> KOLRecordOut:
    data = {
        "id": record.id,
        "name": record.name,
        "category": record.category,
        "normalized_category": record.normalized_category or normalize_category(record.category),
        "source_file": record.source_file,
        "country": record.country,
        "language": record.language,
        "platform_text": record.platform_text,
        "notes": record.notes,
        "content_tags": record.content_tags,
        "recommendation": record.recommendation,
        "case_links": record.case_links,
        "tt_link": record.tt_link,
        "tt_follower": record.tt_follower,
        "tt_avv": record.tt_avv,
        "tt_short_video_price": record.tt_short_video_price,
        "tt_anchor_link_price": record.tt_anchor_link_price,
        "ins_link": record.ins_link,
        "ins_follower": record.ins_follower,
        "ins_post_price": record.ins_post_price,
        "ins_reels_price": record.ins_reels_price,
        "yt_link": record.yt_link,
        "yt_follower": record.yt_follower,
        "yt_avv": record.yt_avv,
        "yt_full_video_price": record.yt_full_video_price,
        "yt_live_2hr_price": record.yt_live_2hr_price,
        "yt_pre_roll_price": record.yt_pre_roll_price,
        "yt_short_video_price": record.yt_short_video_price,
        "avg_engagement": record.avg_engagement,
        "last_scraped_at": record.last_scraped_at,
        "updated_at": record.updated_at,
        "extra_fields": {},
    }
    if record.extra_fields:
        try:
            data["extra_fields"] = json.loads(record.extra_fields)
        except json.JSONDecodeError:
            data["extra_fields"] = {}
    return KOLRecordOut(**data)


@router.get("/kols/fields")
def fields(db: Session = Depends(get_db)) -> dict[str, Any]:
    counts: dict[str, int] = {}
    rows = db.query(KOLRecord.extra_fields).filter(KOLRecord.extra_fields.is_not(None)).all()
    for (raw,) in rows:
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for key, value in obj.items():
            if key in CORE_FIELD_KEYS or _is_business_extra_key(key):
                continue
            if value not in (None, ""):
                counts[key] = counts.get(key, 0) + 1
    return {
        "core": [
            {"key": "name", "label": "KOL / Name"},
            {"key": "normalized_category", "label": "Standard Category / 标准类目"},
            {"key": "category", "label": "Category / 类目"},
            {"key": "source_file", "label": "Source / 来源"},
            {"key": "country", "label": "Country / 国家"},
            {"key": "language", "label": "Language / 语言"},
            {"key": "platform_text", "label": "Platform / 平台"},
            {"key": "tt_follower", "label": "TT Follower"},
            {"key": "tt_avv", "label": "TT AVV"},
            {"key": "ins_follower", "label": "INS Follower"},
            {"key": "yt_follower", "label": "YT Follower"},
            {"key": "yt_avv", "label": "YT AVV"},
        ],
        "extra": [
            {"key": key, "label": key, "count": count}
            for key, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        ],
    }


@router.get("/filters/options")
def filter_options(db: Session = Depends(get_db)) -> dict[str, list[str]]:
    options: dict[str, list[str]] = {"normalized_category": STANDARD_CATEGORIES}
    for field in ("category", "source_file", "country", "language", "platform_text"):
        col = getattr(KOLRecord, field)
        rows = db.query(col).filter(col.is_not(None), col != "").group_by(col).order_by(col).limit(200).all()
        options[field] = [str(row[0]) for row in rows if row[0]]

    extra_values: dict[str, dict[str, int]] = {}
    rows = db.query(KOLRecord.extra_fields).filter(KOLRecord.extra_fields.is_not(None)).all()
    for (raw,) in rows:
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for key, value in obj.items():
            if key in CORE_FIELD_KEYS or _is_business_extra_key(key) or value in (None, ""):
                continue
            text = str(value)
            if len(text) > 80:
                continue
            bucket = extra_values.setdefault(f"extra:{key}", {})
            bucket[text] = bucket.get(text, 0) + 1
    for field, values in extra_values.items():
        popular = sorted(values.items(), key=lambda x: (-x[1], x[0]))[:80]
        options[field] = [value for value, _count in popular]
    return options


def _is_business_extra_key(key: str) -> bool:
    return any(key.endswith(f" - {suffix}") for suffix in BUSINESS_EXTRA_SUFFIXES)
