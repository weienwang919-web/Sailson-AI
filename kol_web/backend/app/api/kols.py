from __future__ import annotations

import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, desc, func, or_
from sqlalchemy.orm import Session

from app.core.business_fields import BUSINESS_FIELDS, field_payload, fields_for_usage
from app.core.category import STANDARD_CATEGORIES, major_category, normalize_category
from app.core.field_normalizer import STANDARD_PLATFORM_FIELDS
from app.database import get_db
from app.models import KOLRecord, ScrapeJob
from app.schemas import (
    ExportRequest,
    FilterPayload,
    FilterRule,
    ImportResponse,
    JobOut,
    KOLCreate,
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
CREATABLE_FIELDS = EDITABLE_FIELDS - {"raw_json"}
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
TEXT_SEARCH_FIELDS = (
    "name",
    "category",
    "normalized_category",
    "platform_text",
    "source_file",
    "notes",
    "country",
    "language",
    "content_tags",
    "main_tag",
    "channel_content",
    "recommendation",
    "case_links",
    "email",
    "tt_link",
    "ins_link",
    "yt_link",
    "extra_fields",
)


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
        query = query.filter(or_(*(getattr(KOLRecord, field).ilike(like) for field in TEXT_SEARCH_FIELDS)))
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


@router.post("/kols", response_model=KOLRecordOut)
def create_kol(payload: KOLCreate, db: Session = Depends(get_db)) -> KOLRecordOut:
    data = payload.model_dump(exclude_unset=True)
    extra_fields = data.pop("extra_fields", {}) or {}
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="KOL name is required")
    if not data.get("normalized_category") and data.get("category"):
        data["normalized_category"] = normalize_category(data["category"])
    record = KOLRecord(**{key: value for key, value in data.items() if key in CREATABLE_FIELDS})
    if extra_fields:
        record.extra_fields = json.dumps(extra_fields, ensure_ascii=False)
    db.add(record)
    db.commit()
    db.refresh(record)
    return serialize_kol(record)


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
    if not record.normalized_category and record.category:
        record.normalized_category = normalize_category(record.category)
    db.commit()
    db.refresh(record)
    return serialize_kol(record)


@router.delete("/kols/{kol_id}")
def delete_kol(kol_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    record = db.get(KOLRecord, kol_id)
    if not record:
        raise HTTPException(status_code=404, detail="KOL not found")
    db.delete(record)
    db.commit()
    return {"deleted": True, "id": kol_id}


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
    clause = build_filter_clause(payload)
    return query.filter(clause) if clause is not None else query


def build_filter_clause(node: Any):
    if isinstance(node, FilterPayload):
        children = node.children or node.rules
        return combine_filter_clauses(node.logic, [build_filter_clause(child) for child in children])
    if isinstance(node, FilterRule):
        return build_rule_clause(node)
    if isinstance(node, dict):
        if "children" in node:
            return combine_filter_clauses(
                str(node.get("logic", "and")),
                [build_filter_clause(child) for child in node.get("children", [])],
            )
        if "rules" in node:
            return combine_filter_clauses(
                str(node.get("logic", "and")),
                [build_filter_clause(child) for child in node.get("rules", [])],
            )
        if "field" in node:
            return build_rule_clause(FilterRule.model_validate(node))
    return None


def combine_filter_clauses(logic: str, clauses: list[Any]):
    valid = [clause for clause in clauses if clause is not None]
    if not valid:
        return None
    if logic.lower() == "or":
        return or_(*valid)
    return and_(*valid)


def build_rule_clause(rule: FilterRule):
    col = column_for_filter_field(rule.field)
    if col is None:
        return None
    op = rule.op
    value = rule.value
    if rule.field in ("normalized_category", "major_category") and op in ("eq", "contains"):
        expanded = _expand_major_category(value)
        if expanded:
            return col.in_(expanded)
    if op == "eq":
        return col == value
    if op == "neq":
        return col != value
    if op == "contains":
        return col.ilike(f"%{value}%")
    if op == "gte":
        return col >= value
    if op == "lte":
        return col <= value
    if op == "between" and isinstance(value, list) and len(value) == 2:
        return col.between(value[0], value[1])
    if op == "is_empty":
        return (col.is_(None)) | (col == "")
    if op == "is_not_empty":
        return (col.is_not(None)) & (col != "")
    return None


def _expand_major_category(value: str) -> list[str] | None:
    from app.core.category import MAJOR_CATEGORY_MAP

    reverse: dict[str, list[str]] = {}
    for std, major in MAJOR_CATEGORY_MAP.items():
        reverse.setdefault(major, []).append(std)
    if value in reverse:
        return reverse[value]
    return None


def column_for_filter_field(field: str):
    if field == "major_category":
        return getattr(KOLRecord, "normalized_category")
    if field.startswith("extra:"):
        extra_key = field.split(":", 1)[1]
        return func.json_extract(KOLRecord.extra_fields, f'$."{extra_key}"')
    if hasattr(KOLRecord, field):
        return getattr(KOLRecord, field)
    return None


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
        "major_category": major_category(record.normalized_category or normalize_category(record.category)),
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


@router.get("/kols/business-fields")
def business_fields() -> dict[str, Any]:
    return {
        "list": [field_payload(field) for field in fields_for_usage("list")],
        "filter": [field_payload(field) for field in fields_for_usage("filter")],
        "detail": [field_payload(field) for field in fields_for_usage("detail")],
        "export": [field_payload(field) for field in fields_for_usage("export")],
        "create": [field_payload(field) for field in fields_for_usage("create")],
        "update": [field_payload(field) for field in fields_for_usage("update")],
    }


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
        "core": [{"key": field.filter_key, "label": field.label} for field in fields_for_usage("filter")],
        "extra": [
            {"key": key, "label": key, "count": count}
            for key, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        ],
    }


