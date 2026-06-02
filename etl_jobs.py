"""
ETL 异步任务：Hashtag 发现导出、链接批量评论导出（供 worker 与 Web 后台线程共用）。
"""
import json
import logging
from datetime import date

import database as db
import etl_tools
import tasks
import video_metrics_etl

logger = logging.getLogger(__name__)


def run_etl_hashtag_task(task_id: str, params: dict, update_task_fn) -> None:
    """
    params: seed_tags, platforms, start_date, end_date, max_posts, user_id
    """
    user_id = params.get("user_id")
    seed_tags = params.get("seed_tags") or []
    platforms = params.get("platforms") or ["facebook", "instagram"]
    start_date = (params.get("start_date") or "")[:10]
    end_date = (params.get("end_date") or "")[:10]
    max_posts = int(params.get("max_posts") or 500)

    if not tasks.apify_client:
        update_task_fn(task_id, status="failed", error="Apify 未配置")
        return

    try:
        today = date.today()
        sd = date.fromisoformat(start_date)
        days_back = min(max((today - sd).days + 1, 1), 60)
    except Exception as e:
        update_task_fn(task_id, status="failed", error=f"日期无效: {e}")
        return

    update_task_fn(task_id, progress=f"正在调用 Apify 发现帖子（约 {days_back} 天窗口）...")

    discover_result = tasks.discover_posts_by_tags(
        seed_tags=seed_tags,
        platforms=platforms,
        days_back=days_back,
        max_posts=max_posts,
        post_language_filter=None,
    )

    if discover_result.get("status") != "success":
        update_task_fn(
            task_id,
            status="failed",
            error=discover_result.get("message", "discover failed")[:500],
        )
        return

    posts = discover_result.get("posts") or []
    raw_rows = []
    for p in posts:
        raw_rows.append(
            {
                "post_url": p.get("post_url"),
                "platform": p.get("platform"),
                "author": p.get("author"),
                "post_date": p.get("post_date"),
                "post_content": p.get("post_content"),
                "likes": p.get("likes"),
                "comments_count": p.get("comments_count"),
                "shares": p.get("shares"),
                "views": p.get("views"),
                "engagement": p.get("engagement"),
                "thumbnail_url": p.get("thumbnail_url"),
            }
        )

    update_task_fn(task_id, progress="正在生成 Excel...")
    xbytes = etl_tools.posts_metrics_to_excel_bytes(raw_rows, start_date, end_date)
    row = db.execute_and_fetch_one(
        """
        INSERT INTO etl_file_outputs (task_id, user_id, filename, content)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (task_id, user_id, "hashtag_posts.xlsx", xbytes),
    )
    rid = row["id"] if row else None
    update_task_fn(
        task_id,
        status="completed",
        progress="完成",
        result=json.dumps(
            {
                "download_id": rid,
                "filename": "hashtag_posts.xlsx",
                "row_count": len(raw_rows),
            },
            ensure_ascii=False,
        ),
    )


def run_etl_comments_task(task_id: str, params: dict, update_task_fn) -> None:
    """
    params: post_urls, results_limit, days_back, user_id
    """
    user_id = params.get("user_id")
    urls = params.get("post_urls") or []
    results_limit = int(params.get("results_limit") or 2500)
    days_back = int(params.get("days_back") or 365)

    if not urls:
        update_task_fn(task_id, status="failed", error="无有效链接")
        return

    if not tasks.apify_client:
        update_task_fn(task_id, status="failed", error="Apify 未配置")
        return

    def hook(msg):
        update_task_fn(task_id, progress=msg)

    r = tasks.scrape_fb_comments(
        post_urls=urls,
        discovered_posts=[],
        days_back=days_back,
        task_id=None,
        results_limit=results_limit,
        enable_ai_analysis=False,
        max_ai_comments=0,
        allow_fallback_to_config=False,
        language_filter=None,
        dataset_name=None,
        min_comments_for_actor=0,
        persist_to_db=False,
        progress_hook=hook,
    )

    if r.get("status") != "success":
        update_task_fn(task_id, status="failed", error=r.get("message", "scrape failed")[:500])
        return

    rows = r.get("export_rows") or []
    update_task_fn(task_id, progress="正在生成 Excel...")
    xbytes = etl_tools.comments_to_excel_bytes(rows)
    row = db.execute_and_fetch_one(
        """
        INSERT INTO etl_file_outputs (task_id, user_id, filename, content)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (task_id, user_id, "comments_export.xlsx", xbytes),
    )
    rid = row["id"] if row else None
    update_task_fn(
        task_id,
        status="completed",
        progress="完成",
        result=json.dumps(
            {
                "download_id": rid,
                "filename": "comments_export.xlsx",
                "comment_rows": len(rows),
            },
            ensure_ascii=False,
        ),
    )


