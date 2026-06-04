from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
    extra_fields: dict[str, Any] = Field(default_factory=dict)
    last_scraped_at: datetime | None = None
    updated_at: datetime


class KOLListResponse(BaseModel):
    total: int
    items: list[KOLRecordOut]


class KOLCreate(BaseModel):
    name: str
    category: str = ""
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


FilterNode = FilterRule | FilterGroup


class FilterPayload(BaseModel):
    logic: Literal["and", "or"] = "and"
    rules: list[FilterRule] = Field(default_factory=list)
    children: list[FilterNode] = Field(default_factory=list)


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
    ids: list[int] = Field(default_factory=list)
    filename: str | None = None
    job: JobOut | None = None


class LinkImportResponse(ImportResponse):
    ids: list[int]
    job: JobOut | None = None
