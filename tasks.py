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


def scrape_fb_comments(post_urls=None, days_back=7):
    """
    Scrape FB comments from configured post URLs

    Args:
        post_urls: List of FB post URLs to scrape. If None, fetch from database config
        days_back: Only process comments from last N days

    Returns:
        dict with status and stats
    """
    logger.info(f"🔄 Starting FB comment scraping task (days_back={days_back})")

    if not apify_client:
        logger.error("❌ Apify client not initialized")
        return {"status": "error", "message": "Apify not configured"}

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
        return {"status": "error", "message": "No URLs to scrape"}

    total_new = 0
    total_updated = 0
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days_back)

    for post_url in post_urls:
        try:
            logger.info(f"📥 Scraping comments from: {post_url}")

            # Run Apify actor for FB comments
            run_input = {
                "startUrls": [{"url": post_url}],
                "maxComments": 500,
                "maxReplies": 0
            }

            # 启动 Apify actor 并等待完成（设置超时）
            logger.info(f"🚀 Starting Apify actor...")
            run = apify_client.actor("apify/facebook-comments-scraper").call(
                run_input=run_input,
                timeout_secs=300  # 5分钟超时
            )

            logger.info(f"✅ Apify actor completed, fetching results...")

            # Fetch results
            items = []
            for item in apify_client.dataset(run["defaultDatasetId"]).iterate_items():
                items.append(item)

            logger.info(f"✅ Scraped {len(items)} comments from {post_url}")

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

                # Check if comment already exists
                existing = db.query_one("SELECT id FROM fb_comments WHERE comment_id = %s", (comment_id,))

                if existing:
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
    return {
        "status": "success",
        "new_comments": total_new,
        "existing_comments": total_updated
    }


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
  "category": "<内容分类，如：产品反馈/客服咨询/游戏体验/社交互动/其他>",
  "language": "<语言代码，如：zh/en/id/th/vi>"
}}"""

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

