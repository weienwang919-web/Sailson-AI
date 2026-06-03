from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import OfficialAccount, OfficialMonitorJob, OfficialVideoSnapshot
from app.schemas import (
    OfficialAccountOut,
    OfficialJobOut,
    OfficialProfileMetricOut,
    OfficialRefreshRequest,
    OfficialVideoListResponse,
    OfficialVideoOut,
)
from app.services.official_monitor_service import (
    export_official_videos,
    list_profile_metrics,
    list_videos,
    load_configured_accounts,
    refresh_official_accounts,
)

router = APIRouter(prefix="/api/official", tags=["official"])


@router.get("/accounts", response_model=list[OfficialAccountOut])
def official_accounts(db: Session = Depends(get_db)) -> list[OfficialAccountOut]:
    accounts = load_configured_accounts(db)
    return [OfficialAccountOut.model_validate(account) for account in accounts]


@router.post("/refresh", response_model=OfficialJobOut)
def refresh(payload: OfficialRefreshRequest, db: Session = Depends(get_db)) -> OfficialJobOut:
    job = refresh_official_accounts(db, payload.account_ids, payload.days)
    return OfficialJobOut.model_validate(job)


@router.get("/jobs", response_model=list[OfficialJobOut])
def jobs(db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=100)) -> list[OfficialJobOut]:
    rows = db.query(OfficialMonitorJob).order_by(desc(OfficialMonitorJob.created_at)).limit(limit).all()
    return [OfficialJobOut.model_validate(row) for row in rows]


@router.get("/videos", response_model=OfficialVideoListResponse)
def videos(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    account_id: int | None = None,
) -> OfficialVideoListResponse:
    total, rows = list_videos(db, page, page_size, account_id)
    return OfficialVideoListResponse(total=total, items=[serialize_video(row) for row in rows])


@router.get("/videos/{item_id}", response_model=OfficialVideoOut)
def video_detail(item_id: str, db: Session = Depends(get_db)) -> OfficialVideoOut:
    row = db.query(OfficialVideoSnapshot).filter(OfficialVideoSnapshot.item_id == item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Video not found")
    return serialize_video(row)


@router.get("/profile-metrics", response_model=list[OfficialProfileMetricOut])
def profile_metrics(db: Session = Depends(get_db), account_id: int | None = None) -> list[OfficialProfileMetricOut]:
    rows = list_profile_metrics(db, account_id)
    return [OfficialProfileMetricOut.model_validate(row) for row in rows]


@router.post("/export")
def export(db: Session = Depends(get_db)) -> FileResponse:
    out = export_official_videos(db)
    return FileResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="official_tiktok_monitor_export.xlsx",
    )


def serialize_video(row: OfficialVideoSnapshot) -> OfficialVideoOut:
    data: dict[str, Any] = {
        "id": row.id,
        "account_id": row.account_id,
        "business_id": row.business_id,
        "item_id": row.item_id,
        "media_type": row.media_type,
        "is_ad": row.is_ad,
        "thumbnail_url": row.thumbnail_url,
        "share_url": row.share_url,
        "embed_url": row.embed_url,
        "caption": row.caption,
        "create_time": row.create_time,
        "video_duration": row.video_duration,
        "reach": row.reach,
        "video_views": row.video_views,
        "likes": row.likes,
        "comments": row.comments,
        "shares": row.shares,
        "favorites": row.favorites,
        "total_time_watched": row.total_time_watched,
        "average_time_watched": row.average_time_watched,
        "full_video_watched_rate": row.full_video_watched_rate,
        "new_followers": row.new_followers,
        "profile_views": row.profile_views,
        "engagement_likes": _json_list(row.engagement_likes_json),
        "video_view_retention": _json_list(row.video_view_retention_json),
        "impression_sources": _json_list(row.impression_sources_json),
        "audience_countries": _json_list(row.audience_countries_json),
        "request_id": row.request_id,
        "log_id": row.log_id,
        "fetched_at": row.fetched_at,
    }
    return OfficialVideoOut(**data)


def _json_list(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
