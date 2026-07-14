from __future__ import annotations

import json
import logging
import os
import secrets as pysecrets
import time
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from urllib.parse import unquote

import requests
from itsdangerous import BadSignature, URLSafeTimedSerializer
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference

import crypto_util
import database as db
import usage_service

logger = logging.getLogger(__name__)

API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
INVITE_SALT = "tiktok-official-invite"

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
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tiktok_official_tokens (
            id SERIAL PRIMARY KEY,
            token_type VARCHAR(32) DEFAULT 'account',
            open_id VARCHAR(128) UNIQUE NOT NULL,
            access_token TEXT NOT NULL,
            refresh_token TEXT,
            scope TEXT,
            expires_at TIMESTAMP,
            refresh_expires_at TIMESTAMP,
            raw_json TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_tt_official_videos_business ON tiktok_official_video_snapshots (business_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tt_official_videos_create_time ON tiktok_official_video_snapshots (create_time DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tt_official_profile_business_date ON tiktok_official_profile_daily_metrics (business_id, metric_date)")

    # 矩阵号自助授权：账号别名/授权人/启停状态
    db.execute("ALTER TABLE tiktok_official_accounts ADD COLUMN IF NOT EXISTS account_alias VARCHAR(255)")
    db.execute("ALTER TABLE tiktok_official_accounts ADD COLUMN IF NOT EXISTS authorized_by VARCHAR(255)")
    db.execute("ALTER TABLE tiktok_official_accounts ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'active'")
    db.execute("ALTER TABLE tiktok_official_tokens ADD COLUMN IF NOT EXISTS account_alias VARCHAR(255)")
    db.execute("ALTER TABLE tiktok_official_tokens ADD COLUMN IF NOT EXISTS authorized_by VARCHAR(255)")
    db.execute("ALTER TABLE tiktok_official_tokens ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'active'")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tiktok_official_invites (
            nonce VARCHAR(64) PRIMARY KEY,
            account_alias VARCHAR(255) NOT NULL,
            authorized_by VARCHAR(255),
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP,
            used_at TIMESTAMP
        )
        """
    )
    db.execute("ALTER TABLE tiktok_official_invites ALTER COLUMN expires_at DROP NOT NULL")


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
    return db.query_all(
        """
        SELECT a.*, t.expires_at AS token_expires_at, t.status AS token_status
        FROM tiktok_official_accounts a
        LEFT JOIN tiktok_official_tokens t ON t.open_id = a.business_id
        ORDER BY a.enabled DESC, a.account_name, a.id
        """
    ) or []


def refresh_official_accounts(
    business_ids: list[str] | None = None,
    profile_days: int = 30,
    max_pages: int = 5,
    progress_hook=None,
) -> dict[str, Any]:
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
        token = get_access_token(bid)
        if not token:
            raise RuntimeError(f"{name} 缺少 access_token，请先点击 TikTok 账号授权")
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


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def enqueue_daily_sync(
    create_task_fn,
    *,
    update_task_params_fn,
    after_enqueue_fn=None,
    batch_size: int = 5,
    profile_days: int = 30,
    max_pages: int = 5,
) -> list[str]:
    """每日矩阵账号批量拉取：把 status='active' 的账号按 batch_size 分批，创建 task_queue 记录复用现有刷新链路。

    不在调度线程里同步跑完，交给 worker 的线程池/DB worker 消费，账号一多也不会拖垮主调度线程。
    """
    accounts = [
        a for a in list_accounts()
        if a.get("enabled", True) and (a.get("status") or "active") == "active"
    ]
    if not accounts:
        logger.info("TikTok 官号矩阵每日同步：无启用中的账号，跳过")
        return []

    session_id = f"tiktok_official_daily_sync_{date.today().isoformat()}"
    task_ids = []
    for batch in _chunks(accounts, max(1, batch_size)):
        task_id = pysecrets.token_hex(16)
        business_ids = [a["business_id"] for a in batch]
        create_task_fn(task_id, None, session_id, function_type="tiktok_official_refresh")
        params = {
            "source": "tiktok_official_daily_sync",
            "trigger_type": "scheduled",
            "session_id": session_id,
            "business_ids": business_ids,
            "profile_days": profile_days,
            "max_pages": max_pages,
        }
        update_task_params_fn(task_id, params)
        if after_enqueue_fn:
            after_enqueue_fn(task_id, params)
        task_ids.append(task_id)
    return task_ids


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


def build_account_auth_url(public_base: str, state: str | None = None) -> str:
    from urllib.parse import urlencode

    app_id = (os.environ.get("TIKTOK_APP_ID") or os.environ.get("TIKTOK_CLIENT_KEY") or "").strip()
    if not app_id:
        raise RuntimeError("TIKTOK_APP_ID 未配置")
    scopes = [
        "user.info.basic",
        "user.info.username",
        "user.info.stats",
        "user.info.profile",
        "user.account.type",
        "user.insights",
        "video.list",
        "video.insights",
    ]
    params = {
        "client_key": app_id,
        "scope": ",".join(scopes),
        "response_type": "code",
        "redirect_uri": f"{public_base.rstrip('/')}/tiktok/account/callback",
        "state": state or "tiktok_account",
    }
    return "https://www.tiktok.com/v2/auth/authorize?" + urlencode(params)


_dev_secret_key_cache: str | None = None


def _invite_signing_key() -> str:
    """签名密钥：优先复用 Flask 的 SECRET_KEY；未配置时退化为进程内随机密钥（仅供本地开发，重启后旧邀请链接失效）。"""
    global _dev_secret_key_cache
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    if _dev_secret_key_cache is None:
        logger.warning("⚠️ SECRET_KEY 未配置，邀请链接签名将使用临时开发密钥，进程重启后已生成的邀请链接会失效")
        _dev_secret_key_cache = "dev-" + pysecrets.token_hex(32)
    return _dev_secret_key_cache


def _invite_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_invite_signing_key(), salt=INVITE_SALT)


def create_invite(account_alias: str, authorized_by: str | None = None, ttl_seconds: int | None = 24 * 3600) -> dict[str, Any]:
    """生成一条一次性授权邀请：写入 tiktok_official_invites，返回签名后的 state token。

    ttl_seconds 为 None 表示永不过期（仍然是一次性的，被使用后立即核销失效）。
    """
    account_alias = (account_alias or "").strip()
    if not account_alias:
        raise ValueError("account_alias 不能为空")
    expires_at = None
    if ttl_seconds is not None:
        ttl_seconds = max(300, min(int(ttl_seconds), 365 * 24 * 3600))
        expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
    nonce = pysecrets.token_hex(16)
    db.execute(
        """
        INSERT INTO tiktok_official_invites (nonce, account_alias, authorized_by, created_at, expires_at)
        VALUES (%s, %s, %s, NOW(), %s)
        """,
        (nonce, account_alias, authorized_by, expires_at),
    )
    state = _invite_serializer().dumps({"nonce": nonce, "account_alias": account_alias, "authorized_by": authorized_by})
    return {"state": state, "nonce": nonce, "account_alias": account_alias, "expires_at": expires_at}


def verify_and_consume_invite(state: str) -> dict[str, Any]:
    """校验授权回调带回的 state：签名有效、（若设置了有效期则）未过期、对应 invite 存在且未被使用过，成功后立即标记为已使用。

    过期时间完全由 tiktok_official_invites.expires_at 决定（NULL 代表永不过期），签名本身不做时间校验。
    失败时抛出 ValueError，message 可直接展示给用户。
    """
    try:
        payload = _invite_serializer().loads(state)
    except BadSignature as exc:
        raise ValueError("授权链接无效，请联系管理员重新生成") from exc

    nonce = payload.get("nonce")
    row = db.query_one("SELECT * FROM tiktok_official_invites WHERE nonce = %s", (nonce,))
    if not row:
        raise ValueError("授权链接无效，请联系管理员重新生成")
    if row.get("used_at"):
        raise ValueError("该授权链接已被使用过，请联系管理员重新生成一条新链接")
    if row.get("expires_at") and row["expires_at"] < datetime.utcnow():
        raise ValueError("授权链接已过期，请联系管理员重新生成")

    updated = db.execute(
        "UPDATE tiktok_official_invites SET used_at = NOW() WHERE nonce = %s AND used_at IS NULL",
        (nonce,),
    )
    if not updated:
        # 并发场景下两次回调几乎同时到达，只有第一次能抢到这行 UPDATE
        raise ValueError("该授权链接已被使用过，请联系管理员重新生成一条新链接")

    return {"account_alias": row.get("account_alias") or payload.get("account_alias"), "authorized_by": row.get("authorized_by") or payload.get("authorized_by")}


def build_invite_link(account_alias: str, public_base: str, authorized_by: str | None = None, ttl_seconds: int | None = 24 * 3600) -> dict[str, Any]:
    invite = create_invite(account_alias, authorized_by=authorized_by, ttl_seconds=ttl_seconds)
    invite["url"] = build_account_auth_url(public_base, state=invite["state"])
    return invite


def exchange_account_code(code: str, redirect_uri: str, account_alias: str | None = None, authorized_by: str | None = None) -> dict[str, Any]:
    """用 TikTok 账号授权 code 换 access_token，并保存 open_id 作为官号 business_id。"""
    app_id = (os.environ.get("TIKTOK_APP_ID") or os.environ.get("TIKTOK_CLIENT_KEY") or "").strip()
    app_secret = (os.environ.get("TIKTOK_APP_SECRET") or os.environ.get("TIKTOK_CLIENT_SECRET") or "").strip()
    if not app_id:
        raise RuntimeError("TIKTOK_APP_ID 未配置")
    if not app_secret:
        raise RuntimeError("TIKTOK_APP_SECRET 未配置")

    payload = {
        "client_key": app_id,
        "client_secret": app_secret,
        "code": unquote(code),
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    token_url = "https://open.tiktokapis.com/v2/oauth/token/"
    resp = requests.post(
        token_url,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        },
        timeout=60,
    )
    data = _parse_token_response(resp)
    if not data.get("access_token") or not data.get("open_id"):
        # Some TikTok endpoints wrap token payload in data.
        wrapped = data.get("data") if isinstance(data.get("data"), dict) else {}
        data = {**data, **wrapped}
    if not data.get("access_token") or not data.get("open_id"):
        raise RuntimeError(f"TikTok token 返回缺少 access_token/open_id: {json.dumps(data, ensure_ascii=False)[:500]}")

    save_account_token(data, account_alias=account_alias, authorized_by=authorized_by)
    return data


def save_account_token(token_data: dict[str, Any], account_alias: str | None = None, authorized_by: str | None = None) -> None:
    open_id = str(token_data.get("open_id") or "").strip()
    access_token = str(token_data.get("access_token") or "").strip()
    if not open_id or not access_token:
        raise ValueError("token_data 缺少 open_id/access_token")
    now = datetime.utcnow()
    expires_at = _seconds_from_now(now, token_data.get("expires_in"))
    refresh_expires_at = _seconds_from_now(now, token_data.get("refresh_expires_in"))
    scope = token_data.get("scope")
    if isinstance(scope, list):
        scope = ",".join(scope)
    db.execute(
        """
        INSERT INTO tiktok_official_tokens (
            token_type, open_id, access_token, refresh_token, scope,
            expires_at, refresh_expires_at, raw_json, account_alias, authorized_by, status, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', NOW())
        ON CONFLICT (open_id) DO UPDATE SET
            access_token = EXCLUDED.access_token,
            refresh_token = EXCLUDED.refresh_token,
            scope = EXCLUDED.scope,
            expires_at = EXCLUDED.expires_at,
            refresh_expires_at = EXCLUDED.refresh_expires_at,
            raw_json = EXCLUDED.raw_json,
            account_alias = COALESCE(EXCLUDED.account_alias, tiktok_official_tokens.account_alias),
            authorized_by = COALESCE(EXCLUDED.authorized_by, tiktok_official_tokens.authorized_by),
            status = 'active',
            updated_at = NOW()
        """,
        (
            "account",
            open_id,
            crypto_util.encrypt(access_token),
            crypto_util.encrypt(token_data.get("refresh_token")),
            scope,
            expires_at,
            refresh_expires_at,
            json.dumps(token_data, ensure_ascii=False),
            account_alias,
            authorized_by,
        ),
    )
    db.execute(
        """
        INSERT INTO tiktok_official_accounts (business_id, account_name, enabled, notes, account_alias, authorized_by, status, updated_at)
        VALUES (%s, %s, TRUE, %s, %s, %s, 'active', NOW())
        ON CONFLICT (business_id) DO UPDATE SET
            enabled = TRUE,
            account_alias = COALESCE(EXCLUDED.account_alias, tiktok_official_accounts.account_alias),
            authorized_by = COALESCE(EXCLUDED.authorized_by, tiktok_official_accounts.authorized_by),
            status = 'active',
            updated_at = NOW()
        """,
        (
            open_id,
            account_alias or token_data.get("display_name") or f"TikTok {open_id[-6:]}",
            "OAuth authorized account",
            account_alias,
            authorized_by,
        ),
    )


def refresh_account_token(open_id: str) -> str:
    """用存量 refresh_token 换新的 access_token，成功后重新加密落库，返回新的明文 access_token。"""
    row = db.query_one(
        "SELECT refresh_token, account_alias, authorized_by FROM tiktok_official_tokens WHERE open_id = %s",
        (open_id,),
    )
    refresh_token = crypto_util.decrypt((row or {}).get("refresh_token"))
    if not refresh_token:
        raise RuntimeError(f"open_id={open_id} 没有可用的 refresh_token，需要重新走一遍 OAuth 授权")

    app_id = (os.environ.get("TIKTOK_APP_ID") or os.environ.get("TIKTOK_CLIENT_KEY") or "").strip()
    app_secret = (os.environ.get("TIKTOK_APP_SECRET") or os.environ.get("TIKTOK_CLIENT_SECRET") or "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("TIKTOK_APP_ID / TIKTOK_APP_SECRET 未配置")

    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        data={
            "client_key": app_id,
            "client_secret": app_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cache-Control": "no-cache",
        },
        timeout=60,
    )
    data = _parse_token_response(resp)
    if not data.get("access_token"):
        wrapped = data.get("data") if isinstance(data.get("data"), dict) else {}
        data = {**data, **wrapped}
    if not data.get("access_token"):
        raise RuntimeError(f"TikTok refresh_token 返回缺少 access_token: {json.dumps(data, ensure_ascii=False)[:500]}")

    data.setdefault("open_id", open_id)
    save_account_token(data, account_alias=(row or {}).get("account_alias"), authorized_by=(row or {}).get("authorized_by"))
    logger.info(f"TikTok token 已刷新: open_id={open_id}")
    return data["access_token"]


def get_access_token(business_id: str | None = None, auto_refresh: bool = True) -> str:
    env_token = os.environ.get("TIKTOK_BUSINESS_ACCESS_TOKEN", "").strip()
    if env_token:
        return env_token
    if business_id:
        row = db.query_one(
            """
            SELECT open_id, access_token, refresh_token, expires_at FROM tiktok_official_tokens
            WHERE open_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (business_id,),
        )
    else:
        row = db.query_one(
            """
            SELECT open_id, access_token, refresh_token, expires_at FROM tiktok_official_tokens
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
    if not row:
        return ""

    expires_at = row.get("expires_at")
    if auto_refresh and expires_at and expires_at < datetime.utcnow() + timedelta(hours=1) and row.get("refresh_token"):
        try:
            return refresh_account_token(row["open_id"])
        except Exception as exc:
            logger.warning(f"TikTok token 自动刷新失败，回退用旧 token: open_id={row.get('open_id')} err={exc}")
    return crypto_util.decrypt(row.get("access_token")) or ""


def _parse_token_response(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"TikTok token 返回非 JSON: HTTP {resp.status_code} {resp.text[:300]}") from exc
    if resp.status_code >= 400 or data.get("error") or data.get("code") not in (0, "0", None):
        message = data.get("error_description") or data.get("message") or data.get("error") or resp.text[:300]
        raise RuntimeError(f"TikTok token 错误: {message}")
    return data


def _seconds_from_now(now: datetime, seconds) -> datetime | None:
    try:
        if seconds in (None, ""):
            return None
        return now + timedelta(seconds=int(seconds))
    except Exception:
        return None


def _request(token: str, path: str, params: dict[str, Any], max_retries: int = 3) -> tuple[dict[str, Any], requests.structures.CaseInsensitiveDict]:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        resp = requests.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"Access-Token": token},
            timeout=60,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            last_exc = RuntimeError(f"TikTok API 限流/服务端错误: HTTP {resp.status_code} {resp.text[:200]}")
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(f"TikTok API {path} 返回 {resp.status_code}，{wait}s 后重试（第 {attempt + 1}/{max_retries} 次）")
                time.sleep(wait)
                continue
            raise last_exc
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"TikTok API 返回非 JSON: HTTP {resp.status_code} {resp.text[:300]}") from exc
        if resp.status_code >= 400 or payload.get("code") not in (0, "0", None):
            request_id = payload.get("request_id") or resp.headers.get("X-Tt-Logid") or ""
            raise RuntimeError(f"TikTok API 错误: {payload.get('message') or resp.text[:200]} request_id={request_id}")
        return payload, resp.headers
    raise last_exc or RuntimeError("TikTok API 请求失败")


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


def build_export(videos: list[dict[str, Any]] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "视频明细"
    video_headers = [
        "发布时间",
        "账号",
        "视频",
        "播放",
        "覆盖",
        "点赞",
        "评论",
        "分享",
        "收藏",
        "平均观看",
        "视频时长",
        "完播率",
        "新增粉",
        "详情",
    ]
    ws.append(video_headers)
    rows = _export_video_rows(videos)
    for row in rows:
        ws.append(
            [
                row.get("create_time"),
                row.get("display_name") or row.get("account_name") or row.get("username") or row.get("business_id"),
                row.get("caption"),
                row.get("video_views"),
                row.get("reach"),
                row.get("likes"),
                row.get("comments"),
                row.get("shares"),
                row.get("favorites"),
                row.get("average_time_watched"),
                row.get("video_duration"),
                row.get("full_video_watched_rate"),
                row.get("new_followers"),
                row.get("share_url") or row.get("item_id"),
            ]
        )
    ws.freeze_panes = "A2"
    _autosize_columns(ws)
    for idx, row in enumerate(rows, start=1):
        _append_video_detail_sheet(wb, row, idx)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _export_video_rows(videos: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    selected: list[tuple[str, str | None]] = []
    for item in videos or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item_id") or "").strip()
        business_id = str(item.get("business_id") or "").strip() or None
        if item_id:
            selected.append((item_id, business_id))
    if not selected:
        return list_videos(page=1, page_size=10000)["items"]

    clauses = []
    params: list[Any] = []
    for item_id, business_id in selected:
        if business_id:
            clauses.append("(v.item_id = %s AND v.business_id = %s)")
            params.extend([item_id, business_id])
        else:
            clauses.append("v.item_id = %s")
            params.append(item_id)
    rows = db.query_all(
        f"""
        SELECT v.*, a.account_name, a.display_name, a.username
        FROM tiktok_official_video_snapshots v
        LEFT JOIN tiktok_official_accounts a ON a.business_id = v.business_id
        WHERE {" OR ".join(clauses)}
        ORDER BY v.create_time DESC NULLS LAST, v.updated_at DESC
        """,
        tuple(params),
    )
    return rows or []


def _append_video_detail_sheet(wb: Workbook, row: dict[str, Any], index: int) -> None:
    title = _unique_sheet_title(wb, f"{index}-{_chart_label(row)}")
    ws = wb.create_sheet(title)
    account = row.get("display_name") or row.get("account_name") or row.get("username") or row.get("business_id")
    ws["A1"] = "视频详情"
    ws["A2"] = "账号"
    ws["B2"] = account
    ws["A3"] = "视频"
    ws["B3"] = row.get("caption")
    ws["A4"] = "链接"
    ws["B4"] = row.get("share_url") or row.get("item_id")

    engagement_likes = _like_points(_loads(row.get("engagement_likes")) or [], row.get("likes"))
    retention = _series_points(_loads(row.get("video_view_retention")) or [])
    likes_count = _write_like_table(ws, 30, 1, engagement_likes)
    retention_count = _write_series_table(ws, 30, 6, "每秒留存明细", retention, "留存率")
    _add_series_chart(ws, 30, 1, likes_count, "估算每秒点赞量", "估算点赞数", "A6", value_offset=2)
    _add_series_chart(ws, 30, 6, retention_count, "留存折线图", "留存率", "I6")
    _autosize_columns(ws)


def _write_like_table(ws, start_row: int, start_col: int, values) -> int:
    ws.cell(row=start_row, column=start_col, value="点赞时间分布")
    ws.cell(row=start_row + 1, column=start_col, value="second")
    ws.cell(row=start_row + 1, column=start_col + 1, value="点赞分布比例")
    ws.cell(row=start_row + 1, column=start_col + 2, value="估算点赞数")
    if not isinstance(values, list) or not values:
        ws.cell(row=start_row + 2, column=start_col, value="暂无数据")
        return 0
    out_row = start_row + 2
    for second, ratio, estimated_likes in values:
        ws.cell(row=out_row, column=start_col, value=second)
        ws.cell(row=out_row, column=start_col + 1, value=ratio)
        ws.cell(row=out_row, column=start_col + 2, value=estimated_likes)
        out_row += 1
    return len(values)


def _write_series_table(ws, start_row: int, start_col: int, title: str, values, value_header: str) -> int:
    ws.cell(row=start_row, column=start_col, value=title)
    ws.cell(row=start_row + 1, column=start_col, value="second")
    ws.cell(row=start_row + 1, column=start_col + 1, value=value_header)
    if not isinstance(values, list) or not values:
        ws.cell(row=start_row + 2, column=start_col, value="暂无数据")
        return 0
    out_row = start_row + 2
    count = 0
    for item in values:
        second, value = item
        ws.cell(row=out_row, column=start_col, value=second)
        ws.cell(row=out_row, column=start_col + 1, value=value)
        out_row += 1
        count += 1
    if count == 0:
        ws.cell(row=start_row + 2, column=start_col, value="暂无数据")
    return count


def _series_points(values) -> list[tuple[int, float]]:
    if not isinstance(values, list):
        return []
    points: dict[int, float] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        second = _series_second(item)
        value = _series_value(item)
        if second is None or value is None:
            continue
        points[int(second)] = float(value)
    return sorted(points.items())


def _like_points(values, total_likes) -> list[tuple[int, float, float]]:
    likes = _to_float(total_likes) or 0.0
    return [(second, ratio, ratio * likes) for second, ratio in _series_points(values)]


def _add_series_chart(
    ws,
    start_row: int,
    start_col: int,
    source_len: int,
    title: str,
    value_title: str,
    anchor: str,
    value_offset: int = 1,
) -> None:
    if source_len <= 0:
        return
    data_start = start_row + 1
    data_end = start_row + 1 + source_len
    if data_end <= data_start:
        return
    chart = LineChart()
    chart.title = title
    chart.y_axis.title = value_title
    chart.x_axis.title = "second"
    data_col = start_col + value_offset
    data = Reference(ws, min_col=data_col, max_col=data_col, min_row=data_start, max_row=data_end)
    cats = Reference(ws, min_col=start_col, min_row=data_start + 1, max_row=data_end)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.height = 12
    chart.width = 24
    ws.add_chart(chart, anchor)


def _unique_sheet_title(wb: Workbook, raw: str) -> str:
    safe = "".join("_" if ch in '[]:*?/\\' else ch for ch in str(raw or "video")).strip() or "video"
    base = safe[:31]
    title = base
    n = 2
    while title in wb.sheetnames:
        suffix = f"_{n}"
        title = base[: 31 - len(suffix)] + suffix
        n += 1
    return title


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
        usage_service.record_usage_event(
            module="tiktok_official_refresh",
            user_id=params.get("user_id"),
            task_id=task_id,
            item_count=int(result.get("videos") or 0),
            crawler_items=0,
            api_calls=int(result.get("accounts") or 0) * 2,
            source="actual",
            detail={"business_ids": params.get("business_ids") or [], "profile_days": params.get("profile_days"), "result": result},
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
                ws.append([row.get("item_id"), _series_second(item), _series_value(item)])
    ws.freeze_panes = "A2"
    _autosize_columns(ws)


def _append_series_chart_sheet(wb: Workbook, title: str, field: str, value_title: str) -> None:
    ws = wb.create_sheet(title)
    rows = db.query_all(
        f"""
        SELECT item_id, caption, {field}
        FROM tiktok_official_video_snapshots
        WHERE {field} IS NOT NULL
        ORDER BY create_time DESC NULLS LAST, updated_at DESC
        LIMIT 8
        """
    )
    series: list[tuple[str, dict[int, float]]] = []
    seconds: set[int] = set()
    for row in rows or []:
        values = _loads(row.get(field)) or []
        if not isinstance(values, list):
            continue
        points: dict[int, float] = {}
        for item in values:
            if not isinstance(item, dict):
                continue
            second = _series_second(item)
            value = _series_value(item)
            if second is None or value is None:
                continue
            points[int(second)] = float(value)
            seconds.add(int(second))
        if points:
            series.append((_chart_label(row), points))

    ws.append(["second"] + [label for label, _points in series])
    for second in sorted(seconds):
        ws.append([second] + [points.get(second) for _label, points in series])
    if not series or not seconds:
        ws["A2"] = f"暂无 {value_title} 分布数据"
        return

    chart = LineChart()
    chart.title = title
    chart.y_axis.title = value_title
    chart.x_axis.title = "second"
    data = Reference(ws, min_col=2, max_col=1 + len(series), min_row=1, max_row=1 + len(seconds))
    cats = Reference(ws, min_col=1, min_row=2, max_row=1 + len(seconds))
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.height = 14
    chart.width = 28
    ws.add_chart(chart, "H2")
    ws.freeze_panes = "A2"
    _autosize_columns(ws)


def _series_second(item: dict[str, Any]) -> int | None:
    for key in ("second", "seconds", "time", "time_second"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except Exception:
            continue
    return None


def _series_value(item: dict[str, Any]) -> float | None:
    for key in ("percentage", "percent", "value", "likes", "like_count", "count", "rate"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _series_value_at_second(values, second: int) -> float | None:
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, dict) and _series_second(item) == second:
            return _series_value(item)
    return None


def _format_series_summary(values) -> str | None:
    if not isinstance(values, list) or not values:
        return None
    points = []
    for item in values[:20]:
        if not isinstance(item, dict):
            continue
        second = _series_second(item)
        value = _series_value(item)
        if second is not None and value is not None:
            points.append(f"{second}s:{value:g}")
    return "; ".join(points) if points else None


def _chart_label(row: dict[str, Any]) -> str:
    caption = str(row.get("caption") or "").strip()
    item_id = str(row.get("item_id") or "").strip()
    label = caption[:18] if caption else item_id
    return label or "video"


def _autosize_columns(ws) -> None:
    for column_cells in ws.columns:
        letter = column_cells[0].column_letter
        max_len = 0
        for cell in column_cells[:80]:
            value = cell.value
            if value is not None:
                max_len = max(max_len, len(str(value)))
        ws.column_dimensions[letter].width = min(max(max_len + 2, 10), 36)


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