@router.get("/kols/field-inventory")
def field_inventory(db: Session = Depends(get_db), limit: int = Query(300, ge=1, le=1000)) -> dict[str, Any]:
    field_stats: dict[str, dict[str, Any]] = {}
    rows = db.query(KOLRecord.source_file, KOLRecord.raw_json, KOLRecord.extra_fields).all()
    for source_file, raw_json, extra_fields in rows:
        for origin, raw in (("raw_json", raw_json), ("extra_fields", extra_fields)):
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            for key, value in obj.items():
                text = "" if value is None else str(value).strip()
                if not key or not text:
                    continue
                normalized = normalize_field_name(key)
                bucket = field_stats.setdefault(
                    key,
                    {
                        "raw_field": key,
                        "normalized_field": normalized,
                        "count": 0,
                        "sources": set(),
                        "origins": set(),
                        "sample_values": [],
                        "suggested_standard_field": suggest_standard_field(key),
                        "platform": guess_platform(key),
                    },
                )
                bucket["count"] += 1
                if source_file:
                    bucket["sources"].add(source_file)
                bucket["origins"].add(origin)
                if len(bucket["sample_values"]) < 5 and text not in bucket["sample_values"]:
                    bucket["sample_values"].append(text[:160])
    items = sorted(field_stats.values(), key=lambda x: (-x["count"], x["raw_field"]))[:limit]
    for item in items:
        item["sources"] = sorted(item["sources"])[:10]
        item["origins"] = sorted(item["origins"])
    grouped: dict[str, int] = defaultdict(int)
    for item in items:
        grouped[item["suggested_standard_field"] or "未归类"] += item["count"]
    return {"items": items, "summary": dict(sorted(grouped.items(), key=lambda x: (-x[1], x[0])))}


@router.get("/kols/field-alias-rules")
def field_alias_rules() -> dict[str, Any]:
    return {
        "standard_business_fields": [field_payload(field) for field in BUSINESS_FIELDS],
        "platform_alias_rules": [
            {
                "standard_field": standard,
                "label": field.label,
                "aliases": list(field.aliases),
            }
            for standard, field in STANDARD_PLATFORM_FIELDS.items()
        ],
    }


@router.get("/filters/options")
def filter_options(db: Session = Depends(get_db)) -> dict[str, list[str]]:
    options: dict[str, list[str]] = {
        "normalized_category": STANDARD_CATEGORIES,
        "major_category": STANDARD_CATEGORIES,
    }
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
            if key in CORE_FIELD_KEYS or value in (None, ""):
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


def normalize_field_name(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[\s\n\r\t_\-]+", " ", text)
    return text.strip()


def guess_platform(value: str) -> str | None:
    text = value.lower()
    if "tiktok" in text or text.startswith("tt"):
        return "TikTok"
    if "instagram" in text or "ins" in text or "ig" in text:
        return "Instagram"
    if "youtube" in text or text.startswith("yt"):
        return "YouTube"
    return None


def suggest_standard_field(value: str) -> str | None:
    text = normalize_field_name(value)
    for field in BUSINESS_FIELDS:
        if field.extra_key and normalize_field_name(field.extra_key) == text:
            return field.label
        if normalize_field_name(field.label) == text:
            return field.label
    for standard, field in STANDARD_PLATFORM_FIELDS.items():
        aliases = [standard, field.label, *field.aliases]
        if any(normalize_field_name(alias) == text for alias in aliases):
            return field.label
    if any(token in text for token in ("creator", "kol", "name", "渠道名", "资源名称")):
        return "KOL 名称"
    if any(token in text for token in ("link", "url", "链接", "channel")):
        return "平台链接"
    if any(token in text for token in ("follower", "粉丝")):
        return "粉丝数"
    if any(token in text for token in ("avg view", "avv", "ccv", "观看")):
        return "AVV"
    if any(token in text for token in ("price", "报价", "cpm")):
        return "报价/CPM"
    if any(token in text for token in ("audience", "受众", "gender", "age", "年龄", "性别")):
        return "受众字段"
    if any(token in text for token in ("feedback", "状态", "推进", "进展")):
        return "跟进字段"
    return None


def _is_business_extra_key(key: str) -> bool:
    return any(key.endswith(f" - {suffix}") for suffix in BUSINESS_EXTRA_SUFFIXES)
