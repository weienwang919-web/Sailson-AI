"""
Background tasks for scheduled jobs
- FB comment scraping
- TikTok hotspot refresh
- Competitor analysis automation
"""
import os
import json
import logging
import datetime
from urllib.parse import urlparse
from apify_client import ApifyClient
from openai import OpenAI
import database as db
import rag

# 北京时区
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))

logger = logging.getLogger(__name__)

APIFY_TOKEN = os.environ.get('APIFY_TOKEN')
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
QWEN_BASE_URL = os.environ.get('QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')

# Initialize clients
apify_client = ApifyClient(APIFY_TOKEN) if APIFY_TOKEN else None
qwen_client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=QWEN_BASE_URL) if DASHSCOPE_API_KEY else None

DEFAULT_FB_HASHTAG_ACTOR = "apify/facebook-hashtag-scraper"
DEFAULT_IG_HASHTAG_ACTOR = "apify/instagram-hashtag-scraper"
DEFAULT_FB_COMMENTS_ACTOR = "apify/facebook-comments-scraper"
DEFAULT_IG_COMMENTS_ACTOR = "apify/instagram-comment-scraper"


def _normalize_list_input(values):
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    result = []
    seen = set()
    for val in values:
        if not isinstance(val, str):
            continue
        cleaned = val.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _is_facebook_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        if hostname == "fb.watch":
            return True
        if hostname == "facebook.com":
            return True
        return hostname.endswith(".facebook.com")
    except Exception:
        return False


