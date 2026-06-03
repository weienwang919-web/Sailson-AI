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
