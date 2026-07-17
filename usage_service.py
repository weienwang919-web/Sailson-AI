"""Unified usage and cost accounting.

Crawler cost is standardized at USD 3 per 1,000 crawler-returned rows.
The table stores both actual events and estimated historical rows so reports can
separate reliable records from backfilled estimates.
"""
import datetime
import json
import logging
import os
from decimal import Decimal, ROUND_HALF_UP

import database as db

logger = logging.getLogger(__name__)

USD_TO_CNY = Decimal(str(os.environ.get("USD_TO_CNY", "7.2")))
CRAWLER_USD_PER_1000 = Decimal(str(os.environ.get("CRAWLER_USD_PER_1000", "4.5")))
AI_CNY_PER_1000 = Decimal(str(os.environ.get("AI_CNY_PER_1000", "0.008")))


def ensure_schema():
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id SERIAL PRIMARY KEY,
                event_key VARCHAR(160) UNIQUE,
                source VARCHAR(32) NOT NULL DEFAULT 'actual',
                module VARCHAR(80) NOT NULL,
                task_id VARCHAR(128),
                record_id INTEGER,
                user_id INTEGER,
                username VARCHAR(80),
                department VARCHAR(80),
                item_count INTEGER DEFAULT 0,
                crawler_items INTEGER DEFAULT 0,
                ai_tokens INTEGER DEFAULT 0,
                api_calls INTEGER DEFAULT 0,
                crawler_cost_usd DECIMAL(12, 4) DEFAULT 0,
                crawler_cost_cny DECIMAL(12, 4) DEFAULT 0,
                ai_cost_cny DECIMAL(12, 4) DEFAULT 0,
                total_cost_cny DECIMAL(12, 4) DEFAULT 0,
                pricing_json TEXT,
                detail_json TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_usage_events_created_at ON usage_events (created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_usage_events_user_id ON usage_events (user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_usage_events_module ON usage_events (module)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_usage_events_source ON usage_events (source)")
        logger.info("✅ 已确认 usage_events 消耗明细表存在")
    except Exception as exc:
        logger.warning("⚠️ 无法创建 usage_events 表: %s", exc)


def record_usage_event(
    module,
    user_id=None,
    username=None,
    department=None,
    task_id=None,
    record_id=None,
    item_count=0,
    crawler_items=None,
    ai_tokens=0,
    api_calls=0,
    source="actual",
    detail=None,
    event_key=None,
    created_at=None,
):
    """Record a usage event. Returns the inserted/updated event id when possible."""
    try:
        user = _lookup_user(user_id) if user_id and (not username or not department) else {}
        username = username or user.get("username") or user.get("real_name") or "unknown"
        department = department or user.get("department") or "未知"
        item_count = _safe_int(item_count)
        crawler_items = item_count if crawler_items is None else _safe_int(crawler_items)
        ai_tokens = _safe_int(ai_tokens)
        api_calls = _safe_int(api_calls)
        crawler_cost_usd = _money(Decimal(crawler_items) * CRAWLER_USD_PER_1000 / Decimal(1000))
        crawler_cost_cny = _money(crawler_cost_usd * USD_TO_CNY)
        ai_cost_cny = _money(Decimal(ai_tokens) * AI_CNY_PER_1000 / Decimal(1000))
        total_cost_cny = _money(crawler_cost_cny + ai_cost_cny)
        pricing = {
            "crawler_usd_per_1000": str(CRAWLER_USD_PER_1000),
            "usd_to_cny": str(USD_TO_CNY),
            "ai_cny_per_1000_tokens": str(AI_CNY_PER_1000),
        }
        event_key = event_key or _event_key(module, source, task_id, record_id, detail)
        created_at = created_at or datetime.datetime.utcnow()
        row = db.execute_and_fetch_one(
            """
            INSERT INTO usage_events (
                event_key, source, module, task_id, record_id, user_id, username, department,
                item_count, crawler_items, ai_tokens, api_calls,
                crawler_cost_usd, crawler_cost_cny, ai_cost_cny, total_cost_cny,
                pricing_json, detail_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_key) DO UPDATE SET
                item_count = EXCLUDED.item_count,
                crawler_items = EXCLUDED.crawler_items,
                ai_tokens = EXCLUDED.ai_tokens,
                api_calls = EXCLUDED.api_calls,
                crawler_cost_usd = EXCLUDED.crawler_cost_usd,
                crawler_cost_cny = EXCLUDED.crawler_cost_cny,
                ai_cost_cny = EXCLUDED.ai_cost_cny,
                total_cost_cny = EXCLUDED.total_cost_cny,
                pricing_json = EXCLUDED.pricing_json,
                detail_json = EXCLUDED.detail_json
            RETURNING id
            """,
            (
                event_key,
                source,
                module,
                task_id,
                record_id,
                user_id,
                username,
                department,
                item_count,
                crawler_items,
                ai_tokens,
                api_calls,
                float(crawler_cost_usd),
                float(crawler_cost_cny),
                float(ai_cost_cny),
                float(total_cost_cny),
                json.dumps(pricing, ensure_ascii=False),
                json.dumps(detail or {}, ensure_ascii=False, default=str),
                created_at,
            ),
        )
        logger.info(
            "💰 usage_event module=%s source=%s items=%s crawler_items=%s tokens=%s cost=%.4f",
            module,
            source,
            item_count,
            crawler_items,
            ai_tokens,
            float(total_cost_cny),
        )
        return row.get("id") if row else None
    except Exception as exc:
        logger.error("❌ 记录 usage_event 失败: %s", exc)
        return None


def get_usage_summary(user_id=None, include_estimated=True, month=None):
    where = []
    params = []
    if user_id:
        where.append("user_id = %s")
        params.append(user_id)
    if not include_estimated:
        where.append("source = 'actual'")
    if month:
        where.append("TO_CHAR(created_at, 'YYYY-MM') = %s")
        params.append(month)
    sql_where = "WHERE " + " AND ".join(where) if where else ""
    rows = db.query_all(
        f"""
        SELECT module, source,
               COUNT(*) AS events,
               COALESCE(SUM(item_count), 0) AS item_count,
               COALESCE(SUM(crawler_items), 0) AS crawler_items,
               COALESCE(SUM(ai_tokens), 0) AS ai_tokens,
               COALESCE(SUM(api_calls), 0) AS api_calls,
               COALESCE(SUM(crawler_cost_usd), 0) AS crawler_cost_usd,
               COALESCE(SUM(crawler_cost_cny), 0) AS crawler_cost_cny,
               COALESCE(SUM(ai_cost_cny), 0) AS ai_cost_cny,
               COALESCE(SUM(total_cost_cny), 0) AS total_cost_cny
        FROM usage_events
        {sql_where}
        GROUP BY module, source
        ORDER BY total_cost_cny DESC
        """,
        tuple(params),
    ) or []
    return [_row_to_json(row) for row in rows]


def get_usage_events(user_id=None, include_estimated=True, limit=100):
    where = []
    params = []
    if user_id:
        where.append("user_id = %s")
        params.append(user_id)
    if not include_estimated:
        where.append("source = 'actual'")
    sql_where = "WHERE " + " AND ".join(where) if where else ""
    params.append(max(1, min(int(limit or 100), 500)))
    rows = db.query_all(
        f"""
        SELECT * FROM usage_events
        {sql_where}
        ORDER BY created_at DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    ) or []
    return [_row_to_json(row) for row in rows]


def estimate_history(limit=200):
    """Return historical estimates without writing them.

    Existing usage_events/usage_logs are actual-ish records. Other rows are inferred from
    analysis_results/task_queue payloads and marked as estimated.
    """
    estimates = []
    estimates.extend(_estimate_from_usage_logs(limit))
    estimates.extend(_estimate_from_analysis_results(limit))
    estimates.extend(_estimate_from_task_queue(limit))
    estimates.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return estimates[: max(1, min(int(limit or 200), 1000))]


def _estimate_from_usage_logs(limit):
    rows = db.query_all(
        """
        SELECT l.*, u.real_name
        FROM usage_logs l
        LEFT JOIN users u ON u.id = l.user_id
        ORDER BY l.created_at DESC
        LIMIT %s
        """,
        (max(1, min(int(limit or 200), 1000)),),
    ) or []
    out = []
    for row in rows:
        items = _safe_int(row.get("comments_count"))
        tokens = _safe_int(row.get("ai_tokens"))
        out.append(_estimate_payload(
            module=row.get("function_type") or "legacy_usage",
            source="recorded_legacy",
            user_id=row.get("user_id"),
            username=row.get("username") or row.get("real_name"),
            department=row.get("department"),
            item_count=items,
            crawler_items=items,
            ai_tokens=tokens,
            created_at=row.get("created_at"),
            detail={"basis": "usage_logs", "legacy_total_cost_cny": float(row.get("total_cost") or 0)},
        ))
    return out


def _estimate_from_analysis_results(limit):
    rows = db.query_all(
        """
        SELECT r.id, r.user_id, r.type, r.result_json, r.created_at, u.username, u.real_name, u.department
        FROM analysis_results r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.result_json IS NOT NULL
        ORDER BY r.created_at DESC
        LIMIT %s
        """,
        (max(1, min(int(limit or 200), 1000)),),
    ) or []
    out = []
    for row in rows:
        payload = _loads(row.get("result_json"))
        if not payload:
            continue
        module = row.get("type") or "analysis"
        items = 0
        detail = {"basis": "analysis_results.result_json", "record_id": row.get("id")}
        if module == "competitor":
            if isinstance(payload, dict):
                items = _safe_int(payload.get("total_videos"))
                if not items and isinstance(payload.get("cleaned"), list):
                    items = len(payload.get("cleaned") or [])
            elif isinstance(payload, list):
                items = len(payload)
        elif module == "sentiment":
            items = len(payload) if isinstance(payload, list) else 0
        if items:
            out.append(_estimate_payload(
                module=module,
                source="estimated",
                user_id=row.get("user_id"),
                username=row.get("username") or row.get("real_name"),
                department=row.get("department"),
                record_id=row.get("id"),
                item_count=items,
                crawler_items=items,
                created_at=row.get("created_at"),
                detail=detail,
            ))
    return out


def _estimate_from_task_queue(limit):
    rows = db.query_all(
        """
        SELECT q.task_id, q.user_id, q.function_type, q.result, q.task_params, q.created_at,
               u.username, u.real_name, u.department
        FROM task_queue q
        LEFT JOIN users u ON u.id = q.user_id
        WHERE q.status = 'completed'
        ORDER BY q.created_at DESC
        LIMIT %s
        """,
        (max(1, min(int(limit or 200), 1000)),),
    ) or []
    out = []
    for row in rows:
        result = _loads(row.get("result")) or {}
        params = _loads(row.get("task_params")) or {}
        module = row.get("function_type") or "task"
        items = 0
        crawler_items = 0
        api_calls = 0
        if module == "etl_hashtag":
            items = _safe_int(result.get("row_count"))
            crawler_items = items
        elif module == "etl_comments":
            items = _safe_int(result.get("comment_rows"))
            crawler_items = items
        elif module == "etl_video_metrics":
            items = _safe_int(result.get("url_count") or result.get("success_count"))
            crawler_items = items
        elif module == "tiktok_official_refresh":
            items = _safe_int(result.get("videos"))
            crawler_items = 0
            api_calls = _safe_int(result.get("accounts")) * 2
        elif module in ("fb_scrape", "thai_scrape"):
            items = _safe_int(params.get("results_limit"))
            crawler_items = items
        if items or api_calls:
            out.append(_estimate_payload(
                module=module,
                source="estimated",
                user_id=row.get("user_id"),
                username=row.get("username") or row.get("real_name"),
                department=row.get("department"),
                task_id=row.get("task_id"),
                item_count=items,
                crawler_items=crawler_items,
                api_calls=api_calls,
                created_at=row.get("created_at"),
                detail={"basis": "task_queue", "result": result, "params_summary": _summarize_params(params)},
            ))
    return out


def _estimate_payload(module, source, user_id=None, username=None, department=None, task_id=None, record_id=None,
                      item_count=0, crawler_items=0, ai_tokens=0, api_calls=0, created_at=None, detail=None):
    crawler_cost_usd = _money(Decimal(_safe_int(crawler_items)) * CRAWLER_USD_PER_1000 / Decimal(1000))
    crawler_cost_cny = _money(crawler_cost_usd * USD_TO_CNY)
    ai_cost_cny = _money(Decimal(_safe_int(ai_tokens)) * AI_CNY_PER_1000 / Decimal(1000))
    return {
        "module": module,
        "source": source,
        "user_id": user_id,
        "username": username or "unknown",
        "department": department or "未知",
        "task_id": task_id,
        "record_id": record_id,
        "item_count": _safe_int(item_count),
        "crawler_items": _safe_int(crawler_items),
        "ai_tokens": _safe_int(ai_tokens),
        "api_calls": _safe_int(api_calls),
        "crawler_cost_usd": float(crawler_cost_usd),
        "crawler_cost_cny": float(crawler_cost_cny),
        "ai_cost_cny": float(ai_cost_cny),
        "total_cost_cny": float(_money(crawler_cost_cny + ai_cost_cny)),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "detail": detail or {},
        "pricing": {
            "crawler": "USD 3 / 1000 crawler rows",
            "ai": "CNY 0.008 / 1000 tokens",
            "usd_to_cny": float(USD_TO_CNY),
        },
    }


def _lookup_user(user_id):
    try:
        return db.query_one("SELECT username, real_name, department FROM users WHERE id = %s", (user_id,)) or {}
    except Exception:
        return {}


def _event_key(module, source, task_id, record_id, detail):
    if task_id:
        return "%s:%s:%s" % (source, module, task_id)
    if record_id:
        return "%s:%s:record:%s" % (source, module, record_id)
    raw = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True, default=str)[:80]
    return "%s:%s:%s:%s" % (source, module, datetime.datetime.utcnow().isoformat(), abs(hash(raw)))


def _safe_int(value):
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except Exception:
        return 0


def _money(value):
    return Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _loads(value):
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def _summarize_params(params):
    if not isinstance(params, dict):
        return {}
    keep = {}
    for key in ("urls", "post_urls", "seed_tags", "platforms", "results_limit", "max_posts", "max_ai_comments", "business_ids"):
        value = params.get(key)
        if isinstance(value, list):
            keep[key] = {"count": len(value), "sample": value[:3]}
        elif value not in (None, ""):
            keep[key] = value
    return keep


def _row_to_json(row):
    out = dict(row)
    for key, value in list(out.items()):
        if isinstance(value, Decimal):
            out[key] = float(value)
        elif hasattr(value, "isoformat"):
            out[key] = value.isoformat()
    for key in ("pricing_json", "detail_json"):
        if key in out:
            out[key.replace("_json", "")] = _loads(out.pop(key)) or {}
    return out
