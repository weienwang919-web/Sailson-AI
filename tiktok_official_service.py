from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any

import requests
from openpyxl import Workbook

import database as db

API_BASE = "https://business-api.tiktok.com/open_api/v1.3"

VIDEO_FIELDS = [
    "item_id",
    "media_type",
    "is_ad",
    "thumbnail_url",
    "share_url",
    "embed_url",
    "caption",
    "video_duration",
    "likes",
    "comments",
    "shares",
    "favorites",
    "create_time",
    "reach",
    "video_views",
    "total_time_watched",
    "average_time_watched",
    "full_video_watched_rate",
    "new_followers",
    "profile_views",
    "website_clicks",
    "phone_number_clicks",
    "lead_submissions",
    "app_download_clicks",
    "email_clicks",
    "address_clicks",
    "video_view_retention",
    "impression_sources",
    "audience_genders",
    "audience_countries",
    "audience_cities",
    "audience_types",
    "engagement_likes",
]

PROFILE_FIELDS = [
    "is_business_account",
    "profile_image",
    "username",
    "profile_deep_link",
    "display_name",
    "bio_description",
    "is_verified",
    "following_count",
    "followers_count",
    "total_likes",
    "videos_count",
    "video_views",
    "unique_video_views",
    "profile_views",
    "likes",
    "comments",
    "shares",
    "phone_number_clicks",
    "lead_submissions",
    "app_download_clicks",
    "bio_link_clicks",
    "email_clicks",
    "address_clicks",
    "daily_total_followers",
    "daily_new_followers",
    "daily_lost_followers",
    "audience_activity",
    "engaged_audience",
    "audience_ages",
    "audience_genders",
    "audience_countries",
    "audience_cities",
]


