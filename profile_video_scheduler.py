from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Optional

import requests

import database as db
import tasks
import usage_service
import video_metrics_etl

logger = logging.getLogger(__name__)

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
DEFAULT_SYNC_HOUR = int(os.environ.get("PROFILE_VIDEO_SYNC_HOUR", "9"))
DEFAULT_MAX_VIDEOS_PER_PROFILE = int(os.environ.get("PROFILE_VIDEO_MAX_VIDEOS", "50"))
DEFAULT_FEISHU_MAX_PROFILES_PER_RUN = int(os.environ.get("FEISHU_PROFILE_VIDEO_MAX_PROFILES_PER_RUN", "5"))
DEFAULT_PROFILE_VIDEO_HARD_MAX_VIDEOS = int(os.environ.get("PROFILE_VIDEO_HARD_MAX_VIDEOS_PER_PROFILE", "50"))
try:
    from zoneinfo import ZoneInfo

    FEISHU_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - very old Python fallback
    FEISHU_TZ = None

FEISHU_FIELD_MAP = {
    "video_key": "视频唯一键",
    "profile_url": "主页链接",
    "platform": "平台",
    "video_url": "视频链接",
    "author": "作者",
    "post_date": "发布日期",
    "caption": "视频文案",
    "duration": "视频时长",
    "views": "播放量",
    "likes": "点赞量",
    "comments": "评论量",
    "shares": "转发量",
    "collects": "收藏量",
    "engagement": "互动量",
    "followers": "主页粉丝数",
    "last_synced_at": "最后同步时间",
}

FEISHU_CONFIG_FIELDS = {
    "platform": "平台",
    "schedule_hour": "抓取小时",
    "owner": "负责人",
    "recent_days": "近N天",
    "notes": "备注",
    "region": "国家/地区",
    "profile_url": "达人主页链接",
    "project": "项目/品牌",
    "display_name": "达人名称",
    "category": "垂类",
    "last_status": "最后抓取状态",
    "max_videos": "最大视频数",
    "enabled": "是否启用",
    "sync_scope": "抓取范围",
    "last_run_at": "最后抓取时间",
    "config_key": "配置唯一键",
    "creator_key": "达人唯一键",
    "last_error": "失败原因",
}

FEISHU_LATEST_FIELDS = {
    "video_key": "视频唯一键",
    "creator_key": "达人唯一键",
    "platform": "平台",
    "profile_url": "达人主页链接",
    "author": "达人名称",
    "video_id": "视频ID",
    "video_url": "视频链接",
    "post_date": "发布时间",
    "first_seen_date": "首次发现日期",
    "caption": "视频文案/标题",
    "video_type": "视频类型",
    "duration_seconds": "时长秒",
    "views": "播放量",
    "likes": "点赞量",
    "comments": "评论量",
    "shares": "分享量",
    "collects": "收藏量",
    "engagement": "互动量",
    "followers": "主页粉丝数",
    "engagement_rate": "互动率",
    "views_delta_1d": "近1日新增播放",
    "views_delta_7d": "近7日新增播放",
    "post_age_days": "视频发布天数",
    "hashtags": "Hashtag",
    "last_synced_at": "最后同步时间",
    "is_new_video": "是否新视频",
    "status": "抓取状态",
    "error": "失败原因",
}

FEISHU_SNAPSHOT_FIELDS = {
    "snapshot_key": "快照唯一键",
    "video_key": "视频唯一键",
    "video_url": "视频链接",
    "creator_key": "达人唯一键",
    "platform": "平台",
    "project": "项目/品牌",
    "snapshot_date": "抓取日期",
    "synced_at": "同步时间",
    "task_id": "抓取任务ID",
    "post_date": "发布时间",
    "post_age_days": "视频发布天数",
    "views": "播放量",
    "likes": "点赞量",
    "comments": "评论量",
    "shares": "分享量",
    "collects": "收藏量",
    "engagement": "互动量",
    "engagement_rate": "互动率",
    "daily_views_delta": "日增播放",
    "daily_engagement_delta": "日增互动",
}

FEISHU_LOG_FIELDS = {
    "task_id": "任务ID",
    "run_date": "执行日期",
    "trigger_type": "触发方式",
    "status": "状态",
    "started_at": "开始时间",
    "finished_at": "结束时间",
    "profile_count": "达人主页数",
    "success_profile_count": "成功主页数",
    "failed_profile_count": "失败主页数",
    "video_count": "抓到视频数",
    "created_count": "新增视频数",
    "updated_count": "更新视频数",
    "snapshot_count": "快照写入数",
    "error": "失败原因",
}

PLATFORM_LABELS = {
    "TT": "TikTok",
    "IG": "Instagram",
    "YTB": "YouTube",
}

FEISHU_VIDEO_ENV_KEYS = (
    "FEISHU_VIDEO_BASE_TOKEN",
    "FEISHU_VIDEO_CONFIG_TABLE_ID",
    "FEISHU_VIDEO_LATEST_TABLE_ID",
    "FEISHU_VIDEO_SNAPSHOT_TABLE_ID",
    "FEISHU_VIDEO_LOG_TABLE_ID",
)


class FeishuRecordNotFound(RuntimeError):
    pass


