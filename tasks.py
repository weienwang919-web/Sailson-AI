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
import time
import requests
from apify_client import ApifyClient
from openai import OpenAI
import database as db
import rag

logger = logging.getLogger(__name__)

APIFY_TOKEN = os.environ.get('APIFY_TOKEN')
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
QWEN_BASE_URL = os.environ.get('QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')

# Initialize clients
apify_client = ApifyClient(APIFY_TOKEN) if APIFY_TOKEN else None
qwen_client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=QWEN_BASE_URL) if DASHSCOPE_API_KEY else None


def scrape_fb_comments(post_urls=None, days_back=7, task_id=None):
    """
    Scrape FB comments from configured post URLs

    Args:
        post_urls: List of FB post URLs to scrape. If None, fetch from database config
        days_back: Only process comments from last N days
        task_id: Optional task ID for status tracking

    Returns:
        dict with status and stats
    """
    logger.info(f"🔄 Starting FB comment scraping task (days_back={days_back}, task_id={task_id})")

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

    if not post_urls:
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
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days_back)

    for post_url in post_urls:
        try:
            logger.info(f"📥 Scraping comments from: {post_url}")

            # Run Apify actor using REST API (same as analysis.py)
            run_input = {
                "startUrls": [{"url": post_url}],
                "maxComments": 500,
                "maxReplies": 0
            }

            logger.info("🚀 Starting Apify actor via REST API...")
            api_url = "https://api.apify.com/v2/acts/apify~facebook-comments-scraper/runs"
            headers = {
                "Authorization": f"Bearer {APIFY_TOKEN}",
                "Content-Type": "application/json"
            }

            # 启动 actor
            response = requests.post(
                api_url,
                json=run_input,
                headers=headers,
                timeout=30
            )

            if response.status_code != 201:
                logger.error(f"❌ Apify API error: {response.status_code}, {response.text}")
                continue

            run = response.json()['data']
            run_id = run['id']
            logger.info(f"✅ Apify actor started, Run ID: {run_id}")

            # 轮询任务状态
            logger.info("⏳ Polling for completion...")
            start_time = time.time()
            max_wait_time = 300  # 5 分钟
            poll_interval = 5

            status_api_url = f"https://api.apify.com/v2/actor-runs/{run_id}"

            while True:
                elapsed = time.time() - start_time
                if elapsed > max_wait_time:
                    logger.error(f"❌ Timeout waiting for actor to complete")
                    break

                time.sleep(poll_interval)

                status_response = requests.get(status_api_url, headers=headers, timeout=10)
                if status_response.status_code != 200:
                    logger.error(f"❌ Failed to get status: {status_response.status_code}")
                    break

                run_data = status_response.json()['data']
                status = run_data['status']

                logger.info(f"   Status: {status} (elapsed: {elapsed:.0f}s)")

                if status in ['SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT']:
                    run = run_data
                    break

            if run['status'] != 'SUCCEEDED':
                logger.error(f"❌ Actor failed with status: {run['status']}")
                continue

            # 获取结果
            dataset_id = run.get("defaultDatasetId")
            if not dataset_id:
                logger.error(f"❌ No dataset ID found")
                continue

            logger.info(f"📦 Fetching results from dataset: {dataset_id}")
            dataset_api_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
            dataset_response = requests.get(dataset_api_url, headers=headers, timeout=30)

            if dataset_response.status_code != 200:
                logger.error(f"❌ Failed to fetch dataset: {dataset_response.status_code}")
                continue

            items = dataset_response.json()
            logger.info(f"✅ Apify returned {len(items)} items")

            # Check for unusually low item count
            if len(items) < 10:
                logger.warning(f"⚠️ Unusually low item count: {len(items)}, expected more")

            # Batch deduplication: query all existing comment_ids at once
            comment_ids = [item.get('id') for item in items if item.get('id')]
            existing_ids = set()

            if comment_ids:
                placeholders = ','.join(['%s'] * len(comment_ids))
                sql = f"SELECT comment_id FROM fb_comments WHERE comment_id IN ({placeholders})"
                rows = db.query_all(sql, tuple(comment_ids))
                existing_ids = {row['comment_id'] for row in rows}
                logger.info(f"📊 Found {len(existing_ids)} existing comments out of {len(comment_ids)}")

            # Process each comment
            for item in items:
                comment_id = item.get('id')
                author = item.get('author', {}).get('name', 'Unknown')
                content = item.get('text', '')
                created_at_str = item.get('createdTime')

                if not content or not comment_id:
                    continue

                # Parse timestamp
                try:
                    created_at = datetime.datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                except:
                    created_at = datetime.datetime.now()

                # Skip old comments
                if created_at < cutoff_date:
                    continue

                # Memory-based deduplication (fast)
                if comment_id in existing_ids:
                    total_updated += 1
                    continue

                # Analyze sentiment using Qwen
                sentiment_score, category, language = analyze_comment_sentiment(content)

                # Generate embedding
                embedding = rag.get_embedding(content)
                embedding_json = json.dumps(embedding) if embedding else None

                # Insert into database
                db.execute(
                    """
                    INSERT INTO fb_comments
                    (post_url, comment_id, author, created_at, content, sentiment_score,
                     category, language, post_link, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (post_url, comment_id, author, created_at, content, sentiment_score,
                     category, language, post_url, embedding_json)
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

    logger.info(f"✅ FB scraping complete: {total_new} new, {total_updated} existing")

    result = {
        "status": "success",
        "new_comments": total_new,
        "existing_comments": total_updated
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
        tuple: (sentiment_score, category, language)
    """
    if not qwen_client:
        return 0.0, "unknown", "unknown"

    try:
        prompt = f"""分析以下评论的情感倾向、内容分类和语言。

评论内容：
{content}

请以JSON格式返回：
{{
  "sentiment_score": <-1到1之间的浮点数，-1最负面，1最正面>,
  "category": "<内容分类，必须是以下之一：外挂作弊/游戏优化/游戏Bug/充值退款/新模式地图平衡性建议/其他>",
  "language": "<语言代码，如：zh/en/id/th/vi>"
}}

分类说明：
1. 外挂作弊 - hackers, cheating, 作弊相关
2. 游戏优化 - lag, crashes, 卡顿、闪退、性能问题
3. 游戏Bug - glitches, errors, 游戏错误、异常
4. 充值退款 - payment issues, 充值、退款、支付问题
5. 新模式地图平衡性建议 - new content requests, 新内容、平衡性建议
6. 其他 - spam, praise, 其他内容"""

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
            result.get("language", "unknown")
        )

    except Exception as e:
        logger.error(f"❌ Sentiment analysis failed: {e}")
        return 0.0, "unknown", "unknown"


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

