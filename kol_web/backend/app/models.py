from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
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