def _is_instagram_url(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        if hostname == "instagram.com":
            return True
        return hostname.endswith(".instagram.com")
    except Exception:
        return False


def _detect_social_platform(url):
    if _is_facebook_url(url):
        return "facebook"
    if _is_instagram_url(url):
        return "instagram"
    return "unknown"


def _extract_post_url(item):
    if not isinstance(item, dict):
        return None
    candidate_keys = [
        "postUrl", "postURL", "post_url", "url", "link", "permalink",
        "canonicalUrl", "postLink"
    ]
    for key in candidate_keys:
        value = item.get(key)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.startswith("http://") or cleaned.startswith("https://"):
                return cleaned
    return None


def _parse_created_at(value):
    if value is None:
        return datetime.datetime.now(BEIJING_TZ)
    try:
        if isinstance(value, (int, float)):
            created = datetime.datetime.fromtimestamp(float(value), tz=datetime.timezone.utc)
            return created.astimezone(BEIJING_TZ)
        text = str(value).strip()
        if not text:
            return datetime.datetime.now(BEIJING_TZ)
        if text.isdigit():
            created = datetime.datetime.fromtimestamp(float(text), tz=datetime.timezone.utc)
            return created.astimezone(BEIJING_TZ)
        created = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        return created.astimezone(BEIJING_TZ)
    except Exception:
        return datetime.datetime.now(BEIJING_TZ)


def _extract_comment_fields(item, platform, default_post_url):
    if not isinstance(item, dict):
        return None

    raw_comment_id = (
        item.get('id') or item.get('commentId') or item.get('comment_id') or item.get('pk')
    )
    content = (
        item.get('text') or item.get('content') or item.get('comment') or item.get('message') or ""
    )
    created_at_raw = (
        item.get('createdTime') or item.get('created_at') or item.get('createdAt')
        or item.get('timestamp') or item.get('time')
    )

    author = "Unknown"
    raw_author = item.get('author')
    if isinstance(raw_author, dict):
        author = raw_author.get('name') or raw_author.get('username') or author
    elif isinstance(raw_author, str) and raw_author.strip():
        author = raw_author.strip()
    else:
        author = (
            item.get('ownerUsername') or item.get('username') or item.get('userName') or author
        )

    post_url = _extract_post_url(item) or default_post_url

    if not raw_comment_id or not str(raw_comment_id).strip() or not str(content).strip():
        return None

    comment_id = str(raw_comment_id).strip()
    # 为避免不同平台 comment_id 冲突，IG 加前缀
    if platform == "instagram" and not comment_id.startswith("ig:"):
        comment_id = f"ig:{comment_id}"

    return {
        "comment_id": comment_id,
        "author": str(author).strip() or "Unknown",
        "content": str(content).strip(),
        "created_at": _parse_created_at(created_at_raw),
        "post_url": post_url
    }


def _run_actor_with_inputs(actor_id, inputs, timeout_secs=600):
    last_error = None
    for run_input in inputs:
        try:
            run = apify_client.actor(actor_id).call(run_input=run_input, timeout_secs=timeout_secs)
            dataset_id = run.get("defaultDatasetId")
            if not dataset_id:
                raise RuntimeError("actor missing defaultDatasetId")
            items = list(apify_client.dataset(dataset_id).iterate_items())
            return items, run_input
        except Exception as e:
            last_error = str(e)
            logger.warning(f"⚠️ Actor call failed ({actor_id}) with one input: {e}")
            continue
    raise RuntimeError(last_error or "actor call failed")


def discover_posts_by_tags(seed_tags, platforms=None, days_back=7, max_posts=200):
    """按标签/关键词发现帖子 URL（按平台调用 hashtag actors）。"""
    if not apify_client:
        return {"status": "error", "message": "Apify not configured", "post_urls": []}

    tags = _normalize_list_input(seed_tags)
    if not tags:
        return {"status": "error", "message": "seed_tags is empty", "post_urls": []}

    target_platforms = [p.lower() for p in _normalize_list_input(platforms)] or ["facebook", "instagram"]
    max_posts = max(20, min(int(max_posts), 5000))
    days_back = max(1, min(int(days_back), 60))

    actor_map = {
        "facebook": os.environ.get("APIFY_FB_HASHTAG_ACTOR_ID", DEFAULT_FB_HASHTAG_ACTOR).strip(),
        "instagram": os.environ.get("APIFY_IG_HASHTAG_ACTOR_ID", DEFAULT_IG_HASHTAG_ACTOR).strip(),
    }

    end_dt = datetime.datetime.now(datetime.timezone.utc)
    start_dt = end_dt - datetime.timedelta(days=days_back)
    per_platform_limit = max(20, int(max_posts / max(len(target_platforms), 1)))
    hashtags = [t.lstrip("#") for t in tags if t.strip()]

    urls = []
    seen = set()
    actor_runs = []
    errors = []

    for platform in target_platforms:
        actor_id = actor_map.get(platform)
        if not actor_id:
            continue

        # 不同 actor 的输入 schema 不同，按候选输入依次尝试
        candidate_inputs = [
            {
                "hashtags": hashtags,
                "resultsLimit": per_platform_limit,
                "startDate": start_dt.date().isoformat(),
                "endDate": end_dt.date().isoformat()
            },
            {
                "searchQueries": tags,
                "resultsLimit": per_platform_limit,
                "startDate": start_dt.date().isoformat(),
                "endDate": end_dt.date().isoformat()
            },
            {
                "searchTerms": tags,
                "maxItems": per_platform_limit,
                "startDate": start_dt.date().isoformat(),
                "endDate": end_dt.date().isoformat()
            }
        ]

        try:
            logger.info(f"🔎 Discover {platform} posts via actor: {actor_id}")
            items, used_input = _run_actor_with_inputs(actor_id, candidate_inputs, timeout_secs=600)
            actor_runs.append({"platform": platform, "actor_id": actor_id, "items": len(items)})
            for item in items:
                url = _extract_post_url(item)
                if not url:
                    continue
                key = url.strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                urls.append(url)
                if len(urls) >= max_posts:
                    break
            logger.info(f"✅ Discover {platform} done: {len(items)} items")
        except Exception as e:
            errors.append(f"{platform}:{e}")
            logger.warning(f"⚠️ Discover failed for {platform}: {e}")
            continue

        if len(urls) >= max_posts:
            break

    if not actor_runs and errors:
        return {
            "status": "error",
            "message": f"discover actor failed: {'; '.join(errors)[:500]}",
            "post_urls": []
        }

    logger.info(f"✅ Discover done: {len(urls)} URLs")
    return {
        "status": "success",
        "actor_runs": actor_runs,
        "total_urls": len(urls),
        "post_urls": urls
    }


def scrape_fb_comments(
    post_urls=None,
    days_back=7,
    task_id=None,
    results_limit=2500,
    enable_ai_analysis=True,
    max_ai_comments=1200,
    allow_fallback_to_config=True
):
    """
    Scrape FB/IG comments from post URLs

    Args:
        post_urls: List of FB post URLs to scrape. If None, fetch from database config
        days_back: Only process comments from last N days
        task_id: Optional task ID for status tracking
        results_limit: Apify actor maximum comments per post
        enable_ai_analysis: Whether to run sentiment/topic AI analysis
        max_ai_comments: Safety cap for number of comments to run AI analysis
        allow_fallback_to_config: If True, fallback to fb_monitor_config when post_urls is None

    Returns:
        dict with status and stats
    """
    logger.info(f"🔄 Starting social comment scraping task (days_back={days_back}, task_id={task_id})")

    # Update task status to running
    if task_id:
        try:
            db.execute("UPDATE scrape_tasks SET status = 'running' WHERE id = %s", (task_id,))
        except:
            pass

    if not apify_client:
        error_msg = "Apify not configured"
        logger.error(f"❌ {error_msg}")
        if task_id:
            try:
                db.execute(
                    "UPDATE scrape_tasks SET status = 'failed', completed_at = NOW(), error_message = %s WHERE id = %s",
                    (error_msg, task_id)
                )
            except:
                pass
        return {"status": "error", "message": error_msg}

    if post_urls is None and allow_fallback_to_config:
        # Fetch from database config
        try:
            rows = db.query_all("SELECT post_url FROM fb_monitor_config WHERE is_active = TRUE")
            post_urls = [row['post_url'] for row in rows]
        except Exception as e:
            logger.error(f"❌ Failed to fetch config from database: {e}")
            # Fallback to environment variable
            post_urls = os.environ.get('FB_POST_URLS', '').split(',')
            post_urls = [url.strip() for url in post_urls if url.strip()]

    if not post_urls:
        logger.warning("⚠️ No FB post URLs configured")
        error_msg = "No URLs to scrape"
        if task_id:
            try:
                db.execute(
                    "UPDATE scrape_tasks SET status = 'failed', completed_at = NOW(), error_message = %s WHERE id = %s",
                    (error_msg, task_id)
                )
            except:
                pass
        return {"status": "error", "message": error_msg}

    total_new = 0
    total_updated = 0
    total_skipped_unsupported = 0
    cutoff_date = datetime.datetime.now(BEIJING_TZ) - datetime.timedelta(days=days_back)

    for post_url in post_urls:
        try:
            platform = _detect_social_platform(post_url)
            if platform not in ("facebook", "instagram"):
                total_skipped_unsupported += 1
                logger.info(f"⏭️ Skip unsupported URL in social scraper: {post_url}")
                continue

            logger.info(f"📥 Scraping comments from: {post_url}")

            if platform == "facebook":
                actor_id = os.environ.get("APIFY_FB_COMMENTS_ACTOR_ID", DEFAULT_FB_COMMENTS_ACTOR).strip()
                candidate_inputs = [{
                    "includeNestedComments": True,
                    "resultsLimit": int(results_limit),
                    "startUrls": [{"url": post_url}],
                    "viewOption": "RANKED_UNFILTERED"
                }]
            else:
                actor_id = os.environ.get("APIFY_IG_COMMENTS_ACTOR_ID", DEFAULT_IG_COMMENTS_ACTOR).strip()
                candidate_inputs = [
                    {"directUrls": [post_url], "resultsLimit": int(results_limit)},
                    {"postUrls": [post_url], "resultsLimit": int(results_limit)},
                    {"startUrls": [{"url": post_url}], "resultsLimit": int(results_limit)}
                ]

            if not actor_id:
                logger.error(f"❌ Missing actor id for platform={platform}")
                continue

            logger.info(f"🚀 Starting comments actor ({platform}): {actor_id}")
            items, _ = _run_actor_with_inputs(actor_id, candidate_inputs, timeout_secs=600)
            logger.info(f"✅ Apify returned {len(items)} items")

            # Check for unusually low item count
            if len(items) < 10:
                logger.warning(f"⚠️ Unusually low item count: {len(items)}, expected more")

            # Batch deduplication: query all existing comment_ids at once
            normalized_rows = []
            comment_ids = []
            for item in items:
                parsed = _extract_comment_fields(item, platform, post_url)
                if not parsed:
                    continue
                normalized_rows.append(parsed)
                comment_ids.append(parsed["comment_id"])
            existing_ids = set()

            if comment_ids:
                placeholders = ','.join(['%s'] * len(comment_ids))
                sql = f"SELECT comment_id FROM fb_comments WHERE comment_id IN ({placeholders})"
                rows = db.query_all(sql, tuple(comment_ids))
                existing_ids = {row['comment_id'] for row in rows}
                logger.info(f"📊 Found {len(existing_ids)} existing comments out of {len(comment_ids)}")

            ai_processed = 0
            # Process each comment
            for row in normalized_rows:
                comment_id = row["comment_id"]
                author = row["author"]
                content = row["content"]
                created_at = row["created_at"]
                row_post_url = row["post_url"]

                # Skip old comments
                if created_at < cutoff_date:
                    continue

                # Memory-based deduplication (fast)
                if comment_id in existing_ids:
                    total_updated += 1
                    continue

                # Analyze sentiment using Qwen (with cap to control latency/cost)
                if enable_ai_analysis and ai_processed < max_ai_comments:
                    sentiment_score, category, language, brief_analysis = analyze_comment_sentiment(content)
                    ai_processed += 1
                else:
                    sentiment_score, category, language, brief_analysis = 0.0, "unknown", "unknown", ""

                # Generate embedding
                embedding = rag.get_embedding(content)
                embedding_json = json.dumps(embedding) if embedding else None

                # Insert into database
                db.execute(
                    """
                    INSERT INTO fb_comments
                    (post_url, comment_id, author, created_at, content, sentiment_score,
                     category, language, post_link, embedding, brief_analysis)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (row_post_url, comment_id, author, created_at, content, sentiment_score,
                     category, language, row_post_url, embedding_json, brief_analysis)
                )
                total_new += 1

        except Exception as e:
            logger.error(f"❌ Error scraping {post_url}: {e}")
            continue

    # Update last_scraped_at for all processed URLs
    for post_url in post_urls:
        try:
            db.execute(
                "UPDATE fb_monitor_config SET last_scraped_at = NOW() WHERE post_url = %s",
                (post_url,)
            )
        except:
            pass

    logger.info(f"✅ Social scraping complete: {total_new} new, {total_updated} existing")

    result = {
        "status": "success",
        "new_comments": total_new,
        "existing_comments": total_updated,
        "skipped_non_facebook": total_skipped_unsupported,  # backward compatibility
        "skipped_unsupported_urls": total_skipped_unsupported,
        "enable_ai_analysis": enable_ai_analysis,
        "max_ai_comments": max_ai_comments
    }

    # Update task status to completed
    if task_id:
        try:
            summary = f"New: {total_new}, Existing: {total_updated}"
            db.execute(
                "UPDATE scrape_tasks SET status = 'completed', completed_at = NOW(), result_summary = %s WHERE id = %s",
                (summary, task_id)
            )
        except Exception as e:
            logger.error(f"❌ Failed to update task status: {e}")

    return result


def analyze_comment_sentiment(content):
    """
    Analyze comment sentiment using Qwen

    Returns:
        tuple: (sentiment_score, category, language, brief_analysis)
    """
    if not qwen_client:
        return 0.0, "unknown", "unknown", ""

    try:
        # Count words to determine analysis depth
        word_count = len(content)

        prompt = f"""分析以下评论的情感倾向、内容分类、语言和简要分析。

评论内容：
{content}

请以JSON格式返回：
{{
  "sentiment_score": <-1到1之间的浮点数，-1最负面，1最正面>,
  "category": "<内容分类，必须是以下之一：外挂作弊/游戏优化/游戏Bug/充值退款/新模式地图平衡性建议/其他>",
  "language": "<语言代码，如：zh/en/id/th/vi>",
  "brief_analysis": "<简要分析>"
}}

分类说明：
1. 外挂作弊 - hackers, cheating, 作弊相关
2. 游戏优化 - lag, crashes, 卡顿、闪退、性能问题
3. 游戏Bug - glitches, errors, 游戏错误、异常
4. 充值退款 - payment issues, 充值、退款、支付问题
5. 新模式地图平衡性建议 - new content requests, 新内容、平衡性建议
6. 其他 - spam, praise, 其他内容

情感评分细则：
- 直接骂人、投诉、强烈不满：-0.6 到 -1.0
- 隐晦负面（反讽、阴阳怪气、失望、冷嘲热讽、无奈放弃）：-0.3 到 -0.6
- emoji 表达不满（如 💀🤡😤😡）：识别为负面
- 多语言注意：印尼语/越南语/泰语的俚语抱怨也需正确识别
- 中性咨询、提问：-0.1 到 0.1
- 正面评价、夸赞：0.4 到 1.0

简要分析要求：
- 短评论（原文 < 30 字）：一句话概括，15-20 个中文字
- 长评论（原文 ≥ 30 字）：详细解释，包含主要问题、玩家情绪、关键细节，50-100 个中文字"""

        response = qwen_client.chat.completions.create(
            model="qwen-plus",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )

        result_text = response.choices[0].message.content.strip()

        # Extract JSON
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0].strip()

        result = json.loads(result_text)

        return (
            float(result.get("sentiment_score", 0.0)),
            result.get("category", "unknown"),
            result.get("language", "unknown"),
            result.get("brief_analysis", "")
        )

    except Exception as e:
        logger.error(f"❌ Sentiment analysis failed: {e}")
        return 0.0, "unknown", "unknown", ""


def refresh_tiktok_hotspots(region='sea', top_n=50):
    """
    Refresh TikTok hotspots for specified region

    Args:
        region: Target region (default: 'sea' for Southeast Asia)
        top_n: Number of top hotspots to fetch

    Returns:
        dict with status and stats
    """
    logger.info(f"🔄 Starting TikTok hotspot refresh (region={region}, top_n={top_n})")

    if not apify_client:
        logger.error("❌ Apify client not initialized")
        return {"status": "error", "message": "Apify not configured"}

    try:
        # Use TikTok trending hashtags actor
        run_input = {
            "resultsPerPage": top_n,
            "region": region
        }

        # Note: Replace with actual TikTok trending actor ID
        # For now, using placeholder
        run = apify_client.actor("clockworks/tiktok-hashtag-scraper").call(run_input=run_input)

        items = []
        for item in apify_client.dataset(run["defaultDatasetId"]).iterate_items():
            items.append(item)

        logger.info(f"✅ Fetched {len(items)} hotspots")

        today = datetime.date.today()
        inserted = 0

        for item in items:
            hotspot_name = item.get('hashtag') or item.get('name', '')
            metric_score = item.get('views') or item.get('posts', 0)
            hotspot_type = item.get('type', 'hashtag')

            if not hotspot_name:
                continue

            # Check if already exists for today
            existing = db.query_one(
                "SELECT id FROM tiktok_hotspots WHERE hotspot_name = %s AND date = %s",
                (hotspot_name, today)
            )

            if existing:
                # Update metric
                db.execute(
                    "UPDATE tiktok_hotspots SET metric_score = %s WHERE id = %s",
                    (metric_score, existing['id'])
                )
            else:
                # Insert new
                db.execute(
                    """
                    INSERT INTO tiktok_hotspots (hotspot_name, type, metric_score, date)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (hotspot_name, hotspot_type, metric_score, today)
                )
                inserted += 1

        logger.info(f"✅ Hotspot refresh complete: {inserted} new entries")
        return {
            "status": "success",
            "new_hotspots": inserted,
            "total_fetched": len(items)
        }

    except Exception as e:
        logger.error(f"❌ Hotspot refresh failed: {e}")
        return {"status": "error", "message": str(e)}