def ensure_schema() -> None:
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS video_profile_configs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                profile_url TEXT NOT NULL,
                platform VARCHAR(16),
                display_name VARCHAR(255),
                enabled BOOLEAN DEFAULT TRUE,
                sync_scope VARCHAR(16) DEFAULT 'recent',
                start_date DATE,
                end_date DATE,
                max_videos INTEGER DEFAULT 50,
                schedule_hour INTEGER DEFAULT 9,
                feishu_app_token TEXT,
                feishu_table_id TEXT,
                notes TEXT,
                last_task_id VARCHAR(128),
                last_run_at TIMESTAMP,
                last_success_at TIMESTAMP,
                last_error TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        db.execute("ALTER TABLE video_profile_configs DROP CONSTRAINT IF EXISTS video_profile_configs_profile_url_key")
        db.execute("CREATE INDEX IF NOT EXISTS idx_video_profile_configs_enabled ON video_profile_configs (enabled)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_video_profile_configs_user_id ON video_profile_configs (user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_video_profile_configs_schedule ON video_profile_configs (enabled, schedule_hour)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_video_profile_configs_user_url ON video_profile_configs (user_id, profile_url)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS video_profile_runs (
                id SERIAL PRIMARY KEY,
                task_id VARCHAR(128) UNIQUE NOT NULL,
                user_id INTEGER,
                trigger_type VARCHAR(32),
                profile_count INTEGER DEFAULT 0,
                video_count INTEGER DEFAULT 0,
                created_count INTEGER DEFAULT 0,
                updated_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status VARCHAR(32) DEFAULT 'running',
                message TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                finished_at TIMESTAMP
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS video_profile_video_state (
                id SERIAL PRIMARY KEY,
                video_key VARCHAR(256) NOT NULL,
                feishu_app_token TEXT,
                feishu_table_id TEXT,
                profile_url TEXT,
                platform VARCHAR(16),
                video_url TEXT,
                feishu_record_id VARCHAR(128),
                last_metrics_json TEXT,
                last_synced_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        db.execute("ALTER TABLE video_profile_video_state ADD COLUMN IF NOT EXISTS feishu_app_token TEXT")
        db.execute("ALTER TABLE video_profile_video_state ADD COLUMN IF NOT EXISTS feishu_table_id TEXT")
        db.execute("ALTER TABLE video_profile_video_state DROP CONSTRAINT IF EXISTS video_profile_video_state_video_key_key")
        db.execute("CREATE INDEX IF NOT EXISTS idx_video_profile_video_state_profile ON video_profile_video_state (profile_url)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_video_profile_video_state_feishu ON video_profile_video_state (feishu_app_token, feishu_table_id)")
        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_video_profile_video_state_target_key
            ON video_profile_video_state (feishu_app_token, feishu_table_id, video_key)
            """
        )
        logger.info("✅ 已确认主页视频定时同步表存在")
    except Exception as exc:
        logger.warning("⚠️ 无法创建主页视频定时同步表: %s", exc)


def list_configs(user_id: Optional[int] = None) -> list[dict]:
    where = []
    params = []
    if user_id is not None:
        where.append("(user_id = %s OR user_id IS NULL)")
        params.append(user_id)
    sql = """
        SELECT id, user_id, profile_url, platform, display_name, enabled, sync_scope,
               start_date, end_date, max_videos, schedule_hour, feishu_app_token,
               feishu_table_id, notes, last_task_id, last_run_at, last_success_at,
               last_error, created_at, updated_at
        FROM video_profile_configs
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY enabled DESC, updated_at DESC, id DESC"
    return [dict(r) for r in db.query_all(sql, tuple(params)) or []]


def upsert_configs(
    profile_urls: Iterable[str],
    *,
    user_id: Optional[int],
    enabled: bool = True,
    sync_scope: str = "recent",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_videos: Optional[int] = None,
    schedule_hour: Optional[int] = None,
    feishu_app_token: Optional[str] = None,
    feishu_table_id: Optional[str] = None,
) -> dict:
    urls = []
    seen = set()
    for raw in profile_urls:
        url = video_metrics_etl.normalize_url(str(raw or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)

    inserted = 0
    updated = 0
    ids = []
    for url in urls:
        platform = video_metrics_etl.detect_platform(url)
        row = db.execute_and_fetch_one(
            """
            INSERT INTO video_profile_configs (
                user_id, profile_url, platform, enabled, sync_scope, start_date, end_date,
                max_videos, schedule_hour, feishu_app_token, feishu_table_id, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (user_id, profile_url) DO UPDATE SET
                platform = EXCLUDED.platform,
                enabled = EXCLUDED.enabled,
                sync_scope = EXCLUDED.sync_scope,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                max_videos = EXCLUDED.max_videos,
                schedule_hour = EXCLUDED.schedule_hour,
                feishu_app_token = COALESCE(EXCLUDED.feishu_app_token, video_profile_configs.feishu_app_token),
                feishu_table_id = COALESCE(EXCLUDED.feishu_table_id, video_profile_configs.feishu_table_id),
                updated_at = NOW()
            RETURNING id, (xmax = 0) AS inserted
            """,
            (
                user_id,
                url,
                platform,
                bool(enabled),
                _clean_scope(sync_scope),
                _clean_date(start_date),
                _clean_date(end_date),
                _clean_max_videos(max_videos),
                _clean_hour(schedule_hour),
                (feishu_app_token or "").strip() or None,
                (feishu_table_id or "").strip() or None,
            ),
        )
        if row:
            ids.append(row["id"])
            if row.get("inserted"):
                inserted += 1
            else:
                updated += 1
    return {"ids": ids, "inserted": inserted, "updated": updated, "total": len(urls)}


def update_config(config_id: int, user_id: Optional[int], patch: dict) -> Optional[dict]:
    allowed = {
        "enabled": bool,
        "sync_scope": str,
        "start_date": str,
        "end_date": str,
        "max_videos": int,
        "schedule_hour": int,
        "feishu_app_token": str,
        "feishu_table_id": str,
        "notes": str,
        "display_name": str,
    }
    updates = []
    params = []
    for key, caster in allowed.items():
        if key not in patch:
            continue
        value = patch.get(key)
        if key == "sync_scope":
            value = _clean_scope(value)
        elif key in {"start_date", "end_date"}:
            value = _clean_date(value)
        elif key == "max_videos":
            value = _clean_max_videos(value)
        elif key == "schedule_hour":
            value = _clean_hour(value)
        elif key in {"feishu_app_token", "feishu_table_id", "notes", "display_name"}:
            value = str(value or "").strip() or None
        elif key == "enabled":
            value = bool(value)
        updates.append(f"{key} = %s")
        params.append(value)

    if not updates:
        return get_config(config_id, user_id)
    updates.append("updated_at = NOW()")
    params.extend([config_id, user_id])
    row = db.execute_and_fetch_one(
        f"""
        UPDATE video_profile_configs
        SET {', '.join(updates)}
        WHERE id = %s AND (user_id = %s OR user_id IS NULL)
        RETURNING *
        """,
        tuple(params),
    )
    return dict(row) if row else None


def get_config(config_id: int, user_id: Optional[int] = None) -> Optional[dict]:
    row = db.query_one(
        """
        SELECT *
        FROM video_profile_configs
        WHERE id = %s AND (%s IS NULL OR user_id = %s OR user_id IS NULL)
        """,
        (config_id, user_id, user_id),
    )
    return dict(row) if row else None


def due_configs_for_hour(hour: Optional[int] = None) -> list[dict]:
    h = _clean_hour(default_sync_hour() if hour is None else hour)
    return [dict(r) for r in db.query_all(
        """
        SELECT *
        FROM video_profile_configs
        WHERE enabled = TRUE
          AND COALESCE(schedule_hour, %s) = %s
          AND (last_run_at IS NULL OR last_run_at::date < CURRENT_DATE)
        ORDER BY id ASC
        """,
        (default_sync_hour(), h),
    ) or []]


def mark_task_for_configs(task_id: str, configs: Iterable[dict]) -> None:
    ids = [int(c["id"]) for c in configs if c.get("id")]
    if not ids:
        return
    db.execute(
        """
        UPDATE video_profile_configs
        SET last_task_id = %s, last_run_at = NOW(), updated_at = NOW()
        WHERE id = ANY(%s)
        """,
        (task_id, ids),
    )


def enqueue_due_profile_video_sync(
    create_task_fn,
    *,
    update_task_params_fn,
    hour: Optional[int] = None,
    after_enqueue_fn: Optional[Callable[[str, dict], None]] = None,
) -> list[str]:
    if not profile_video_sync_enabled():
        logger.info("主页视频同步未启用，跳过定时入队")
        return []
    configs = due_configs_for_hour(hour)
    by_user: dict[Optional[int], list[dict]] = defaultdict(list)
    for cfg in configs:
        by_user[cfg.get("user_id")].append(cfg)

    task_ids = []
    for user_id, rows in by_user.items():
        task_id = str(uuid.uuid4())
        session_id = f"profile_video_schedule_{date.today().isoformat()}"
        create_task_fn(task_id, user_id, session_id, function_type="profile_video_sync")
        params = {
            "source": "profile_video_sync",
            "trigger_type": "scheduled",
            "config_ids": [r["id"] for r in rows],
            "user_id": user_id,
            "session_id": session_id,
        }
        update_task_params_fn(task_id, params)
        mark_task_for_configs(task_id, rows)
        if after_enqueue_fn:
            after_enqueue_fn(task_id, params)
        task_ids.append(task_id)
    return task_ids


def run_profile_video_sync_task(task_id: str, params: dict, update_task_fn: Callable[..., None]) -> None:
    if not profile_video_sync_enabled():
        update_task_fn(task_id, status="failed", error="主页视频同步未启用，请配置 PROFILE_VIDEO_SYNC_ENABLED=true")
        return
    user_id = params.get("user_id")
    trigger_type = params.get("trigger_type") or "manual"
    config_ids = params.get("config_ids") or []
    profile_urls = params.get("profile_urls") or []
    inline_configs = params.get("inline_configs") or []

    configs = _load_task_configs(config_ids, profile_urls, user_id)
    if inline_configs:
        for raw_cfg in inline_configs:
            if not isinstance(raw_cfg, dict):
                continue
            profile_url = video_metrics_etl.normalize_url(str(raw_cfg.get("profile_url") or ""))
            if not profile_url:
                continue
            cfg = dict(raw_cfg)
            cfg["profile_url"] = profile_url
            cfg["platform"] = cfg.get("platform") or video_metrics_etl.detect_platform(profile_url)
            cfg["enabled"] = True
            cfg["user_id"] = user_id
            configs.append(cfg)
    if not configs:
        update_task_fn(task_id, status="failed", error="没有可同步的主页配置")
        return

    apify_token = tasks.APIFY_TOKEN
    if not apify_token:
        update_task_fn(task_id, status="failed", error="Apify 未配置")
        return

    missing_targets = _configs_missing_feishu_target(configs)
    if missing_targets:
        message = f"有 {len(missing_targets)} 个主页缺少飞书多维表格配置，请先配置默认表或在主页配置里填写 app token/table id"
        _mark_config_results(missing_targets, task_id, len(missing_targets), message)
        update_task_fn(task_id, status="failed", error=message)
        return

    db.execute(
        """
        INSERT INTO video_profile_runs (task_id, user_id, trigger_type, profile_count, status, message)
        VALUES (%s, %s, %s, %s, 'running', '开始同步')
        ON CONFLICT (task_id) DO UPDATE SET status = 'running', message = '重新开始同步'
        """,
        (task_id, user_id, trigger_type, len(configs)),
    )
    mark_task_for_configs(task_id, configs)

    all_rows = []
    failed_profiles = 0
    for idx, cfg in enumerate(configs, start=1):
        _raise_if_task_stopped(task_id)
        update_task_fn(task_id, progress=f"正在同步主页 {idx}/{len(configs)}")
        start_date, end_date = _date_window_for_config(cfg)
        rows = video_metrics_etl.fetch_profile_video_metrics(
            [cfg["profile_url"]],
            apify_token,
            start_date=start_date,
            end_date=end_date,
            max_videos=_clean_max_videos(cfg.get("max_videos") or default_max_videos_per_profile()),
            progress_hook=lambda msg: update_task_fn(task_id, progress=msg),
            should_abort=lambda: _task_stop_requested(task_id),
        )
        for row in rows:
            row["config_id"] = cfg.get("id")
            row["feishu_app_token"] = cfg.get("feishu_app_token")
            row["feishu_table_id"] = cfg.get("feishu_table_id")
            if row.get("_error"):
                failed_profiles += 1
        all_rows.extend(rows)

    _raise_if_task_stopped(task_id)
    update_task_fn(task_id, progress=f"抓到 {len(all_rows)} 条视频，正在写入飞书")
    created_count = 0
    updated_count = 0
    failed_count = failed_profiles
    grouped = defaultdict(list)
    for row in all_rows:
        if row.get("_error"):
            continue
        default_app_token, default_table_id = default_feishu_target()
        key = (
            row.get("feishu_app_token") or default_app_token,
            row.get("feishu_table_id") or default_table_id,
        )
        grouped[key].append(row)

    for (app_token, table_id), rows in grouped.items():
        if not app_token or not table_id:
            failed_count += len(rows)
            logger.warning("主页视频同步缺少飞书 app_token/table_id，跳过 %s 条", len(rows))
            continue
        try:
            result = sync_rows_to_feishu(rows, app_token=app_token, table_id=table_id)
            created_count += result.get("created", 0)
            updated_count += result.get("updated", 0)
        except Exception as exc:
            failed_count += len(rows)
            logger.error("写入飞书失败 app=%s table=%s: %s", app_token, table_id, exc)

    status = "completed" if failed_count == 0 else "completed"
    message = f"完成：视频 {len(all_rows)} 条，新增 {created_count}，更新 {updated_count}，失败/跳过 {failed_count}"
    db.execute(
        """
        UPDATE video_profile_runs
        SET video_count = %s, created_count = %s, updated_count = %s, failed_count = %s,
            status = %s, message = %s, finished_at = NOW()
        WHERE task_id = %s
        """,
        (len(all_rows), created_count, updated_count, failed_count, status, message, task_id),
    )
    _mark_config_results(configs, task_id, failed_count, message)
    usage_service.record_usage_event(
        module="profile_video_sync",
        user_id=user_id,
        task_id=task_id,
        item_count=len(all_rows),
        crawler_items=len(all_rows),
        source="actual",
        detail={
            "trigger_type": trigger_type,
            "profile_count": len(configs),
            "created_count": created_count,
            "updated_count": updated_count,
            "failed_count": failed_count,
        },
    )
    update_task_fn(
        task_id,
        status=status,
        progress=message,
        result=json.dumps(
            {
                "profile_count": len(configs),
                "video_count": len(all_rows),
                "created_count": created_count,
                "updated_count": updated_count,
                "failed_count": failed_count,
            },
            ensure_ascii=False,
        ),
    )


def feishu_video_table_config() -> dict:
    """Return the four-table Feishu target for daily homepage video automation."""
    return {
        "base_token": os.environ.get("FEISHU_VIDEO_BASE_TOKEN", "").strip(),
        "config_table_id": os.environ.get("FEISHU_VIDEO_CONFIG_TABLE_ID", "").strip(),
        "latest_table_id": os.environ.get("FEISHU_VIDEO_LATEST_TABLE_ID", "").strip(),
        "snapshot_table_id": os.environ.get("FEISHU_VIDEO_SNAPSHOT_TABLE_ID", "").strip(),
        "log_table_id": os.environ.get("FEISHU_VIDEO_LOG_TABLE_ID", "").strip(),
    }


def validate_feishu_video_table_config() -> tuple[bool, list[str]]:
    config = feishu_video_table_config()
    missing = [key for key in FEISHU_VIDEO_ENV_KEYS if not config.get(_env_key_to_config_key(key))]
    return (not missing, missing)


def profile_video_sync_enabled() -> bool:
    return os.environ.get("PROFILE_VIDEO_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def feishu_profile_video_sync_enabled() -> bool:
    return os.environ.get("FEISHU_PROFILE_VIDEO_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def enqueue_due_feishu_profile_video_sync(
    create_task_fn,
    *,
    update_task_params_fn,
    hour: Optional[int] = None,
    after_enqueue_fn: Optional[Callable[[str, dict], None]] = None,
) -> list[str]:
    if not feishu_profile_video_sync_enabled():
        logger.info("飞书主页视频四表同步未启用，跳过定时入队")
        return []
    ok, missing = validate_feishu_video_table_config()
    if not ok:
        logger.warning("飞书主页视频四表同步缺少环境变量: %s", ", ".join(missing))
        return []

    try:
        due_configs = load_due_feishu_profile_configs(hour=hour)
    except Exception as exc:
        logger.error("读取飞书主页配置失败: %s", exc)
        return []
    if not due_configs:
        return []

    session_id = f"feishu_profile_video_schedule_{date.today().isoformat()}"
    max_profiles = _feishu_max_profiles_per_run()
    task_ids = []
    for batch in _chunks(due_configs, max_profiles):
        task_id = str(uuid.uuid4())
        create_task_fn(task_id, None, session_id, function_type="feishu_profile_video_sync")
        params = {
            "source": "feishu_profile_video_sync",
            "trigger_type": "scheduled",
            "session_id": session_id,
            "config_record_ids": [cfg["record_id"] for cfg in batch if cfg.get("record_id")],
            "config_count": len(batch),
            "max_profiles_per_run": max_profiles,
        }
        update_task_params_fn(task_id, params)
        mark_feishu_configs_started(batch, task_id=task_id)
        if after_enqueue_fn:
            after_enqueue_fn(task_id, params)
        task_ids.append(task_id)
    return task_ids


def load_due_feishu_profile_configs(hour: Optional[int] = None) -> list[dict]:
    h = _clean_hour(default_sync_hour() if hour is None else hour)
    configs = load_feishu_profile_configs(enabled_only=True)
    today = _today_text()
    due = []
    for cfg in configs:
        cfg_hour = _clean_hour(cfg.get("schedule_hour") if cfg.get("schedule_hour") is not None else default_sync_hour())
        if cfg_hour != h:
            continue
        last_run_day = _date_text(cfg.get("last_run_at"))
        if last_run_day and last_run_day >= today:
            continue
        due.append(cfg)
    return due


def load_feishu_profile_configs(*, enabled_only: bool = True, record_ids: Optional[list[str]] = None) -> list[dict]:
    table_config = feishu_video_table_config()
    app_token = table_config["base_token"]
    table_id = table_config["config_table_id"]
    if not app_token or not table_id:
        raise RuntimeError("FEISHU_VIDEO_BASE_TOKEN / FEISHU_VIDEO_CONFIG_TABLE_ID 未配置")
    client = FeishuBitableClient()
    records = client.list_records(app_token, table_id)
    wanted = set(str(x) for x in (record_ids or []) if x)
    configs = []
    for item in records:
        if wanted and item.get("record_id") not in wanted:
            continue
        cfg = _feishu_config_from_record(item)
        if enabled_only and not cfg.get("enabled"):
            continue
        if cfg.get("profile_url"):
            configs.append(cfg)
    return configs


def mark_feishu_configs_started(configs: Iterable[dict], *, task_id: str) -> None:
    _update_feishu_config_records(
        configs,
        lambda _cfg: {
            FEISHU_CONFIG_FIELDS["last_status"]: "未开始",
            FEISHU_CONFIG_FIELDS["last_error"]: "",
            FEISHU_CONFIG_FIELDS["last_run_at"]: _feishu_datetime_value(datetime.now(FEISHU_TZ) if FEISHU_TZ else datetime.now()),
        },
    )


def run_feishu_profile_video_sync_task(task_id: str, params: dict, update_task_fn: Callable[..., None]) -> None:
    """Read creator homepage configs from Feishu and write latest/snapshot/log tables."""
    if not feishu_profile_video_sync_enabled():
        update_task_fn(task_id, status="failed", error="飞书主页视频同步未启用，请配置 FEISHU_PROFILE_VIDEO_SYNC_ENABLED=true")
        return
    table_config = feishu_video_table_config()
    ok, missing = validate_feishu_video_table_config()
    if not ok:
        update_task_fn(task_id, status="failed", error=f"缺少环境变量: {', '.join(missing)}")
        return
    if not tasks.APIFY_TOKEN:
        update_task_fn(task_id, status="failed", error="Apify 未配置")
        return

    trigger_type = params.get("trigger_type") or "manual"
    started_at = datetime.now(FEISHU_TZ) if FEISHU_TZ else datetime.now()
    log_record_id = ""
    client = FeishuBitableClient()
    try:
        configs = _configs_for_feishu_task(params)
        if not configs:
            update_task_fn(task_id, status="failed", error="没有可同步的飞书主页配置")
            return
        max_profiles = _feishu_max_profiles_per_run(params)
        if len(configs) > max_profiles:
            update_task_fn(
                task_id,
                status="failed",
                error=f"本次识别到 {len(configs)} 个主页，超过安全上限 {max_profiles}。请分批执行或调整 FEISHU_PROFILE_VIDEO_MAX_PROFILES_PER_RUN。",
            )
            return
        update_task_fn(task_id, status="processing", progress=f"读取到 {len(configs)} 个启用主页，开始抓取")
        log_record_id = upsert_feishu_sync_log(
            client,
            table_config,
            {
                "task_id": task_id,
                "run_date": _today_text(),
                "trigger_type": _trigger_label(trigger_type),
                "status": "运行中",
                "started_at": started_at,
                "profile_count": len(configs),
            },
        )

        rows: list[dict] = []
        profile_results: list[dict] = []
        for idx, cfg in enumerate(configs, start=1):
            _raise_if_task_stopped(task_id)
            update_task_fn(task_id, progress=f"正在抓取主页 {idx}/{len(configs)}: {cfg.get('profile_url')}")
            start_date, end_date = _date_window_for_feishu_config(cfg)
            try:
                fetched = video_metrics_etl.fetch_profile_video_metrics(
                    [cfg["profile_url"]],
                    tasks.APIFY_TOKEN,
                    start_date=start_date,
                    end_date=end_date,
                    max_videos=_clean_max_videos(cfg.get("max_videos") or default_max_videos_per_profile()),
                    progress_hook=lambda msg: update_task_fn(task_id, progress=msg),
                    should_abort=lambda: _task_stop_requested(task_id),
                )
            except Exception as exc:
                if _task_stop_requested(task_id):
                    raise
                fetched = [{
                    "profile_url": cfg["profile_url"],
                    "platform": cfg.get("platform") or video_metrics_etl.detect_platform(cfg["profile_url"]),
                    "video_url": "",
                    "video_key": f"{cfg.get('platform') or 'UNKNOWN'}:{cfg['profile_url']}",
                    "_error": str(exc)[:300],
                }]
            ok_rows = [r for r in fetched if isinstance(r, dict) and not r.get("_error")]
            err_rows = [r for r in fetched if isinstance(r, dict) and r.get("_error")]
            error_text = "; ".join(str(r.get("_error")) for r in err_rows if r.get("_error"))[:500]
            status = "成功" if ok_rows and not err_rows else ("部分成功" if ok_rows else "失败")
            profile_results.append({
                "record_id": cfg.get("record_id"),
                "status": status,
                "error": error_text,
                "video_count": len(ok_rows),
            })
            for row in ok_rows:
                enriched = dict(row)
                enriched["config_record_id"] = cfg.get("record_id")
                enriched["config_key"] = cfg.get("config_key")
                enriched["creator_key"] = cfg.get("creator_key") or _creator_key_for(enriched.get("platform"), cfg.get("profile_url"))
                enriched["project"] = cfg.get("project")
                enriched["profile_url"] = cfg.get("profile_url") or enriched.get("profile_url")
                enriched["platform"] = enriched.get("platform") or cfg.get("platform")
                rows.append(enriched)

        _raise_if_task_stopped(task_id)
        update_task_fn(task_id, progress=f"抓到 {len(rows)} 条视频，正在写入飞书最新表和快照表")
        sync_result = sync_rows_to_feishu_video_tables(rows, task_id=task_id, table_config=table_config, client=client)
        success_profiles = sum(1 for item in profile_results if item["status"] in {"成功", "部分成功"})
        failed_profiles = sum(1 for item in profile_results if item["status"] == "失败")
        finished_at = datetime.now(FEISHU_TZ) if FEISHU_TZ else datetime.now()
        status_label = "成功" if failed_profiles == 0 else ("部分成功" if success_profiles else "失败")
        message = (
            f"完成：主页 {len(configs)}，成功 {success_profiles}，失败 {failed_profiles}，"
            f"视频 {len(rows)}，最新表新增 {sync_result.get('latest_created', 0)}，"
            f"更新 {sync_result.get('latest_updated', 0)}，快照 {sync_result.get('snapshot_written', 0)}"
        )
        _write_feishu_config_results(profile_results)
        upsert_feishu_sync_log(
            client,
            table_config,
            {
                "task_id": task_id,
                "run_date": _today_text(),
                "trigger_type": _trigger_label(trigger_type),
                "status": status_label,
                "started_at": started_at,
                "finished_at": finished_at,
                "profile_count": len(configs),
                "success_profile_count": success_profiles,
                "failed_profile_count": failed_profiles,
                "video_count": len(rows),
                "created_count": sync_result.get("latest_created", 0),
                "updated_count": sync_result.get("latest_updated", 0),
                "snapshot_count": sync_result.get("snapshot_written", 0),
                "error": _profile_error_summary(profile_results),
            },
            record_id=log_record_id,
        )
        usage_service.record_usage_event(
            module="feishu_profile_video_sync",
            user_id=params.get("user_id"),
            task_id=task_id,
            item_count=len(rows),
            crawler_items=len(rows),
            source="actual",
            detail={
                "trigger_type": trigger_type,
                "profile_count": len(configs),
                **sync_result,
            },
        )
        update_task_fn(
            task_id,
            status="completed" if success_profiles else "failed",
            progress=message,
            result=json.dumps(
                {
                    "profile_count": len(configs),
                    "success_profile_count": success_profiles,
                    "failed_profile_count": failed_profiles,
                    "video_count": len(rows),
                    **sync_result,
                    "base_token": table_config["base_token"],
                },
                ensure_ascii=False,
                default=str,
            ),
            error=None if success_profiles else _profile_error_summary(profile_results),
        )
    except Exception as exc:
        logger.error("飞书主页视频四表同步失败 task=%s: %s", task_id, exc)
        finished_at = datetime.now(FEISHU_TZ) if FEISHU_TZ else datetime.now()
        try:
            upsert_feishu_sync_log(
                client,
                table_config,
                {
                    "task_id": task_id,
                    "run_date": _today_text(),
                    "trigger_type": _trigger_label(trigger_type),
                    "status": "失败",
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "error": str(exc)[:500],
                },
                record_id=log_record_id,
            )
        except Exception as log_exc:
            logger.warning("写入飞书失败日志也失败: %s", log_exc)
        update_task_fn(task_id, status="failed", error=str(exc)[:500], progress="飞书主页视频同步失败")


def sync_rows_to_feishu_video_tables(
    rows: list[dict],
    *,
    task_id: str,
    table_config: dict,
    client: Optional["FeishuBitableClient"] = None,
) -> dict:
    client = client or FeishuBitableClient()
    now_dt = datetime.now(FEISHU_TZ) if FEISHU_TZ else datetime.now()
    snapshot_date = _today_text(now_dt)
    latest_existing = _existing_by_key(
        client,
        table_config["base_token"],
        table_config["latest_table_id"],
        FEISHU_LATEST_FIELDS["video_key"],
        [r.get("video_key") for r in rows],
    )
    snapshot_keys = [_snapshot_key(r.get("video_key"), snapshot_date) for r in rows if r.get("video_key")]
    snapshot_existing = _existing_by_key(
        client,
        table_config["base_token"],
        table_config["snapshot_table_id"],
        FEISHU_SNAPSHOT_FIELDS["snapshot_key"],
        snapshot_keys,
    )
    history_by_video = _snapshot_history_for_rows(
        rows,
        app_token=table_config["base_token"],
        snapshot_table_id=table_config["snapshot_table_id"],
        snapshot_date=snapshot_date,
    )

    latest_creates = []
    latest_updates = []
    snapshot_creates = []
    snapshot_updates = []
    for row in rows:
        if not row.get("video_key"):
            continue
        row = _normalize_video_table_row(row, now_dt=now_dt, is_new=row.get("video_key") not in latest_existing)
        history = history_by_video.get(row.get("video_key")) or {}
        _apply_video_growth_metrics(row, history)
        latest_fields = _row_to_named_fields(row, FEISHU_LATEST_FIELDS)
        existing_latest_id = latest_existing.get(row["video_key"])
        if existing_latest_id:
            latest_updates.append({"record_id": existing_latest_id, "fields": latest_fields, "source_row": row})
        else:
            latest_creates.append({"fields": latest_fields, "source_row": row})

        snapshot_row = dict(row)
        snapshot_row["snapshot_date"] = snapshot_date
        snapshot_row["synced_at"] = now_dt
        snapshot_row["task_id"] = task_id
        snapshot_row["snapshot_key"] = _snapshot_key(row.get("video_key"), snapshot_date)
        snapshot_fields = _row_to_named_fields(snapshot_row, FEISHU_SNAPSHOT_FIELDS)
        existing_snapshot_id = snapshot_existing.get(snapshot_row["snapshot_key"])
        if existing_snapshot_id:
            snapshot_updates.append({"record_id": existing_snapshot_id, "fields": snapshot_fields, "source_row": snapshot_row})
        else:
            snapshot_creates.append({"fields": snapshot_fields, "source_row": snapshot_row})

    created_latest_ids = _create_records_with_state(
        client,
        table_config["base_token"],
        table_config["latest_table_id"],
        latest_creates,
    )
    stale_latest_creates = []
    if latest_updates:
        stale_latest_creates = _safe_batch_update_records(
            client,
            table_config["base_token"],
            table_config["latest_table_id"],
            latest_updates,
        )
    stale_latest_ids = _create_records_with_state(
        client,
        table_config["base_token"],
        table_config["latest_table_id"],
        stale_latest_creates,
    )
    created_snapshot_ids = _create_records_with_state(
        client,
        table_config["base_token"],
        table_config["snapshot_table_id"],
        snapshot_creates,
        snapshot=True,
    )
    stale_snapshot_creates = []
    if snapshot_updates:
        stale_snapshot_creates = _safe_batch_update_records(
            client,
            table_config["base_token"],
            table_config["snapshot_table_id"],
            snapshot_updates,
        )
    stale_snapshot_ids = _create_records_with_state(
        client,
        table_config["base_token"],
        table_config["snapshot_table_id"],
        stale_snapshot_creates,
        snapshot=True,
    )
    return {
        "latest_created": len(created_latest_ids) + len(stale_latest_ids),
        "latest_updated": len(latest_updates) - len(stale_latest_creates),
        "snapshot_created": len(created_snapshot_ids) + len(stale_snapshot_ids),
        "snapshot_updated": len(snapshot_updates) - len(stale_snapshot_creates),
        "snapshot_written": len(created_snapshot_ids) + len(stale_snapshot_ids) + len(snapshot_updates) - len(stale_snapshot_creates),
    }


def upsert_feishu_sync_log(
    client: "FeishuBitableClient",
    table_config: dict,
    row: dict,
    *,
    record_id: str = "",
) -> str:
    fields = _row_to_named_fields(row, FEISHU_LOG_FIELDS)
    app_token = table_config["base_token"]
    table_id = table_config["log_table_id"]
    if record_id:
        client.batch_update_records(app_token, table_id, [{"record_id": record_id, "fields": fields}])
        return record_id
    existing = _existing_by_key(client, app_token, table_id, FEISHU_LOG_FIELDS["task_id"], [row.get("task_id")])
    found = existing.get(row.get("task_id"))
    if found:
        client.batch_update_records(app_token, table_id, [{"record_id": found, "fields": fields}])
        return found
    created = client.batch_create_records(app_token, table_id, [fields])
    return created[0] if created else ""


def sync_rows_to_feishu(rows: list[dict], *, app_token: str, table_id: str) -> dict:
    client = FeishuBitableClient()
    video_keys = [r["video_key"] for r in rows if r.get("video_key")]
    existing = _local_existing_records(video_keys, app_token, table_id)
    missing_keys = [k for k in video_keys if k not in existing]
    if missing_keys:
        existing.update(client.find_existing_records(app_token, table_id, missing_keys))
    creates = []
    updates = []
    now_text = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        row["last_synced_at"] = now_text
        fields = _row_to_feishu_fields(row)
        record_id = existing.get(row.get("video_key"))
        if record_id:
            updates.append({"record_id": record_id, "fields": fields, "source_row": row})
        else:
            creates.append({"fields": fields, "source_row": row})

    created_ids = client.batch_create_records(app_token, table_id, [x["fields"] for x in creates])
    for item, record_id in zip(creates, created_ids):
        _upsert_video_state(item["source_row"], record_id, app_token, table_id)

    if updates:
        client.batch_update_records(app_token, table_id, [{"record_id": x["record_id"], "fields": x["fields"]} for x in updates])
        for item in updates:
            _upsert_video_state(item["source_row"], item["record_id"], app_token, table_id)

    return {"created": len(created_ids), "updated": len(updates)}


class FeishuBitableClient:
    def __init__(self) -> None:
        self.app_id = os.environ.get("FEISHU_APP_ID") or os.environ.get("LARK_APP_ID") or ""
        self.app_secret = os.environ.get("FEISHU_APP_SECRET") or os.environ.get("LARK_APP_SECRET") or ""
        self._tenant_token = ""
        self._tenant_token_expire = 0.0

    def tenant_access_token(self) -> str:
        if self._tenant_token and self._tenant_token_expire > datetime.utcnow().timestamp() + 60:
            return self._tenant_token
        if not self.app_id or not self.app_secret:
            raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未配置")
        resp = requests.post(
            f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=20,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")
        self._tenant_token = data["tenant_access_token"]
        self._tenant_token_expire = datetime.utcnow().timestamp() + int(data.get("expire") or 7200)
        return self._tenant_token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.tenant_access_token()}", "Content-Type": "application/json; charset=utf-8"}

    def find_existing_records(self, app_token: str, table_id: str, video_keys: list[str]) -> dict[str, str]:
        return self.find_records_by_field(app_token, table_id, FEISHU_FIELD_MAP["video_key"], video_keys)

    def find_records_by_field(self, app_token: str, table_id: str, field_name: str, values: list) -> dict[str, str]:
        keys = [str(k) for k in dict.fromkeys(values) if k not in (None, "")]
        if not keys:
            return {}
        existing = {}
        for key in keys:
            page_token = ""
            while True:
                params = {"page_size": 20}
                if page_token:
                    params["page_token"] = page_token
                data = self._request_json(
                    "POST",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
                    params=params,
                    json_body={
                        "filter": {
                            "conjunction": "and",
                            "conditions": [
                                {
                                    "field_name": field_name,
                                    "operator": "is",
                                    "value": [key],
                                }
                            ],
                        }
                    },
                )
                for item in data.get("items") or []:
                    fields = item.get("fields") or {}
                    if _field_text(fields.get(field_name)) == key:
                        existing[key] = item.get("record_id")
                page_token = data.get("page_token") or ""
                if not data.get("has_more") or key in existing:
                    break
        return existing

    def list_records(self, app_token: str, table_id: str, *, page_size: int = 500) -> list[dict]:
        items = []
        page_token = ""
        while True:
            params = {"page_size": max(1, min(int(page_size or 500), 500))}
            if page_token:
                params["page_token"] = page_token
            data = self._request_json(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                params=params,
            )
            items.extend(data.get("items") or [])
            page_token = data.get("page_token") or ""
            if not data.get("has_more"):
                break
        return items

    def batch_create_records(self, app_token: str, table_id: str, fields_list: list[dict]) -> list[str]:
        record_ids = []
        for chunk in _chunks(fields_list, 500):
            if not chunk:
                continue
            data = self._request_json(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                json_body={"records": [{"fields": fields} for fields in chunk]},
            )
            for item in data.get("records") or []:
                record_ids.append(item.get("record_id") or "")
        return record_ids

    def batch_update_records(self, app_token: str, table_id: str, records: list[dict]) -> None:
        for chunk in _chunks(records, 500):
            if not chunk:
                continue
            self._request_json(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update",
                json_body={"records": chunk},
            )

    def _request_json(self, method: str, path: str, *, params: Optional[dict] = None, json_body: Optional[dict] = None) -> dict:
        resp = requests.request(
            method,
            f"{FEISHU_API_BASE}{path}",
            headers=self._headers(),
            params=params,
            json=json_body,
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"飞书接口返回非 JSON: HTTP {resp.status_code}") from exc
        if data.get("code") != 0:
            if data.get("code") == 1254043 or "record not found" in str(data).lower():
                raise FeishuRecordNotFound(f"飞书记录不存在: {data}")
            raise RuntimeError(f"飞书接口失败: {data}")
        return data.get("data") or {}


def _load_task_configs(config_ids: list, profile_urls: list, user_id: Optional[int]) -> list[dict]:
    configs = []
    if config_ids:
        if user_id is None:
            rows = db.query_all(
                """
                SELECT *
                FROM video_profile_configs
                WHERE id = ANY(%s) AND enabled = TRUE
                ORDER BY id ASC
                """,
                ([int(x) for x in config_ids],),
            )
        else:
            rows = db.query_all(
                """
                SELECT *
                FROM video_profile_configs
                WHERE id = ANY(%s)
                  AND enabled = TRUE
                  AND (user_id = %s OR user_id IS NULL)
                ORDER BY id ASC
                """,
                ([int(x) for x in config_ids], user_id),
            )
        configs.extend(dict(r) for r in rows or [])
    if profile_urls:
        result = upsert_configs(profile_urls, user_id=user_id, enabled=True)
        if result.get("ids"):
            rows = db.query_all(
                "SELECT * FROM video_profile_configs WHERE id = ANY(%s) ORDER BY id ASC",
                (result["ids"],),
            )
            configs.extend(dict(r) for r in rows or [])
    return configs


def _date_window_for_config(cfg: dict) -> tuple[Optional[str], Optional[str]]:
    scope = cfg.get("sync_scope") or "recent"
    if scope == "all":
        return None, None
    if scope == "range":
        start = cfg.get("start_date")
        end = cfg.get("end_date")
        return (
            start.isoformat() if hasattr(start, "isoformat") else (str(start)[:10] if start else None),
            end.isoformat() if hasattr(end, "isoformat") else (str(end)[:10] if end else None),
        )
    days = int(os.environ.get("PROFILE_VIDEO_RECENT_DAYS", "7"))
    return (date.today() - timedelta(days=max(days, 1))).isoformat(), None


def _row_to_feishu_fields(row: dict) -> dict:
    fields = {}
    for key, field_name in FEISHU_FIELD_MAP.items():
        value = row.get(key)
        if value in (None, ""):
            continue
        if key in {"views", "likes", "comments", "shares", "collects", "engagement", "followers"}:
            try:
                value = int(value)
            except Exception:
                pass
        fields[field_name] = value
    return fields


def _upsert_video_state(row: dict, record_id: str, app_token: str, table_id: str) -> None:
    if not row.get("video_key") or not record_id:
        return
    db.execute(
        """
        INSERT INTO video_profile_video_state (
            video_key, feishu_app_token, feishu_table_id, profile_url, platform, video_url, feishu_record_id,
            last_metrics_json, last_synced_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (feishu_app_token, feishu_table_id, video_key) DO UPDATE SET
            feishu_app_token = EXCLUDED.feishu_app_token,
            feishu_table_id = EXCLUDED.feishu_table_id,
            profile_url = EXCLUDED.profile_url,
            platform = EXCLUDED.platform,
            video_url = EXCLUDED.video_url,
            feishu_record_id = EXCLUDED.feishu_record_id,
            last_metrics_json = EXCLUDED.last_metrics_json,
            last_synced_at = NOW(),
            updated_at = NOW()
        """,
        (
            row.get("video_key"),
            app_token,
            table_id,
            row.get("profile_url"),
            row.get("platform"),
            row.get("video_url"),
            record_id,
            json.dumps(row, ensure_ascii=False, default=str),
        ),
    )


def _local_existing_records(video_keys: list[str], app_token: str, table_id: str) -> dict[str, str]:
    keys = [k for k in dict.fromkeys(video_keys) if k]
    if not keys:
        return {}
    rows = db.query_all(
        """
        SELECT video_key, feishu_record_id
        FROM video_profile_video_state
        WHERE video_key = ANY(%s)
          AND COALESCE(feishu_app_token, '') = %s
          AND COALESCE(feishu_table_id, '') = %s
          AND feishu_record_id IS NOT NULL
          AND feishu_record_id <> ''
        """,
        (keys, app_token, table_id),
    )
    return {r["video_key"]: r["feishu_record_id"] for r in rows or []}


def _create_records_with_state(
    client: "FeishuBitableClient",
    app_token: str,
    table_id: str,
    creates: list[dict],
    *,
    snapshot: bool = False,
) -> list[str]:
    created_ids = client.batch_create_records(app_token, table_id, [x["fields"] for x in creates])
    for item, record_id in zip(creates, created_ids):
        state_row = dict(item["source_row"])
        if snapshot:
            state_row["video_key"] = state_row.get("snapshot_key")
        _upsert_video_state(state_row, record_id, app_token, table_id)
    return created_ids


def _safe_batch_update_records(
    client: "FeishuBitableClient",
    app_token: str,
    table_id: str,
    updates: list[dict],
) -> list[dict]:
    stale_creates = []
    for item in updates:
        try:
            client.batch_update_records(
                app_token,
                table_id,
                [{"record_id": item["record_id"], "fields": item["fields"]}],
            )
            _upsert_video_state(item["source_row"], item["record_id"], app_token, table_id)
        except FeishuRecordNotFound:
            stale_creates.append({"fields": item["fields"], "source_row": item["source_row"]})
            logger.warning("飞书记录已不存在，改为新增 table=%s record=%s", table_id, item.get("record_id"))
    return stale_creates


def _configs_missing_feishu_target(configs: list[dict]) -> list[dict]:
    default_app_token, default_table_id = default_feishu_target()
    missing = []
    for cfg in configs:
        app_token = (cfg.get("feishu_app_token") or default_app_token or "").strip()
        table_id = (cfg.get("feishu_table_id") or default_table_id or "").strip()
        if not app_token or not table_id:
            missing.append(cfg)
    return missing


def _mark_config_results(configs: list[dict], task_id: str, failed_count: int, message: str) -> None:
    ids = [int(c["id"]) for c in configs if c.get("id")]
    if not ids:
        return
    if failed_count:
        db.execute(
            """
            UPDATE video_profile_configs
            SET last_task_id = %s, last_run_at = NOW(), last_error = %s, updated_at = NOW()
            WHERE id = ANY(%s)
            """,
            (task_id, message[:500], ids),
        )
    else:
        db.execute(
            """
            UPDATE video_profile_configs
            SET last_task_id = %s, last_run_at = NOW(), last_success_at = NOW(), last_error = NULL, updated_at = NOW()
            WHERE id = ANY(%s)
            """,
            (task_id, ids),
        )


def _env_key_to_config_key(env_key: str) -> str:
    return {
        "FEISHU_VIDEO_BASE_TOKEN": "base_token",
        "FEISHU_VIDEO_CONFIG_TABLE_ID": "config_table_id",
        "FEISHU_VIDEO_LATEST_TABLE_ID": "latest_table_id",
        "FEISHU_VIDEO_SNAPSHOT_TABLE_ID": "snapshot_table_id",
        "FEISHU_VIDEO_LOG_TABLE_ID": "log_table_id",
    }[env_key]


def _configs_for_feishu_task(params: dict) -> list[dict]:
    record_ids = params.get("config_record_ids") or []
    if record_ids:
        return load_feishu_profile_configs(enabled_only=True, record_ids=[str(x) for x in record_ids])
    profile_urls = params.get("profile_urls") or []
    if isinstance(profile_urls, str):
        profile_urls = [x.strip() for x in profile_urls.replace("\r", "").split("\n") if x.strip()]
    if profile_urls:
        configs = []
        for url in profile_urls:
            profile_url = video_metrics_etl.normalize_url(str(url or ""))
            if not profile_url:
                continue
            platform = video_metrics_etl.detect_platform(profile_url)
            configs.append({
                "profile_url": profile_url,
                "platform": platform,
                "enabled": True,
                "sync_scope": params.get("sync_scope") or "recent",
                "recent_days": params.get("recent_days"),
                "max_videos": params.get("max_videos") or default_max_videos_per_profile(),
                "creator_key": _creator_key_for(platform, profile_url),
                "project": params.get("project"),
            })
        return configs
    return load_feishu_profile_configs(enabled_only=True)


def _feishu_config_from_record(item: dict) -> dict:
    fields = item.get("fields") or {}
    profile_url = video_metrics_etl.normalize_url(_field_text(fields.get(FEISHU_CONFIG_FIELDS["profile_url"])))
    platform = _platform_code(_field_text(fields.get(FEISHU_CONFIG_FIELDS["platform"]))) or video_metrics_etl.detect_platform(profile_url)
    creator_key = _field_text(fields.get(FEISHU_CONFIG_FIELDS["creator_key"])) or _creator_key_for(platform, profile_url)
    config_key = _field_text(fields.get(FEISHU_CONFIG_FIELDS["config_key"])) or creator_key
    return {
        "record_id": item.get("record_id"),
        "profile_url": profile_url,
        "platform": platform,
        "display_name": _field_text(fields.get(FEISHU_CONFIG_FIELDS["display_name"])),
        "enabled": _field_bool(fields.get(FEISHU_CONFIG_FIELDS["enabled"])),
        "sync_scope": _sync_scope_code(_field_text(fields.get(FEISHU_CONFIG_FIELDS["sync_scope"]))),
        "recent_days": _field_int(fields.get(FEISHU_CONFIG_FIELDS["recent_days"])) or None,
        "max_videos": _field_int(fields.get(FEISHU_CONFIG_FIELDS["max_videos"])) or default_max_videos_per_profile(),
        "schedule_hour": _field_int(fields.get(FEISHU_CONFIG_FIELDS["schedule_hour"])),
        "project": _field_text(fields.get(FEISHU_CONFIG_FIELDS["project"])),
        "owner": _field_text(fields.get(FEISHU_CONFIG_FIELDS["owner"])),
        "region": _field_text(fields.get(FEISHU_CONFIG_FIELDS["region"])),
        "category": _field_text(fields.get(FEISHU_CONFIG_FIELDS["category"])),
        "notes": _field_text(fields.get(FEISHU_CONFIG_FIELDS["notes"])),
        "config_key": config_key,
        "creator_key": creator_key,
        "last_run_at": fields.get(FEISHU_CONFIG_FIELDS["last_run_at"]),
        "last_status": _field_text(fields.get(FEISHU_CONFIG_FIELDS["last_status"])),
        "last_error": _field_text(fields.get(FEISHU_CONFIG_FIELDS["last_error"])),
    }


def _update_feishu_config_records(configs: Iterable[dict], fields_fn: Callable[[dict], dict]) -> None:
    table_config = feishu_video_table_config()
    app_token = table_config.get("base_token")
    table_id = table_config.get("config_table_id")
    updates = []
    for cfg in configs:
        record_id = cfg.get("record_id")
        if not record_id:
            continue
        fields = fields_fn(cfg)
        if fields:
            updates.append({"record_id": record_id, "fields": fields})
    if not updates or not app_token or not table_id:
        return
    FeishuBitableClient().batch_update_records(app_token, table_id, updates)


def _write_feishu_config_results(profile_results: list[dict]) -> None:
    by_record = {item.get("record_id"): item for item in profile_results if item.get("record_id")}
    if not by_record:
        return
    now_dt = datetime.now(FEISHU_TZ) if FEISHU_TZ else datetime.now()
    _update_feishu_config_records(
        [{"record_id": rid} for rid in by_record],
        lambda cfg: {
            FEISHU_CONFIG_FIELDS["last_status"]: by_record[cfg["record_id"]].get("status") or "",
            FEISHU_CONFIG_FIELDS["last_error"]: by_record[cfg["record_id"]].get("error") or "",
            FEISHU_CONFIG_FIELDS["last_run_at"]: _feishu_datetime_value(now_dt),
        },
    )


def _date_window_for_feishu_config(cfg: dict) -> tuple[Optional[str], Optional[str]]:
    scope = cfg.get("sync_scope") or "recent"
    if scope == "all":
        return None, None
    if scope == "range":
        return _clean_date(cfg.get("start_date")) if cfg.get("start_date") else None, _clean_date(cfg.get("end_date")) if cfg.get("end_date") else None
    days = _field_int(cfg.get("recent_days")) or int(os.environ.get("PROFILE_VIDEO_RECENT_DAYS", "7"))
    return (date.today() - timedelta(days=max(days, 1))).isoformat(), None


def _normalize_video_table_row(row: dict, *, now_dt: datetime, is_new: bool) -> dict:
    out = dict(row)
    platform = out.get("platform") or video_metrics_etl.detect_platform(out.get("video_url") or out.get("profile_url") or "")
    out["platform"] = _platform_label(platform)
    out["creator_key"] = out.get("creator_key") or _creator_key_for(platform, out.get("profile_url"))
    out["video_id"] = _video_id_from_key(out.get("video_key"))
    out["video_type"] = _video_type_for(platform, out.get("video_url"), out.get("duration"))
    out["duration_seconds"] = _duration_seconds(out.get("duration"))
    out["post_age_days"] = _post_age_days(out.get("post_date"))
    out["hashtags"] = _hashtags(out.get("caption"))
    out["last_synced_at"] = now_dt
    out["first_seen_date"] = _today_text(now_dt)
    out["is_new_video"] = bool(is_new)
    out["status"] = "成功"
    out["error"] = ""
    views = _field_int(out.get("views")) or 0
    engagement = _field_int(out.get("engagement")) or 0
    out["engagement_rate"] = round(engagement / views, 6) if views else None
    out["views_delta_1d"] = None
    out["views_delta_7d"] = None
    out["daily_views_delta"] = None
    out["daily_engagement_delta"] = None
    return out


def _row_to_named_fields(row: dict, field_map: dict[str, str]) -> dict:
    fields = {}
    numeric_keys = {
        "schedule_hour", "recent_days", "max_videos", "views", "likes", "comments",
        "shares", "collects", "engagement", "followers", "duration_seconds",
        "post_age_days", "views_delta_1d", "views_delta_7d", "daily_views_delta",
        "daily_engagement_delta", "profile_count", "success_profile_count",
        "failed_profile_count", "video_count", "created_count", "updated_count",
        "snapshot_count", "engagement_rate",
    }
    date_keys = {
        "post_date", "first_seen_date", "last_synced_at", "snapshot_date",
        "synced_at", "run_date", "started_at", "finished_at", "last_run_at",
    }
    bool_keys = {"is_new_video", "enabled"}
    for key, field_name in field_map.items():
        value = row.get(key)
        if value in (None, ""):
            continue
        if key in numeric_keys:
            if key == "engagement_rate":
                try:
                    value = float(value)
                except Exception:
                    continue
            else:
                value = _field_int(value)
                if value is None:
                    continue
        elif key in date_keys:
            value = _feishu_datetime_value(value)
            if value is None:
                continue
        elif key in bool_keys:
            value = bool(value)
        else:
            value = str(value)[:2000]
        fields[field_name] = value
    return fields


def _existing_by_key(client: "FeishuBitableClient", app_token: str, table_id: str, field_name: str, keys: list) -> dict[str, str]:
    unique_keys = [str(k) for k in dict.fromkeys(keys) if k]
    if not unique_keys:
        return {}
    existing = {}
    if field_name in {FEISHU_LATEST_FIELDS["video_key"], FEISHU_SNAPSHOT_FIELDS["snapshot_key"]}:
        existing.update(_local_existing_records(unique_keys, app_token, table_id))
    missing = [key for key in unique_keys if key not in existing]
    if missing:
        existing.update(client.find_records_by_field(app_token, table_id, field_name, missing))
    return existing


def _snapshot_history_for_rows(rows: list[dict], *, app_token: str, snapshot_table_id: str, snapshot_date: str) -> dict[str, dict]:
    video_keys = [str(row.get("video_key")) for row in rows if row.get("video_key")]
    if not video_keys:
        return {}
    target_day = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    day_1 = (target_day - timedelta(days=1)).isoformat()
    day_7 = (target_day - timedelta(days=7)).isoformat()
    snapshot_keys = []
    for video_key in dict.fromkeys(video_keys):
        snapshot_keys.append(_snapshot_key(video_key, day_1))
        snapshot_keys.append(_snapshot_key(video_key, day_7))
    local_rows = _local_state_rows(snapshot_keys, app_token, snapshot_table_id)
    history: dict[str, dict] = defaultdict(dict)
    for snapshot_key, state_row in local_rows.items():
        video_key, day = _split_snapshot_key(snapshot_key)
        if not video_key or not day:
            continue
        if day == day_1:
            history[video_key]["1d"] = state_row
        if day == day_7:
            history[video_key]["7d"] = state_row
    return dict(history)


def _apply_video_growth_metrics(row: dict, history: dict) -> None:
    current_views = _field_int(row.get("views"))
    current_engagement = _field_int(row.get("engagement"))
    one_day = history.get("1d") or {}
    seven_day = history.get("7d") or {}
    views_1d = _field_int(one_day.get("views"))
    engagement_1d = _field_int(one_day.get("engagement"))
    views_7d = _field_int(seven_day.get("views"))
    if current_views is not None and views_1d is not None:
        row["views_delta_1d"] = max(0, current_views - views_1d)
        row["daily_views_delta"] = row["views_delta_1d"]
    if current_engagement is not None and engagement_1d is not None:
        row["daily_engagement_delta"] = max(0, current_engagement - engagement_1d)
    if current_views is not None and views_7d is not None:
        row["views_delta_7d"] = max(0, current_views - views_7d)


def _local_state_rows(video_keys: list[str], app_token: str, table_id: str) -> dict[str, dict]:
    keys = [k for k in dict.fromkeys(video_keys) if k]
    if not keys:
        return {}
    rows = db.query_all(
        """
        SELECT video_key, last_metrics_json
        FROM video_profile_video_state
        WHERE video_key = ANY(%s)
          AND COALESCE(feishu_app_token, '') = %s
          AND COALESCE(feishu_table_id, '') = %s
          AND last_metrics_json IS NOT NULL
          AND last_metrics_json <> ''
        """,
        (keys, app_token, table_id),
    )
    out = {}
    for item in rows or []:
        try:
            out[item["video_key"]] = json.loads(item.get("last_metrics_json") or "{}")
        except Exception:
            continue
    return out


def _snapshot_key(video_key: Optional[str], snapshot_date: str) -> str:
    return f"{video_key or ''}:{snapshot_date}"


def _split_snapshot_key(snapshot_key: str) -> tuple[str, str]:
    text = str(snapshot_key or "")
    if len(text) < 11:
        return text, ""
    day = text[-10:]
    if re.match(r"\d{4}-\d{2}-\d{2}", day) and text.endswith(":" + day):
        return text[: -11], day
    return text, ""


def _profile_error_summary(profile_results: list[dict]) -> str:
    errors = [item.get("error") for item in profile_results if item.get("error")]
    return "；".join(errors)[:500]


def _trigger_label(value: str) -> str:
    text = str(value or "").lower()
    if text == "scheduled":
        return "定时"
    if text == "retry":
        return "重试"
    return "手动"


def _platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(str(platform or "").upper(), str(platform or "") or "未知")


def _platform_code(platform: str) -> str:
    text = str(platform or "").strip().lower()
    if text in {"tt", "tiktok", "tik tok", "抖音"}:
        return "TT"
    if text in {"ig", "instagram"}:
        return "IG"
    if text in {"yt", "ytb", "youtube", "you tube"}:
        return "YTB"
    return ""


def _sync_scope_code(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"全部", "all"}:
        return "all"
    if text in {"近n天", "近 n 天", "recent", "recent_days"}:
        return "recent"
    if text in {"近n条", "近 n 条", "latest", "recent_count"}:
        return "all"
    if text in {"range", "日期范围"}:
        return "range"
    return "recent"


def _creator_key_for(platform: Optional[str], profile_url: Optional[str]) -> str:
    plat = str(platform or video_metrics_etl.detect_platform(profile_url or "") or "UNKNOWN").upper()
    norm = video_metrics_etl.normalize_url(profile_url or "")
    slug = norm.rstrip("/").split("/")[-1] if norm else ""
    return f"{plat}:{slug or norm}"


def _video_id_from_key(video_key: Optional[str]) -> str:
    text = str(video_key or "")
    return text.split(":", 1)[1] if ":" in text else text


def _video_type_for(platform: str, video_url: Optional[str], duration: Optional[str]) -> str:
    url = str(video_url or "").lower()
    plat = str(platform or "").upper()
    if "live" in url:
        return "直播回放"
    if plat in {"TT", "IG"}:
        return "短视频"
    return "视频"


def _duration_seconds(value) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    parts = [p for p in text.split(":") if p.strip().isdigit()]
    if parts:
        total = 0
        for part in parts:
            total = total * 60 + int(part)
        return total
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _post_age_days(value) -> Optional[int]:
    day = _date_text(value)
    if not day:
        return None
    try:
        return max(0, (date.today() - datetime.strptime(day, "%Y-%m-%d").date()).days)
    except Exception:
        return None


def _hashtags(text: Optional[str]) -> str:
    if not text:
        return ""
    tags = re.findall(r"#[\w\u4e00-\u9fff-]+", str(text))
    return ", ".join(dict.fromkeys(tags))[:500]


def _field_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item.get("value") or ""))
            else:
                parts.append(str(item))
        return ", ".join([p for p in parts if p]).strip()
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or "").strip()
    return str(value).strip()


def _field_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = _field_text(value).lower()
    return text not in {"", "false", "0", "否", "停用", "disabled", "no"}


def _field_int(value) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = _field_text(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def _date_text(value=None) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        val = float(value)
        if val > 10_000_000_000:
            val /= 1000
        try:
            return datetime.fromtimestamp(val, FEISHU_TZ).date().isoformat() if FEISHU_TZ else datetime.fromtimestamp(val).date().isoformat()
        except Exception:
            return ""
    text = _field_text(value)
    if not text:
        return ""
    return text[:10].replace("/", "-")


def _today_text(value=None) -> str:
    if value is not None:
        return _date_text(value)
    return datetime.now(FEISHU_TZ).date().isoformat() if FEISHU_TZ else date.today().isoformat()


def _feishu_datetime_value(value) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        raw = int(value)
        return raw if raw > 10_000_000_000 else raw * 1000
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    if isinstance(value, datetime):
        dt = value
    else:
        text = _field_text(value)
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("/", "-").replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(text[:10].replace("/", "-"), "%Y-%m-%d")
            except Exception:
                return None
    if dt.tzinfo is None and FEISHU_TZ:
        dt = dt.replace(tzinfo=FEISHU_TZ)
    return int(dt.timestamp() * 1000)


def _clean_scope(value) -> str:
    value = str(value or "recent").strip().lower()
    return value if value in {"recent", "range", "all"} else "recent"


def _clean_date(value) -> Optional[str]:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    datetime.strptime(text, "%Y-%m-%d")
    return text


def _clean_hour(value) -> int:
    try:
        hour = int(value)
    except Exception:
        hour = default_sync_hour()
    return max(0, min(hour, 23))


def _clean_max_videos(value) -> int:
    try:
        count = int(value)
    except Exception:
        count = default_max_videos_per_profile()
    hard_max = max(1, DEFAULT_PROFILE_VIDEO_HARD_MAX_VIDEOS)
    return max(1, min(count, hard_max))


def _escape_formula_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def default_feishu_target() -> tuple[str, str]:
    return (
        os.environ.get("FEISHU_BITABLE_APP_TOKEN", "").strip(),
        os.environ.get("FEISHU_BITABLE_TABLE_ID", "").strip(),
    )


def default_sync_hour() -> int:
    try:
        return int(os.environ.get("PROFILE_VIDEO_SYNC_HOUR", str(DEFAULT_SYNC_HOUR)))
    except Exception:
        return DEFAULT_SYNC_HOUR


def default_max_videos_per_profile() -> int:
    try:
        return int(os.environ.get("PROFILE_VIDEO_MAX_VIDEOS", str(DEFAULT_MAX_VIDEOS_PER_PROFILE)))
    except Exception:
        return DEFAULT_MAX_VIDEOS_PER_PROFILE


def _feishu_max_profiles_per_run(params: Optional[dict] = None) -> int:
    raw = (params or {}).get("max_profiles_per_run")
    if raw in (None, ""):
        raw = os.environ.get("FEISHU_PROFILE_VIDEO_MAX_PROFILES_PER_RUN", str(DEFAULT_FEISHU_MAX_PROFILES_PER_RUN))
    try:
        value = int(raw)
    except Exception:
        value = DEFAULT_FEISHU_MAX_PROFILES_PER_RUN
    return max(1, min(value, 200))


def _task_stop_requested(task_id: str) -> bool:
    try:
        row = db.query_one("SELECT status FROM task_queue WHERE task_id = %s", (task_id,))
        return bool(row and row.get("status") not in {"claimed", "processing"})
    except Exception as exc:
        logger.warning("检查任务停止状态失败 task=%s: %s", task_id, exc)
        return False


def _raise_if_task_stopped(task_id: str) -> None:
    if _task_stop_requested(task_id):
        raise RuntimeError("任务已被停止，已中断后续 Apify 调用")


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
