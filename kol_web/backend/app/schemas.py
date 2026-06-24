from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class KOLRecordOut(BaseModel):
    id: int
    name: str
    category: str
    normalized_category: Optional[str] = None
    major_category: Optional[str] = None
    source_file: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
    platform_text: Optional[str] = None
    notes: Optional[str] = None
    content_tags: Optional[str] = None
    recommendation: Optional[str] = None
    case_links: Optional[str] = None

    tt_link: Optional[str] = None
    tt_follower: Optional[int] = None
    tt_avv: Optional[int] = None
    tt_acv: Optional[int] = None
    tt_short_video_price: Optional[float] = None
    tt_anchor_link_price: Optional[float] = None

    ins_link: Optional[str] = None
    ins_follower: Optional[int] = None
    ins_acv: Optional[int] = None
    ins_post_price: Optional[float] = None
    ins_reels_price: Optional[float] = None

    yt_link: Optional[str] = None
    yt_follower: Optional[int] = None
    yt_avv: Optional[int] = None
    yt_acv: Optional[int] = None
    yt_full_video_price: Optional[float] = None
    yt_live_2hr_price: Optional[float] = None
    yt_pre_roll_price: Optional[float] = None
    yt_short_video_price: Optional[float] = None

    avg_engagement: Optional[float] = None
    audience_gender: Optional[str] = None
    audience_gender_pct: Optional[str] = None
    audience_region: Optional[str] = None
    audience_age: Optional[str] = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)
    last_scraped_at: Optional[datetime] = None
    updated_at: datetime


class KOLListResponse(BaseModel):
    total: int
    items: list[KOLRecordOut]


class KOLCreate(BaseModel):
    name: str
    category: str = ""
    normalized_category: Optional[str] = None
    source_file: Optional[str] = None
    country: Optional[str] = None
    language: Optional[str] = None
    platform_text: Optional[str] = None
    notes: Optional[str] = None
    content_tags: Optional[str] = None
    recommendation: Optional[str] = None
    case_links: Optional[str] = None
    tt_link: Optional[str] = None
    tt_follower: Optional[int] = None
    tt_avv: Optional[int] = None
    tt_acv: Optional[int] = None
    tt_short_video_price: Optional[float] = None
    tt_anchor_link_price: Optional[float] = None
    ins_link: Optional[str] = None
    ins_follower: Optional[int] = None
    ins_acv: Optional[int] = None
    ins_post_price: Optional[float] = None
    ins_reels_price: Optional[float] = None
    yt_link: Optional[str] = None
    yt_follower: Optional[int] = None
    yt_avv: Optional[int] = None
    yt_acv: Optional[int] = None
    yt_full_video_price: Optional[float] = None
    yt_live_2hr_price: Optional[float] = None
    yt_pre_roll_price: Optional[float] = None
    yt_short_video_price: Optional[float] = None
    avg_engagement: Optional[float] = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class KOLUpdate(BaseModel):
    values: dict[str, Any]


class KOLIdsRequest(BaseModel):
    ids: list[int]


class FilterRule(BaseModel):
    field: str
    op: str
    value: Any = None


class FilterGroup(BaseModel):
    logic: Literal["and", "or"] = "and"
    children: list[Any] = Field(default_factory=list)


FilterNode = Union[FilterRule, FilterGroup]


class FilterPayload(BaseModel):
    logic: Literal["and", "or"] = "and"
    rules: list[FilterRule] = Field(default_factory=list)
    children: list[FilterNode] = Field(default_factory=list)


class ExportRequest(BaseModel):
    ids: Optional[list[int]] = None
    filters: Optional[FilterPayload] = None
    update_metrics: bool = False
    source_file: Optional[str] = None
    has_price: Optional[bool] = None


class ScrapeRequest(BaseModel):
    ids: Optional[list[int]] = None


class LinkImportRequest(BaseModel):
    text: str
    scrape: bool = False


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    total: int
    done: int
    error: Optional[str] = None
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
    ids: list[int] = Field(default_factory=list)
    filename: Optional[str] = None
    job: Optional[JobOut] = None


class LinkImportResponse(ImportResponse):
    ids: list[int]
    job: Optional[JobOut] = None


class DataRefreshJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    input_type: str
    status: str
    total: int
    success_count: int = 0
    failed_count: int = 0
    added_count: int = 0
    updated_count: int = 0
    sync_to_pool: int = 0
    include_acv: int = 1
    videos_per_profile: int = 10
    error: Optional[str] = None
    summary_json: Optional[str] = None
    output_filename: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DataRefreshLinkRequest(BaseModel):
    text: str
    sync_to_pool: bool = False
    videos_per_profile: int = 10
    include_acv: bool = True