def run_etl_video_metrics_task(task_id: str, params: dict, update_task_fn) -> None:
    """
    params: input_file_id, url_column, selected_fields, user_id
    """
    user_id = params.get("user_id")
    input_file_id = params.get("input_file_id")
    url_column = (params.get("url_column") or "").strip() or None
    resolved_url_column = (params.get("resolved_url_column") or "").strip() or None
    sheet_name = params.get("sheet_name")
    header_row = params.get("header_row")
    selected_fields = params.get("selected_fields") or ["views", "likes", "comments"]
    urls = params.get("urls") or []

    if not input_file_id:
        update_task_fn(task_id, status="failed", error="缺少输入文件")
        return

    row = db.query_one(
        "SELECT content, filename FROM etl_file_outputs WHERE id = %s AND user_id = %s",
        (input_file_id, user_id),
    )
    if not row or not row.get("content"):
        update_task_fn(task_id, status="failed", error="输入文件不存在或已过期")
        return

    file_bytes = row["content"]
    if isinstance(file_bytes, memoryview):
        file_bytes = file_bytes.tobytes()

    try:
        if not urls:
            parsed = etl_tools.parse_excel_urls(file_bytes, url_column)
            urls = parsed.urls
            resolved_url_column = parsed.url_column
            sheet_name = parsed.sheet_name
            header_row = parsed.header_row
    except Exception as e:
        update_task_fn(task_id, status="failed", error=f"解析链接失败: {e}")
        return

    if not urls:
        update_task_fn(task_id, status="failed", error="未解析到有效 http(s) 链接")
        return

    apify_token = tasks.APIFY_TOKEN
    if not apify_token:
        update_task_fn(task_id, status="failed", error="Apify 未配置")
        return

    def hook(msg):
        update_task_fn(task_id, progress=msg)

    update_task_fn(task_id, progress=f"共 {len(urls)} 条链接，开始调用 Apify...")
    try:
        metrics_map = video_metrics_etl.fetch_video_metrics(urls, apify_token, progress_hook=hook)
    except Exception as e:
        update_task_fn(task_id, status="failed", error=str(e)[:500])
        return

    update_task_fn(task_id, progress="正在写回 Excel...")
    try:
        xbytes = video_metrics_etl.merge_metrics_into_excel(
            file_bytes,
            url_column,
            metrics_map,
            selected_fields,
            sheet_name=sheet_name,
            header_row=header_row,
            resolved_url_column=resolved_url_column,
        )
    except Exception as e:
        update_task_fn(task_id, status="failed", error=f"写回 Excel 失败: {e}")
        return

    ok_count = sum(1 for m in metrics_map.values() if m and not m.get("_error"))
    out_row = db.execute_and_fetch_one(
        """
        INSERT INTO etl_file_outputs (task_id, user_id, filename, content)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (task_id, user_id, "video_metrics.xlsx", xbytes),
    )
    rid = out_row["id"] if out_row else None
    update_task_fn(
        task_id,
        status="completed",
        progress="完成",
        result=json.dumps(
            {
                "download_id": rid,
                "filename": "video_metrics.xlsx",
                "url_count": len(urls),
                "success_count": ok_count,
            },
            ensure_ascii=False,
        ),
    )
