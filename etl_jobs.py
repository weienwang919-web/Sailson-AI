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
import usage_service

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
    result_payload = {
        "download_id": rid,
        "filename": "hashtag_posts.xlsx",
        "row_count": len(raw_rows),
    }
    usage_service.record_usage_event(
        module="etl_hashtag",
        user_id=user_id,
        task_id=task_id,
        item_count=len(raw_rows),
        crawler_items=len(raw_rows),
        source="actual",
        detail={"seed_tags": seed_tags, "platforms": platforms, "days_back": days_back, "max_posts": max_posts},
    )
    update_task_fn(
        task_id,
        status="completed",
        progress="完成",
        result=json.dumps(result_payload, ensure_ascii=False),
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
    result_payload = {
        "download_id": rid,
        "filename": "comments_export.xlsx",
        "comment_rows": len(rows),
    }
    usage_service.record_usage_event(
        module="etl_comments",
        user_id=user_id,
        task_id=task_id,
        item_count=len(rows),
        crawler_items=len(rows),
        source="actual",
        detail={"post_url_count": len(urls), "days_back": days_back, "results_limit": results_limit},
    )
    update_task_fn(
        task_id,
        status="completed",
        progress="完成",
        result=json.dumps(result_payload, ensure_ascii=False),
    )


def run_etl_video_metrics_task(task_id: str, params: dict, update_task_fn) -> None:
    """
    params: input_file_id, url_column, manual_urls, selected_fields, user_id
    """
    if params.get("mode") == "profile_videos":
        return run_etl_profile_video_export_task(task_id, params, update_task_fn)

    user_id = params.get("user_id")
    input_file_id = params.get("input_file_id")
    url_column = (params.get("url_column") or "").strip() or None
    resolved_url_column = (params.get("resolved_url_column") or "").strip() or None
    sheet_name = params.get("sheet_name")
    header_row = params.get("header_row")
    selected_fields = params.get("selected_fields") or ["views", "likes", "comments"]
    urls = params.get("urls") or []
    excel_urls = params.get("excel_urls") or []
    manual_urls = params.get("manual_urls") or []
    extra_manual_urls = params.get("extra_manual_urls")
    file_bytes = None

    if input_file_id:
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
        if file_bytes is not None and not excel_urls:
            parsed = etl_tools.parse_excel_urls(file_bytes, url_column)
            excel_urls = parsed.urls
            resolved_url_column = parsed.url_column
            sheet_name = parsed.sheet_name
            header_row = parsed.header_row
            if extra_manual_urls is None and manual_urls:
                excel_keys = {video_metrics_etl.normalize_url(u) for u in excel_urls}
                extra_manual_urls = [
                    u for u in manual_urls
                    if video_metrics_etl.normalize_url(u) not in excel_keys
                ]
    except Exception as e:
        update_task_fn(task_id, status="failed", error=f"解析链接失败: {e}")
        return

    if extra_manual_urls is None:
        extra_manual_urls = []

    if not urls:
        deduped_urls = []
        seen = set()
        for raw_url in [*excel_urls, *manual_urls]:
            key = video_metrics_etl.normalize_url(raw_url)
            if key and key not in seen:
                seen.add(key)
                deduped_urls.append(raw_url)
        urls = deduped_urls

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
        if file_bytes is not None:
            xbytes = video_metrics_etl.merge_metrics_into_excel(
                file_bytes,
                url_column,
                metrics_map,
                selected_fields,
                sheet_name=sheet_name,
                header_row=header_row,
                resolved_url_column=resolved_url_column,
                extra_urls=extra_manual_urls,
            )
        else:
            xbytes = video_metrics_etl.build_manual_metrics_excel(
                urls,
                metrics_map,
                selected_fields,
            )
    except Exception as e:
        update_task_fn(task_id, status="failed", error=f"写回 Excel 失败: {e}")
        return

    ok_count = 0
    for raw_url in urls:
        key = video_metrics_etl.normalize_url(raw_url)
        metrics = metrics_map.get(key) or metrics_map.get(raw_url)
        if metrics and not metrics.get("_error"):
            ok_count += 1
    out_row = db.execute_and_fetch_one(
        """
        INSERT INTO etl_file_outputs (task_id, user_id, filename, content)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (task_id, user_id, "video_metrics.xlsx", xbytes),
    )
    rid = out_row["id"] if out_row else None
    result_payload = {
        "download_id": rid,
        "filename": "video_metrics.xlsx",
        "url_count": len(urls),
        "success_count": ok_count,
    }
    usage_service.record_usage_event(
        module="etl_video_metrics",
        user_id=user_id,
        task_id=task_id,
        item_count=len(urls),
        crawler_items=len(urls),
        source="actual",
        detail={
            "success_count": ok_count,
            "selected_fields": selected_fields,
            "excel_url_count": len(excel_urls),
            "manual_url_count": len(manual_urls),
        },
    )
    update_task_fn(
        task_id,
        status="completed",
        progress="完成",
        result=json.dumps(result_payload, ensure_ascii=False),
    )


def run_etl_profile_video_export_task(task_id: str, params: dict, update_task_fn) -> None:
    """
    params: profile_urls, start_date, end_date, max_videos, hashtag_enabled, hashtag, user_id
    """
    user_id = params.get("user_id")
    profile_urls = params.get("profile_urls") or params.get("urls") or []
    start_date = (params.get("start_date") or "")[:10] or None
    end_date = (params.get("end_date") or "")[:10] or None
    max_videos = int(params.get("max_videos") or 100)
    raw_hashtag_enabled = params.get("hashtag_enabled")
    hashtag_enabled = (
        raw_hashtag_enabled
        if isinstance(raw_hashtag_enabled, bool)
        else str(raw_hashtag_enabled or "").strip().lower() in {"1", "true", "yes", "on"}
    )
    hashtag_terms = video_metrics_etl.parse_hashtag_terms(params.get("hashtag") or params.get("hashtags") or [])

    if not profile_urls:
        update_task_fn(task_id, status="failed", error="未解析到有效主页链接")
        return
    if not start_date or not end_date:
        update_task_fn(task_id, status="failed", error="请选择开始与结束日期")
        return
    if start_date > end_date:
        update_task_fn(task_id, status="failed", error="开始日期不能晚于结束日期")
        return
    if hashtag_enabled and not hashtag_terms:
        update_task_fn(task_id, status="failed", error="启用 hashtag 匹配时请填写 hashtag")
        return

    apify_token = tasks.APIFY_TOKEN
    if not apify_token:
        update_task_fn(task_id, status="failed", error="Apify 未配置")
        return

    def hook(msg):
        update_task_fn(task_id, progress=msg)

    update_task_fn(
        task_id,
        progress=f"共 {len(profile_urls)} 个主页，抓取 {start_date} 至 {end_date} 的视频数据...",
    )
    try:
        rows = video_metrics_etl.fetch_profile_video_metrics(
            profile_urls,
            apify_token,
            start_date=start_date,
            end_date=end_date,
            max_videos=max_videos,
            progress_hook=hook,
        )
    except Exception as e:
        update_task_fn(task_id, status="failed", error=str(e)[:500])
        return

    fetched_count = len([row for row in rows if not row.get("_error")])
    if hashtag_enabled:
        update_task_fn(task_id, progress="正在按 hashtag 过滤视频标题...")
        rows = video_metrics_etl.filter_profile_video_rows_by_hashtag(rows, hashtag_terms)

    update_task_fn(task_id, progress="正在生成 Excel...")
    try:
        xbytes = video_metrics_etl.build_profile_video_export_excel(rows)
    except Exception as e:
        update_task_fn(task_id, status="failed", error=f"生成 Excel 失败: {e}")
        return

    out_row = db.execute_and_fetch_one(
        """
        INSERT INTO etl_file_outputs (task_id, user_id, filename, content)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (task_id, user_id, "profile_video_metrics.xlsx", xbytes),
    )
    rid = out_row["id"] if out_row else None
    result_payload = {
        "download_id": rid,
        "filename": "profile_video_metrics.xlsx",
        "mode": "profile_videos",
        "profile_count": len(profile_urls),
        "video_count": len(rows),
        "success_count": len(rows),
        "fetched_count": fetched_count,
        "start_date": start_date,
        "end_date": end_date,
        "hashtag_enabled": hashtag_enabled,
        "hashtags": hashtag_terms,
    }
    usage_service.record_usage_event(
        module="etl_video_metrics",
        user_id=user_id,
        task_id=task_id,
        item_count=len(rows),
        crawler_items=fetched_count,
        source="actual",
        detail={
            "mode": "profile_videos",
            "profile_count": len(profile_urls),
            "max_videos": max_videos,
            "start_date": start_date,
            "end_date": end_date,
            "hashtag_enabled": hashtag_enabled,
            "hashtags": hashtag_terms,
        },
    )
    update_task_fn(
        task_id,
        status="completed",
        progress="完成",
        result=json.dumps(result_payload, ensure_ascii=False),
    )
