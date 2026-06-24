from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'kol.db'}")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_usage_columns()
    ensure_kol_record_columns()
    ensure_data_refresh_columns()


def ensure_usage_columns() -> None:
    columns = {
        "item_count": "INTEGER DEFAULT 0",
        "crawler_items": "INTEGER DEFAULT 0",
        "api_calls": "INTEGER DEFAULT 0",
        "crawler_cost_usd": "FLOAT DEFAULT 0",
        "crawler_cost_cny": "FLOAT DEFAULT 0",
        "total_cost_cny": "FLOAT DEFAULT 0",
        "usage_detail_json": "TEXT",
    }
    with engine.begin() as conn:
        for table in ("scrape_jobs",):
            existing = set()
            try:
                if DATABASE_URL.startswith("sqlite"):
                    rows = conn.execute(text(f"PRAGMA table_info({table})")).mappings().all()
                    existing = {row["name"] for row in rows}
                else:
                    rows = conn.execute(
                        text("SELECT column_name FROM information_schema.columns WHERE table_name = :table"),
                        {"table": table},
                    ).mappings().all()
                    existing = {row["column_name"] for row in rows}
                for name, ddl in columns.items():
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            except Exception:
                # create_all handles fresh DBs; best-effort ALTER keeps older local DBs compatible.
                pass


def _existing_columns(conn, table: str) -> set[str]:
    if DATABASE_URL.startswith("sqlite"):
        rows = conn.execute(text(f"PRAGMA table_info({table})")).mappings().all()
        return {row["name"] for row in rows}
    rows = conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :table"),
        {"table": table},
    ).mappings().all()
    return {row["column_name"] for row in rows}


def ensure_kol_record_columns() -> None:
    columns = {
        "tt_acv": "INTEGER",
        "ins_acv": "INTEGER",
        "yt_acv": "INTEGER",
    }
    with engine.begin() as conn:
        try:
            existing = _existing_columns(conn, "kol_records")
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE kol_records ADD COLUMN {name} {ddl}"))
        except Exception:
            pass


def ensure_data_refresh_columns() -> None:
    columns = {
        "input_type": "VARCHAR(50) DEFAULT 'links'",
        "success_count": "INTEGER DEFAULT 0",
        "failed_count": "INTEGER DEFAULT 0",
        "added_count": "INTEGER DEFAULT 0",
        "updated_count": "INTEGER DEFAULT 0",
        "sync_to_pool": "INTEGER DEFAULT 0",
        "include_acv": "INTEGER DEFAULT 1",
        "videos_per_profile": "INTEGER DEFAULT 10",
        "summary_json": "TEXT",
        "output_path": "TEXT",
        "output_filename": "VARCHAR(512)",
    }
    with engine.begin() as conn:
        try:
            existing = _existing_columns(conn, "data_refresh_jobs")
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE data_refresh_jobs ADD COLUMN {name} {ddl}"))
        except Exception:
            pass