def ensure_schema() -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tiktok_official_accounts (
            id SERIAL PRIMARY KEY,
            business_id VARCHAR(128) UNIQUE NOT NULL,
            account_name VARCHAR(255),
            display_name VARCHAR(255),
            username VARCHAR(255),
            profile_deep_link TEXT,
            profile_image TEXT,
            bio_description TEXT,
            is_business_account BOOLEAN,
            is_verified BOOLEAN,
            following_count BIGINT,
            followers_count BIGINT,
            total_likes BIGINT,
            videos_count BIGINT,
            enabled BOOLEAN DEFAULT TRUE,
            notes TEXT,
            raw_json TEXT,
            last_refreshed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tiktok_official_video_snapshots (
            id SERIAL PRIMARY KEY,
            business_id VARCHAR(128) NOT NULL,
            item_id VARCHAR(128) NOT NULL,
            media_type VARCHAR(32),
            is_ad BOOLEAN,
            thumbnail_url TEXT,
            share_url TEXT,
            embed_url TEXT,
            caption TEXT,
            video_duration DOUBLE PRECISION,
            create_time TIMESTAMP,
            likes BIGINT,
            comments BIGINT,
            shares BIGINT,
            favorites BIGINT,
            reach BIGINT,
            video_views BIGINT,
            total_time_watched DOUBLE PRECISION,
            average_time_watched DOUBLE PRECISION,
            full_video_watched_rate DOUBLE PRECISION,
            new_followers BIGINT,
            profile_views BIGINT,
            website_clicks BIGINT,
            phone_number_clicks BIGINT,
            lead_submissions BIGINT,
            app_download_clicks BIGINT,
            email_clicks BIGINT,
            address_clicks BIGINT,
            video_view_retention TEXT,
            engagement_likes TEXT,
            impression_sources TEXT,
            audience_genders TEXT,
            audience_countries TEXT,
            audience_cities TEXT,
            audience_types TEXT,
            request_id VARCHAR(255),
            log_id VARCHAR(255),
            raw_json TEXT,
            fetched_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (business_id, item_id)
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tiktok_official_profile_daily_metrics (
            id SERIAL PRIMARY KEY,
            business_id VARCHAR(128) NOT NULL,
            metric_date DATE NOT NULL,
            followers_count BIGINT,
            video_views BIGINT,
            unique_video_views BIGINT,
            profile_views BIGINT,
            likes BIGINT,
            comments BIGINT,
            shares BIGINT,
            phone_number_clicks BIGINT,
            lead_submissions BIGINT,
            app_download_clicks BIGINT,
            bio_link_clicks BIGINT,
            email_clicks BIGINT,
            address_clicks BIGINT,
            daily_total_followers BIGINT,
            daily_new_followers BIGINT,
            daily_lost_followers BIGINT,
            engaged_audience BIGINT,
            raw_json TEXT,
            fetched_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (business_id, metric_date)
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_tt_official_videos_business ON tiktok_official_video_snapshots (business_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tt_official_videos_create_time ON tiktok_official_video_snapshots (create_time DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tt_official_profile_business_date ON tiktok_official_profile_daily_metrics (business_id, metric_date)")


def configured_accounts() -> list[dict[str, Any]]:
    raw = os.environ.get("TIKTOK_OFFICIAL_ACCOUNTS_JSON", "").strip()
    if raw:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("TIKTOK_OFFICIAL_ACCOUNTS_JSON 必须是 JSON 数组")
        return [_normalize_account(x) for x in data if x]
    business_id = os.environ.get("TIKTOK_OFFICIAL_BUSINESS_ID", "").strip()
    if business_id:
        return [
            {
                "business_id": business_id,
                "account_name": os.environ.get("TIKTOK_OFFICIAL_ACCOUNT_NAME", "TikTok Official"),
                "enabled": True,
                "notes": "",
            }
        ]
    return []


def sync_configured_accounts() -> list[dict[str, Any]]:
    accounts = configured_accounts()
    for account in accounts:
        db.execute(
            """
            INSERT INTO tiktok_official_accounts (business_id, account_name, enabled, notes, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (business_id) DO UPDATE SET
                account_name = COALESCE(NULLIF(EXCLUDED.account_name, ''), tiktok_official_accounts.account_name),
                enabled = EXCLUDED.enabled,
                notes = COALESCE(EXCLUDED.notes, tiktok_official_accounts.notes),
                updated_at = NOW()
            """,
            (
                account["business_id"],
                account.get("account_name") or account["business_id"],
                bool(account.get("enabled", True)),
                account.get("notes") or "",
            ),
        )
    return list_accounts()


def list_accounts() -> list[dict[str, Any]]:
    sync_missing = db.query_one("SELECT COUNT(*) AS count FROM tiktok_official_accounts")
    if not sync_missing or int(sync_missing.get("count") or 0) == 0:
        for account in configured_accounts():
            db.execute(
                """
                INSERT INTO tiktok_official_accounts (business_id, account_name, enabled, notes)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (business_id) DO NOTHING
                """,
                (
                    account["business_id"],
                    account.get("account_name") or account["business_id"],
                    bool(account.get("enabled", True)),
                    account.get("notes") or "",
                ),
            )
    return db.query_all("SELECT * FROM tiktok_official_accounts ORDER BY enabled DESC, account_name, id") or []


def refresh_official_accounts(
    business_ids: list[str] | None = None,
    profile_days: int = 30,
    max_pages: int = 5,
    progress_hook=None,
) -> dict[str, Any]:
    token = os.environ.get("TIKTOK_BUSINESS_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TIKTOK_BUSINESS_ACCESS_TOKEN 未配置")
    sync_configured_accounts()
    accounts = list_accounts()
    if business_ids:
        wanted = set(business_ids)
        accounts = [a for a in accounts if a.get("business_id") in wanted]
    accounts = [a for a in accounts if a.get("enabled", True)]
    if not accounts:
        raise RuntimeError("未配置可用官号，请设置 TIKTOK_OFFICIAL_ACCOUNTS_JSON")

    total_videos = 0
    for idx, account in enumerate(accounts, start=1):
        bid = account["business_id"]
        name = account.get("account_name") or bid
        if progress_hook:
            progress_hook(f"正在刷新主页数据：{name} ({idx}/{len(accounts)})")
        profile_data, profile_meta = fetch_profile(token, bid, profile_days)
        upsert_profile(bid, profile_data, profile_meta)

        if progress_hook:
            progress_hook(f"正在刷新视频列表：{name}")
        videos, video_meta = fetch_videos(token, bid, max_pages=max_pages)
        for video in videos:
            upsert_video(bid, video, video_meta)
        total_videos += len(videos)
        db.execute(
            "UPDATE tiktok_official_accounts SET last_refreshed_at = NOW(), updated_at = NOW() WHERE business_id = %s",
            (bid,),
        )
    return {"accounts": len(accounts), "videos": total_videos}


def fetch_videos(token: str, business_id: str, max_pages: int = 5) -> tuple[list[dict[str, Any]], dict[str, str]]:
    videos: list[dict[str, Any]] = []
    cursor = None
    meta: dict[str, str] = {}
    for _ in range(max(1, max_pages)):
        params: dict[str, Any] = {
            "business_id": business_id,
            "fields": json.dumps(VIDEO_FIELDS, separators=(",", ":")),
            "max_count": 20,
        }
        if cursor is not None:
            params["cursor"] = cursor
        payload, headers = _request(token, "/business/video/list/", params)
        meta = {"request_id": payload.get("request_id") or "", "log_id": headers.get("X-Tt-Logid") or ""}
        data = payload.get("data") or {}
        videos.extend(data.get("videos") or [])
        if not data.get("has_more"):
            break
        cursor = data.get("cursor")
        if cursor is None:
            break
    return videos, meta


def fetch_profile(token: str, business_id: str, days: int = 30) -> tuple[dict[str, Any], dict[str, str]]:
    days = max(1, min(int(days or 30), 60))
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    params = {
        "business_id": business_id,
        "fields": json.dumps(PROFILE_FIELDS, separators=(",", ":")),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    payload, headers = _request(token, "/business/get/", params)
    return payload.get("data") or {}, {
        "request_id": payload.get("request_id") or "",
        "log_id": headers.get("X-Tt-Logid") or "",
    }


def _request(token: str, path: str, params: dict[str, Any]) -> tuple[dict[str, Any], requests.structures.CaseInsensitiveDict]:
    resp = requests.get(
        f"{API_BASE}{path}",
        params=params,
        headers={"Access-Token": token},
        timeout=60,
    )
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(f"TikTok API 返回非 JSON: HTTP {resp.status_code} {resp.text[:300]}") from exc
    if resp.status_code >= 400 or payload.get("code") not in (0, "0", None):
        request_id = payload.get("request_id") or resp.headers.get("X-Tt-Logid") or ""
        raise RuntimeError(f"TikTok API 错误: {payload.get('message') or resp.text[:200]} request_id={request_id}")
    return payload, resp.headers


def upsert_profile(business_id: str, data: dict[str, Any], meta: dict[str, str]) -> None:
    db.execute(
        """
        UPDATE tiktok_official_accounts SET
            display_name = %s,
            username = %s,
            profile_deep_link = %s,
            profile_image = %s,
            bio_description = %s,
            is_business_account = %s,
            is_verified = %s,
            following_count = %s,
            followers_count = %s,
            total_likes = %s,
            videos_count = %s,
            raw_json = %s,
            updated_at = NOW()
        WHERE business_id = %s
        """,
        (
            data.get("display_name"),
            data.get("username"),
            data.get("profile_deep_link"),
            data.get("profile_image"),
            data.get("bio_description"),
            data.get("is_business_account"),
            data.get("is_verified"),
            _to_int(data.get("following_count")),
            _to_int(data.get("followers_count")),
            _to_int(data.get("total_likes")),
            _to_int(data.get("videos_count")),
            json.dumps({"data": data, "meta": meta}, ensure_ascii=False),
            business_id,
        ),
    )
    for metric in data.get("metrics") or []:
        metric_date = metric.get("date")
        if not metric_date:
            continue
        db.execute(
            """
            INSERT INTO tiktok_official_profile_daily_metrics (
                business_id, metric_date, followers_count, video_views, unique_video_views,
                profile_views, likes, comments, shares, phone_number_clicks, lead_submissions,
                app_download_clicks, bio_link_clicks, email_clicks, address_clicks,
                daily_total_followers, daily_new_followers, daily_lost_followers,
                engaged_audience, raw_json, fetched_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (business_id, metric_date) DO UPDATE SET
                followers_count = EXCLUDED.followers_count,
                video_views = EXCLUDED.video_views,
                unique_video_views = EXCLUDED.unique_video_views,
                profile_views = EXCLUDED.profile_views,
                likes = EXCLUDED.likes,
                comments = EXCLUDED.comments,
                shares = EXCLUDED.shares,
                daily_total_followers = EXCLUDED.daily_total_followers,
                daily_new_followers = EXCLUDED.daily_new_followers,
                daily_lost_followers = EXCLUDED.daily_lost_followers,
                engaged_audience = EXCLUDED.engaged_audience,
                raw_json = EXCLUDED.raw_json,
                fetched_at = NOW()
            """,
            (
                business_id,
                metric_date,
                _to_int(metric.get("followers_count")),
                _to_int(metric.get("video_views")),
                _to_int(metric.get("unique_video_views")),
                _to_int(metric.get("profile_views")),
                _to_int(metric.get("likes")),
                _to_int(metric.get("comments")),
                _to_int(metric.get("shares")),
                _to_int(metric.get("phone_number_clicks")),
                _to_int(metric.get("lead_submissions")),
                _to_int(metric.get("app_download_clicks")),
                _to_int(metric.get("bio_link_clicks")),
                _to_int(metric.get("email_clicks")),
                _to_int(metric.get("address_clicks")),
                _to_int(metric.get("daily_total_followers")),
                _to_int(metric.get("daily_new_followers")),
                _to_int(metric.get("daily_lost_followers")),
                _to_int(metric.get("engaged_audience")),
                json.dumps(metric, ensure_ascii=False),
            ),
        )


def upsert_video(business_id: str, video: dict[str, Any], meta: dict[str, str]) -> None:
    item_id = str(video.get("item_id") or "").strip()
    if not item_id:
        return
    db.execute(
        """
        INSERT INTO tiktok_official_video_snapshots (
            business_id, item_id, media_type, is_ad, thumbnail_url, share_url, embed_url,
            caption, video_duration, create_time, likes, comments, shares, favorites,
            reach, video_views, total_time_watched, average_time_watched,
            full_video_watched_rate, new_followers, profile_views, website_clicks,
            phone_number_clicks, lead_submissions, app_download_clicks, email_clicks,
            address_clicks, video_view_retention, engagement_likes, impression_sources,
            audience_genders, audience_countries, audience_cities, audience_types,
            request_id, log_id, raw_json, fetched_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            NOW(), NOW()
        )
        ON CONFLICT (business_id, item_id) DO UPDATE SET
            media_type = EXCLUDED.media_type,
            is_ad = EXCLUDED.is_ad,
            thumbnail_url = EXCLUDED.thumbnail_url,
            share_url = EXCLUDED.share_url,
            embed_url = EXCLUDED.embed_url,
            caption = EXCLUDED.caption,
            video_duration = EXCLUDED.video_duration,
            create_time = EXCLUDED.create_time,
            likes = EXCLUDED.likes,
            comments = EXCLUDED.comments,
            shares = EXCLUDED.shares,
            favorites = EXCLUDED.favorites,
            reach = EXCLUDED.reach,
            video_views = EXCLUDED.video_views,
            total_time_watched = EXCLUDED.total_time_watched,
            average_time_watched = EXCLUDED.average_time_watched,
            full_video_watched_rate = EXCLUDED.full_video_watched_rate,
            new_followers = EXCLUDED.new_followers,
            profile_views = EXCLUDED.profile_views,
            website_clicks = EXCLUDED.website_clicks,
            phone_number_clicks = EXCLUDED.phone_number_clicks,
            lead_submissions = EXCLUDED.lead_submissions,
            app_download_clicks = EXCLUDED.app_download_clicks,
            email_clicks = EXCLUDED.email_clicks,
            address_clicks = EXCLUDED.address_clicks,
            video_view_retention = EXCLUDED.video_view_retention,
            engagement_likes = EXCLUDED.engagement_likes,
            impression_sources = EXCLUDED.impression_sources,
            audience_genders = EXCLUDED.audience_genders,
            audience_countries = EXCLUDED.audience_countries,
            audience_cities = EXCLUDED.audience_cities,
            audience_types = EXCLUDED.audience_types,
            request_id = EXCLUDED.request_id,
            log_id = EXCLUDED.log_id,
            raw_json = EXCLUDED.raw_json,
            fetched_at = NOW(),
            updated_at = NOW()
        """,
        (
            business_id,
            item_id,
            video.get("media_type"),
            video.get("is_ad"),
            video.get("thumbnail_url"),
            video.get("share_url"),
            video.get("embed_url"),
            video.get("caption"),
            _to_float(video.get("video_duration")),
            _epoch_to_dt(video.get("create_time")),
            _to_int(video.get("likes")),
            _to_int(video.get("comments")),
            _to_int(video.get("shares")),
            _to_int(video.get("favorites")),
            _to_int(video.get("reach")),
            _to_int(video.get("video_views")),
            _to_float(video.get("total_time_watched")),
            _to_float(video.get("average_time_watched")),
            _to_float(video.get("full_video_watched_rate")),
            _to_int(video.get("new_followers")),
            _to_int(video.get("profile_views")),
            _to_int(video.get("website_clicks")),
            _to_int(video.get("phone_number_clicks")),
            _to_int(video.get("lead_submissions")),
            _to_int(video.get("app_download_clicks")),
            _to_int(video.get("email_clicks")),
            _to_int(video.get("address_clicks")),
            _json(video.get("video_view_retention")),
            _json(video.get("engagement_likes")),
            _json(video.get("impression_sources")),
            _json(video.get("audience_genders")),
            _json(video.get("audience_countries")),
            _json(video.get("audience_cities")),
            _json(video.get("audience_types")),
            meta.get("request_id"),
            meta.get("log_id"),
            json.dumps(video, ensure_ascii=False),
        ),
    )


def list_videos(business_id: str | None = None, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    where = ""
    params: list[Any] = []
    if business_id:
        where = "WHERE v.business_id = %s"
        params.append(business_id)
    total_row = db.query_one(f"SELECT COUNT(*) AS count FROM tiktok_official_video_snapshots v {where}", tuple(params))
    rows = db.query_all(
        f"""
        SELECT v.*, a.account_name, a.display_name, a.username
        FROM tiktok_official_video_snapshots v
        LEFT JOIN tiktok_official_accounts a ON a.business_id = v.business_id
        {where}
        ORDER BY v.create_time DESC NULLS LAST, v.updated_at DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [page_size, (page - 1) * page_size]),
    )
    return {"total": int(total_row.get("count") or 0) if total_row else 0, "items": rows or []}


def get_video(item_id: str, business_id: str | None = None) -> dict[str, Any] | None:
    if business_id:
        row = db.query_one(
            "SELECT * FROM tiktok_official_video_snapshots WHERE item_id = %s AND business_id = %s",
            (item_id, business_id),
        )
    else:
        row = db.query_one("SELECT * FROM tiktok_official_video_snapshots WHERE item_id = %s", (item_id,))
    if row:
        for key in (
            "video_view_retention",
            "engagement_likes",
            "impression_sources",
            "audience_genders",
            "audience_countries",
            "audience_cities",
            "audience_types",
            "raw_json",
        ):
            row[key] = _loads(row.get(key))
    return row


def list_profile_metrics(business_id: str | None = None, days: int = 30) -> list[dict[str, Any]]:
    days = max(1, min(int(days or 30), 60))
    params: list[Any] = [date.today() - timedelta(days=days)]
    where = "WHERE metric_date >= %s"
    if business_id:
        where += " AND business_id = %s"
        params.append(business_id)
    return db.query_all(
        f"""
        SELECT *
        FROM tiktok_official_profile_daily_metrics
        {where}
        ORDER BY metric_date ASC, business_id ASC
        """,
        tuple(params),
    ) or []


def build_export() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Videos"
    video_headers = [
        "账号",
        "视频ID",
        "媒体类型",
        "是否广告视频",
        "发布时间",
        "视频链接",
        "视频标题/描述",
        "播放量",
        "覆盖人数",
        "点赞数",
        "评论数",
        "分享数",
        "收藏数",
        "新增粉丝",
        "平均观看时长(s)",
        "总观看时长(s)",
        "完播率",
        "视频时长(s)",
        "抓取时间",
    ]
    ws.append(video_headers)
    for row in list_videos(page=1, page_size=10000)["items"]:
        ws.append(
            [
                row.get("display_name") or row.get("account_name") or row.get("business_id"),
                row.get("item_id"),
                row.get("media_type"),
                row.get("is_ad"),
                row.get("create_time"),
                row.get("share_url"),
                row.get("caption"),
                row.get("video_views"),
                row.get("reach"),
                row.get("likes"),
                row.get("comments"),
                row.get("shares"),
                row.get("favorites"),
                row.get("new_followers"),
                row.get("average_time_watched"),
                row.get("total_time_watched"),
                row.get("full_video_watched_rate"),
                row.get("video_duration"),
                row.get("fetched_at"),
            ]
        )
    _append_array_sheet(wb, "Engagement Likes", "engagement_likes", ["item_id", "second", "percentage"])
    _append_array_sheet(wb, "Retention", "video_view_retention", ["item_id", "second", "percentage"])

    ws_metrics = wb.create_sheet("Profile Daily Metrics")
    metric_headers = [
        "business_id",
        "date",
        "followers_count",
        "video_views",
        "unique_video_views",
        "profile_views",
        "likes",
        "comments",
        "shares",
        "daily_new_followers",
        "daily_lost_followers",
        "engaged_audience",
    ]
    ws_metrics.append(metric_headers)
    for row in list_profile_metrics(days=60):
        ws_metrics.append([row.get(h if h != "date" else "metric_date") for h in metric_headers])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def run_refresh_task(task_id: str, params: dict[str, Any], update_task_fn) -> None:
    try:
        update_task_fn(task_id, status="processing", progress="TikTok 官号刷新中...")

        def hook(msg):
            update_task_fn(task_id, progress=msg)

        result = refresh_official_accounts(
            business_ids=params.get("business_ids") or None,
            profile_days=int(params.get("profile_days") or 30),
            max_pages=int(params.get("max_pages") or 5),
            progress_hook=hook,
        )
        update_task_fn(
            task_id,
            status="completed",
            progress="完成",
            result=json.dumps(result, ensure_ascii=False),
        )
    except Exception as exc:
        update_task_fn(task_id, status="failed", error=str(exc)[:500], progress="失败")


def _append_array_sheet(wb: Workbook, title: str, field: str, headers: list[str]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    rows = db.query_all(f"SELECT item_id, {field} FROM tiktok_official_video_snapshots WHERE {field} IS NOT NULL")
    for row in rows or []:
        values = _loads(row.get(field)) or []
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                ws.append([row.get("item_id"), item.get("second"), item.get("percentage") or item.get("percent")])


def _normalize_account(raw: dict[str, Any]) -> dict[str, Any]:
    bid = (raw.get("business_id") or raw.get("open_id") or "").strip()
    if not bid:
        raise ValueError("官号配置缺少 business_id/open_id")
    return {
        "business_id": bid,
        "account_name": raw.get("account_name") or raw.get("name") or bid,
        "enabled": raw.get("enabled", True),
        "notes": raw.get("notes") or "",
    }


def _to_int(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except Exception:
        return None


def _to_float(value) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _epoch_to_dt(value) -> datetime | None:
    try:
        if value in (None, ""):
            return None
        ts = float(value)
        if ts > 1e12:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def _json(value) -> str | None:
    if value in (None, ""):
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return value
