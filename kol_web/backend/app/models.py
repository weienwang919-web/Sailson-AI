from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class KOLRecord(Base):
    __tablename__ = "kol_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(255), index=True, default="")
    normalized_category: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    main_tag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_gender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audience_gender_pct: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audience_region: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_age: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_links: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    tt_link: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    tt_follower: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tt_avv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tt_short_video_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    tt_anchor_link_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    ins_link: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    ins_follower: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ins_post_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ins_reels_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    yt_link: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    yt_follower: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yt_avv: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yt_full_video_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    yt_live_2hr_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    yt_pre_roll_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    yt_short_video_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    avg_engagement: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra_fields: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SheetTemplate(Base):
    __tablename__ = "sheet_templates"
    __table_args__ = (UniqueConstraint("category", name="uq_sheet_template_category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(255), index=True)
    source_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ImportLog(Base):
    __tablename__ = "import_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(512))
    added: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    crawler_items: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    crawler_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    crawler_cost_cny: Mapped[float] = mapped_column(Float, default=0)
    total_cost_cny: Mapped[float] = mapped_column(Float, default=0)
    usage_detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )



class OfficialAccount(Base):
    __tablename__ = "official_accounts"
    __table_args__ = (UniqueConstraint("business_id", name="uq_official_account_business_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    business_id: Mapped[str] = mapped_column(String(128), index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_image: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_deep_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_business_account: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    following_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    followers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    videos_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class OfficialVideoSnapshot(Base):
    __tablename__ = "official_video_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "item_id", name="uq_official_video_account_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("official_accounts.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(128), index=True)
    item_id: Mapped[str] = mapped_column(String(128), index=True)
    media_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_ad: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    share_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    embed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    create_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    video_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favorites: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_time_watched: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_time_watched: Mapped[float | None] = mapped_column(Float, nullable=True)
    full_video_watched_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    website_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phone_number_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_submissions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    app_download_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    address_clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_likes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_view_retention_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    impression_sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_genders_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_countries_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_cities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_types_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    log_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class OfficialProfileDailyMetric(Base):
    __tablename__ = "official_profile_daily_metrics"
    __table_args__ = (UniqueConstraint("account_id", "metric_date", name="uq_official_profile_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("official_accounts.id"), index=True)
    business_id: Mapped[str] = mapped_column(String(128), index=True)
    metric_date: Mapped[date] = mapped_column(Date, index=True)
    followers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unique_video_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_total_followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_new_followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_lost_followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engaged_audience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audience_activity_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_ages_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_genders_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_countries_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience_cities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OfficialMonitorJob(Base):
    __tablename__ = "official_monitor_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    log_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    crawler_items: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    crawler_cost_usd: Mapped[float] = mapped_column(Float, default=0)
    crawler_cost_cny: Mapped[float] = mapped_column(Float, default=0)
    total_cost_cny: Mapped[float] = mapped_column(Float, default=0)
    usage_detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
