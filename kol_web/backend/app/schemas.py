from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class KOLRecordOut(BaseModel):
    id: int
    name: str
    category: str
    normalized_category: str | None = None
    source_file: str | None = None
    country: str | None = None
    language: str | None = None
    platform_text: str | None = None
    notes: str | None = None
    content_tags: str | None = None
    recommendation: str | None = None
    case_links: str | None = None

    tt_link: str | None = None
    tt_follower: int | None = None
    tt_avv: int | None = None
    tt_short_video_price: float | None = None
    tt_anchor_link_price: float | None = None

    ins_link: str | None = None
    ins_follower: int | None = None
    ins_post_price: float | None = None
    ins_reels_price: float | None = None

    yt_link: str | None = None
    yt_follower: int | None = None
    yt_avv: int | None = None
    yt_full_video_price: float | None = None
    yt_live_2hr_price: float | None = None
    yt_pre_roll_price: float | None = None
    yt_short_video_price: float | None = None

    avg_engagement: float | None = None
    extra_fields: dict[str, Any] = {}
    last_scraped_at: datetime | None = None
    updated_at: datetime

class KOLListResponse(BaseModel):
    total: int
    items: list[KOLRecordOut]


class KOLUpdate(BaseModel):
    values: dict[str, Any]


class KOLIdsRequest(BaseModel):
    ids: list[int]


class FilterRule(BaseModel):
    field: str
    op: str
    value: Any = None


class FilterPayload(BaseModel):
    logic: str = "and"
    rules: list[FilterRule] = []


class ExportRequest(BaseModel):
    ids: list[int] | None = None
    filters: FilterPayload | None = None
    update_metrics: bool = False
    source_file: str | None = None


class ScrapeRequest(BaseModel):
    ids: list[int] | None = None


class LinkImportRequest(BaseModel):
    text: str
    scrape: bool = False


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    total: int
    done: int
    error: str | None = None
    item_count: int = 0
    crawler_items: int = 0
    api_calls: int = 0
    crawler_cost_usd: float = 0
    crawler_cost_cny: float = 0
    total_cost_cny: float = 0
    created_at: datetime
    updated_at: datetime


class ImportResponse(BaseModel):
    added: int
    updated: int
    skipped: int
    ids: list[int] = []
    filename: str | None = None
    job: JobOut | None = None


class LinkImportResponse(ImportResponse):
    ids: list[int]
    job: JobOut | None = None



class OfficialAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    business_id: str
    username: str | None = None
    display_name: str | None = None
    profile_image: str | None = None
    profile_deep_link: str | None = None
    bio_description: str | None = None
    is_business_account: bool | None = None
    is_verified: bool | None = None
    following_count: int | None = None
    followers_count: int | None = None
    total_likes: int | None = None
    videos_count: int | None = None
    enabled: bool
    notes: str | None = None
    last_refreshed_at: datetime | None = None
    updated_at: datetime


class OfficialVideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    business_id: str
    item_id: str
    media_type: str | None = None
    is_ad: bool | None = None
    thumbnail_url: str | None = None
    share_url: str | None = None
    embed_url: str | None = None
    caption: str | None = None
    create_time: datetime | None = None
    video_duration: float | None = None
    reach: int | None = None
    video_views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    favorites: int | None = None
    total_time_watched: float | None = None
    average_time_watched: float | None = None
    full_video_watched_rate: float | None = None
    new_followers: int | None = None
    profile_views: int | None = None
    engagement_likes: list[dict[str, Any]] = []
    video_view_retention: list[dict[str, Any]] = []
    impression_sources: list[dict[str, Any]] = []
    audience_countries: list[dict[str, Any]] = []
    request_id: str | None = None
    log_id: str | None = None
    fetched_at: datetime


class OfficialVideoListResponse(BaseModel):
    total: int
    items: list[OfficialVideoOut]


class OfficialProfileMetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    business_id: str
    metric_date: date
    followers_count: int | None = None
    video_views: int | None = None
    unique_video_views: int | None = None
    profile_views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    daily_total_followers: int | None = None
    daily_new_followers: int | None = None
    daily_lost_followers: int | None = None
    engaged_audience: int | None = None


class OfficialRefreshRequest(BaseModel):
    account_ids: list[int] | None = None
    days: int = 30


class OfficialExportRequest(BaseModel):
    account_ids: list[int] | None = None


class OfficialJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    total: int
    done: int
    error: str | None = None
    request_id: str | None = None
    log_id: str | None = None
    item_count: int = 0
    crawler_items: int = 0
    api_calls: int = 0
    crawler_cost_usd: float = 0
    crawler_cost_cny: float = 0
    total_cost_cny: float = 0
    created_at: datetime
    updated_at: datetime
