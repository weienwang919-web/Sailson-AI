from __future__ import annotations

import json
import logging
import os
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
        update_task_fn(task_id, progress=f"正在同步主页 {idx}/{len(configs)}")
        start_date, end_date = _date_window_for_config(cfg)
        rows = video_metrics_etl.fetch_profile_video_metrics(
            [cfg["profile_url"]],
            apify_token,
            start_date=start_date,
            end_date=end_date,
            max_videos=int(cfg.get("max_videos") or default_max_videos_per_profile()),
            progress_hook=lambda msg: update_task_fn(task_id, progress=msg),
        )
        for row in rows:
            row["config_id"] = cfg.get("id")
            row["feishu_app_token"] = cfg.get("feishu_app_token")
            row["feishu_table_id"] = cfg.get("feishu_table_id")
            if row.get("_error"):
                failed_profiles += 1
        all_rows.extend(rows)

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
        keys = [k for k in dict.fromkeys(video_keys) if k]
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
                                    "field_name": FEISHU_FIELD_MAP["video_key"],
                                    "operator": "is",
                                    "value": [key],
                                }
                            ],
                        }
                    },
                )
                for item in data.get("items") or []:
                    fields = item.get("fields") or {}
                    if str(fields.get(FEISHU_FIELD_MAP["video_key"]) or "") == key:
                        existing[key] = item.get("record_id")
                page_token = data.get("page_token") or ""
                if not data.get("has_more") or key in existing:
                    break
        return existing

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
    return max(1, min(count, 500))


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


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
