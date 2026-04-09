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
import re
from urllib.parse import urlparse
from apify_client import ApifyClient
from openai import OpenAI
import database as db
import rag

try:
    from langdetect import detect, DetectorFactory
    from langdetect.lang_detect_exception import LangDetectException
    DetectorFactory.seed = 0  # 线程安全，保证结果稳定
    _LANGDETECT_AVAILABLE = True
except ImportError:
    _LANGDETECT_AVAILABLE = False

_THAI_UNICODE_RE = re.compile(r'[\u0E00-\u0E7F]')

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
FB_COMMENTS_ACTOR_TIMEOUT_SECS = int(os.environ.get("FB_COMMENTS_ACTOR_TIMEOUT_SECS", "420"))
IG_COMMENTS_ACTOR_TIMEOUT_SECS = int(os.environ.get("IG_COMMENTS_ACTOR_TIMEOUT_SECS", "180"))


def _is_thai_content(text):
    """判断文本是否为泰语内容。
    优先用 langdetect，对短文本或检测失败时降级用 Unicode 范围判断。
    """
    if not text:
        return False
    # 短文本（<15字符）直接靠泰文字符比例判断
    if len(text.strip()) < 15:
        thai_chars = len(_THAI_UNICODE_RE.findall(text))
        return thai_chars >= 2
    # 优先用 langdetect
    if _LANGDETECT_AVAILABLE:
        try:
            lang = detect(text)
            return lang == 'th'
        except LangDetectException:
            pass
    # 降级：泰文字符占比 > 15%
    thai_chars = len(_THAI_UNICODE_RE.findall(text))
    return thai_chars / max(len(text), 1) > 0.15


def _has_thai_chars(text: str) -> bool:
    """只要文本含有任意泰文 Unicode 字符（U+0E00–U+0E7F）即返回 True。
    比 _is_thai_content 更宽松：有泰语就算，不要求主语言为泰语。
    """
    if not text:
        return False
    return bool(_THAI_UNICODE_RE.search(text))


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


def _normalize_tag_token(term):
    t = (term or "").strip().strip('"').strip("'").lower()
    return t


def _contains_any_tag_or_term(text, terms, hashtags=None):
    """OR 匹配：优先 hashtag 精确命中；未命中时降级 caption 词边界匹配。"""
    base = (text or "").lower()
    hashtag_set = {
        str(h or "").strip().lstrip("#").lower()
        for h in (hashtags or [])
        if str(h or "").strip()
    }
    for raw in (terms or []):
        t = _normalize_tag_token(raw)
        if not t:
            continue
        wildcard = t.endswith("*")
        t_core = (t[:-1] if wildcard else t).strip()
        if not t_core:
            continue
        t_no_hash = t_core.lstrip("#")

        # 1) hashtag 精确优先（或前缀匹配）
        if wildcard:
            if t_no_hash and any(h.startswith(t_no_hash) for h in hashtag_set):
                return True
        else:
            if t_no_hash and t_no_hash in hashtag_set:
                return True

        # 2) caption 降级（词边界），避免纯子串误命中
        if wildcard:
            if t_no_hash and re.search(rf'(?<!\w){re.escape(t_no_hash)}\w*', base):
                return True
        else:
            if t_no_hash and re.search(rf'(?<!\w){re.escape(t_no_hash)}(?!\w)', base):
                return True
    return False


def _expand_seed_terms(raw_values):
    """支持把布尔表达式原文展开为 discover 可用词项。"""
    operators = {"AND", "OR", "NOT"}
    expanded = []
    seen = set()

    token_pattern = re.compile(r'"([^"]+)"|([^\s()]+)')

    for raw in _normalize_list_input(raw_values):
        # 允许直接输入布尔规则原文
        for match in token_pattern.finditer(raw):
            token = (match.group(1) or match.group(2) or "").strip()
            if not token:
                continue
            upper = token.upper()
            if upper in operators:
                continue
            if token in ("(", ")"):
                continue

            # 清理布尔规则中常见修饰
            cleaned = token.strip().strip('"').strip("'")
            cleaned = cleaned.replace("*", "")
            cleaned = cleaned.lstrip("#")
            cleaned = cleaned.strip()
            if not cleaned:
                continue

            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            expanded.append(cleaned)

    return expanded


def _tokenize_boolean_expr(expr):
    token_re = re.compile(r'"[^"]*"|\(|\)|\bAND\b|\bOR\b|\bNOT\b|[^\s()]+', flags=re.IGNORECASE)
    return token_re.findall(expr or "")


def _boolean_to_rpn(tokens):
    precedence = {"NOT": 3, "AND": 2, "OR": 1}
    output = []
    ops = []
    for token in tokens:
        upper = token.upper()
        if upper in precedence:
            while ops and ops[-1] != "(" and precedence.get(ops[-1], 0) >= precedence[upper]:
                output.append(ops.pop())
            ops.append(upper)
        elif token == "(":
            ops.append(token)
        elif token == ")":
            while ops and ops[-1] != "(":
                output.append(ops.pop())
            if ops and ops[-1] == "(":
                ops.pop()
        else:
            output.append(token)
    while ops:
        output.append(ops.pop())
    return output


def _normalize_rule_term(term):
    value = (term or "").strip()
    if not value:
        return ""
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1].strip()
    return value.lower().strip()


def _match_boolean_term(text, raw_term):
    term = _normalize_rule_term(raw_term)
    if not term:
        return False
    if term.endswith('*'):
        prefix = term[:-1]
        return bool(prefix) and (prefix in text)
    return term in text


def _eval_boolean_expr(text, expr):
    tokens = _tokenize_boolean_expr(expr)
    if not tokens:
        return False
    rpn = _boolean_to_rpn(tokens)
    stack = []
    for token in rpn:
        if token in {"AND", "OR", "NOT"}:
            if token == "NOT":
                value = stack.pop() if stack else False
                stack.append(not value)
            else:
                right = stack.pop() if stack else False
                left = stack.pop() if stack else False
                stack.append(left and right if token == "AND" else left or right)
        else:
            stack.append(_match_boolean_term(text, token))
    return bool(stack[-1]) if stack else False


def _extract_include_terms_from_rule(expr):
    """从布尔规则中提取正向词项（忽略 NOT 分支词项）。"""
    tokens = _tokenize_boolean_expr(expr)
    includes = []
    seen = set()
    negate_depth = 0
    pending_not = False
    depth = 0

    for token in tokens:
        upper = token.upper()
        if upper == "NOT":
            pending_not = True
            continue
        if token == "(":
            depth += 1
            if pending_not:
                negate_depth = max(negate_depth, depth)
                pending_not = False
            continue
        if token == ")":
            if negate_depth and depth <= negate_depth:
                negate_depth = 0
            depth = max(0, depth - 1)
            continue
        if upper in {"AND", "OR"}:
            pending_not = False
            continue

        is_negated = pending_not or (negate_depth > 0 and depth >= negate_depth)
        pending_not = False
        if is_negated:
            continue

        term = _normalize_rule_term(token).replace("*", "")
        term = term.lstrip("#").strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        includes.append(term)

    return includes


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


def _safe_int(value, default=0):
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d-]", "", value)
            if not cleaned:
                return default
            return int(cleaned)
        return int(value)
    except Exception:
        return default


def _extract_post_metrics(item, platform, fallback_url=None):
    if not isinstance(item, dict):
        return None
    post_url = _extract_post_url(item) or fallback_url
    if not post_url:
        return None

    author = ""
    raw_author = item.get("author")
    if isinstance(raw_author, dict):
        author = raw_author.get("name") or raw_author.get("username") or ""
    elif isinstance(raw_author, str):
        author = raw_author.strip()
    if not author:
        author = (item.get("ownerUsername") or item.get("username") or item.get("userName") or "").strip()

    text_fields = [
        item.get("text"), item.get("caption"), item.get("description"),
        item.get("message"), item.get("title"), item.get("content")
    ]
    post_content = ""
    for text in text_fields:
        if isinstance(text, str) and text.strip():
            post_content = text.strip()
            break

    created_raw = (
        item.get("createdTime") or item.get("created_at") or item.get("createdAt")
        or item.get("timestamp") or item.get("time") or item.get("postDate")
    )
    post_date = _parse_created_at(created_raw).strftime("%Y-%m-%d")

    likes = _safe_int(
        item.get("likes") or item.get("likeCount") or item.get("likesCount")
        or item.get("reactionCount") or item.get("edge_liked_by", {}).get("count")
    )
    comments_count = _safe_int(
        item.get("commentsCount") or item.get("commentCount") or item.get("comments")
        or item.get("edge_media_to_comment", {}).get("count")
    )
    shares = _safe_int(item.get("shares") or item.get("shareCount") or item.get("sharesCount"))
    views = _safe_int(item.get("views") or item.get("viewCount") or item.get("videoViewCount"))
    engagement = likes + comments_count + shares

    thumbnail_url = ""
    for key in ("thumbnailUrl", "thumbnail_url", "displayUrl", "imageUrl", "image_url"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            thumbnail_url = val.strip()
            break

    return {
        "post_url": post_url,
        "platform": platform.upper()[:16],
        "author": author[:256],
        "post_date": post_date,
        "post_content": post_content[:2000],
        "thumbnail_url": thumbnail_url[:1024],
        "views": views,
        "shares": shares,
        "likes": likes,
        "comments_count": comments_count,
        "engagement": engagement
    }


def upsert_post_metrics(rows):
    if not rows:
        return 0
    upserted = 0
    for row in rows:
        try:
            db.execute(
                """
                INSERT INTO fb_post_metrics (
                    post_url, platform, author, post_date, post_content, thumbnail_url,
                    views, shares, likes, comments_count, engagement, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (post_url) DO UPDATE SET
                    platform = EXCLUDED.platform,
                    author = COALESCE(NULLIF(EXCLUDED.author, ''), fb_post_metrics.author),
                    post_date = COALESCE(EXCLUDED.post_date, fb_post_metrics.post_date),
                    post_content = COALESCE(NULLIF(EXCLUDED.post_content, ''), fb_post_metrics.post_content),
                    thumbnail_url = COALESCE(NULLIF(EXCLUDED.thumbnail_url, ''), fb_post_metrics.thumbnail_url),
                    views = GREATEST(COALESCE(fb_post_metrics.views, 0), COALESCE(EXCLUDED.views, 0)),
                    shares = GREATEST(COALESCE(fb_post_metrics.shares, 0), COALESCE(EXCLUDED.shares, 0)),
                    likes = GREATEST(COALESCE(fb_post_metrics.likes, 0), COALESCE(EXCLUDED.likes, 0)),
                    comments_count = GREATEST(COALESCE(fb_post_metrics.comments_count, 0), COALESCE(EXCLUDED.comments_count, 0)),
                    engagement = GREATEST(COALESCE(fb_post_metrics.engagement, 0), COALESCE(EXCLUDED.engagement, 0)),
                    updated_at = NOW()
                """,
                (
                    row.get("post_url"), row.get("platform"), row.get("author"), row.get("post_date"),
                    row.get("post_content"), row.get("thumbnail_url"), row.get("views"), row.get("shares"),
                    row.get("likes"), row.get("comments_count"), row.get("engagement")
                )
            )
            upserted += 1
        except Exception as e:
            logger.warning(f"⚠️ upsert fb_post_metrics 失败: {e}")
    return upserted


def _extract_discover_text(item):
    if not isinstance(item, dict):
        return ""
    parts = []
    candidate_keys = [
        "text", "caption", "description", "title", "content",
        "message", "postText", "post_text", "postCaption"
    ]
    for key in candidate_keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    hashtags = item.get("hashtags")
    if isinstance(hashtags, list):
        parts.extend([str(x).strip() for x in hashtags if str(x).strip()])
    return " ".join(parts).lower().strip()


def _extract_discover_text_for_lang(item):
    """与 _extract_discover_text 相同字段，保留大小写供语言检测。"""
    if not isinstance(item, dict):
        return ""
    parts = []
    candidate_keys = [
        "text", "caption", "description", "title", "content",
        "message", "postText", "post_text", "postCaption"
    ]
    for key in candidate_keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    hashtags = item.get("hashtags")
    if isinstance(hashtags, list):
        parts.extend([str(x).strip() for x in hashtags if str(x).strip()])
    return " ".join(parts).strip()


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


def discover_posts_by_tags(seed_tags, platforms=None, days_back=7, max_posts=200, boolean_rule=None, post_language_filter=None):
    """按标签/关键词发现帖子 URL（按平台调用 hashtag actors）。

    post_language_filter: 若为 'th'，仅保留帖文/话题合并后能通过泰语检测的条目（无正文则不过滤）。
    """
    if not apify_client:
        return {"status": "error", "message": "Apify not configured", "post_urls": []}

    include_terms = _extract_include_terms_from_rule(boolean_rule) if boolean_rule else []
    merged_raw_terms = []
    seen_term = set()
    for term in include_terms + _expand_seed_terms(seed_tags):
        key = term.lower()
        if key in seen_term:
            continue
        seen_term.add(key)
        merged_raw_terms.append(term)

    tags = merged_raw_terms
    if not tags:
        return {"status": "error", "message": "seed_tags is empty after boolean parsing", "post_urls": []}

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
    raw_terms = [str(t).strip() for t in tags if str(t).strip()]
    if not raw_terms:
        return {"status": "error", "message": "seed_tags is empty", "post_urls": []}

    # FB actor: keywordList 仅允许字母数字/Unicode/下划线（不允许 #、空格、标点）
    fb_keyword_pattern = re.compile(r"^[a-zA-Z0-9\u0080-\uFFFF_]+$")
    cleaned_terms = []
    invalid_terms = []
    for term in raw_terms:
        cleaned = re.sub(r"[^a-zA-Z0-9\u0080-\uFFFF_]+", "", term.lstrip("#"))
        if cleaned and fb_keyword_pattern.match(cleaned):
            cleaned_terms.append(cleaned)
        else:
            invalid_terms.append(term)
    if invalid_terms:
        return {
            "status": "error",
            "message": f"invalid seed_tags: {', '.join(invalid_terms[:10])}",
            "post_urls": []
        }

    # 保持顺序去重：所有输入都必须可转换且有效
    fb_keyword_list = []
    seen_cleaned = set()
    for term in cleaned_terms:
        key = term.lower()
        if key in seen_cleaned:
            continue
        seen_cleaned.add(key)
        fb_keyword_list.append(term)
    if not fb_keyword_list:
        return {"status": "error", "message": "seed_tags sanitized to empty", "post_urls": []}

    # IG actor: hashtags 至少 1 个，且每项不能包含空格或常见标点
    ig_hashtag_pattern = re.compile(r"^[^!?.,:;\-+=*&%$#@/\~^|<>()\[\]{}\"'`\s]+$")
    ig_hashtags = []
    for term in fb_keyword_list:
        if ig_hashtag_pattern.match(term):
            ig_hashtags.append(term)
        else:
            return {
                "status": "error",
                "message": f"invalid instagram hashtag after sanitize: {term}",
                "post_urls": []
            }
    if not ig_hashtags:
        return {"status": "error", "message": "instagram hashtags is empty after sanitize", "post_urls": []}

    urls = []
    posts = []
    seen = set()
    actor_runs = []
    errors = []

    for platform in target_platforms:
        actor_id = actor_map.get(platform)
        if not actor_id:
            continue

        # 不同 actor 的输入 schema 不同，按候选输入依次尝试
        if platform == "facebook":
            candidate_inputs = [
                {
                    "keywordList": fb_keyword_list,
                    "resultsLimit": per_platform_limit,
                    "startDate": start_dt.date().isoformat(),
                    "endDate": end_dt.date().isoformat()
                },
                {
                    "keywordList": fb_keyword_list,
                    "maxItems": per_platform_limit,
                    "startDate": start_dt.date().isoformat(),
                    "endDate": end_dt.date().isoformat()
                }
            ]
        else:
            candidate_inputs = [
                {
                    "hashtags": ig_hashtags,
                    "resultsLimit": per_platform_limit,
                    "startDate": start_dt.date().isoformat(),
                    "endDate": end_dt.date().isoformat()
                },
                {
                    "hashtags": ig_hashtags,
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
                if boolean_rule:
                    text = _extract_discover_text(item)
                    if text and (not _eval_boolean_expr(text, boolean_rule)):
                        continue
                if post_language_filter == 'th':
                    lang_text = _extract_discover_text_for_lang(item)
                    if lang_text and not _is_thai_content(lang_text):
                        continue
                url = _extract_post_url(item)
                if not url:
                    continue
                key = url.strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                urls.append(url)
                post_metrics = _extract_post_metrics(item, platform, fallback_url=url)
                if post_metrics:
                    posts.append(post_metrics)
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
        "post_urls": urls,
        "posts": posts
    }


def scrape_fb_comments(
    post_urls=None,
    discovered_posts=None,
    days_back=7,
    task_id=None,
    results_limit=2500,
    enable_ai_analysis=True,
    max_ai_comments=1200,
    allow_fallback_to_config=True,
    language_filter=None,
    dataset_name=None,
    min_comments_for_actor=None,
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
        language_filter: 语言过滤，'th' 表示只入库泰语内容（基于帖子正文）
        dataset_name: 数据集名称，入库时写入 thai_report_datasets 表
        min_comments_for_actor: 若为正整数，仅当 fb_post_metrics.comments_count 已存在且
            大于等于该值时才调用评论 Actor；用于跳过 0～2 条评论的帖子以省 Apify。

    Returns:
        dict with status and stats
    """
    logger.info(f"🔄 Starting social comment scraping task (days_back={days_back}, task_id={task_id}, language_filter={language_filter})")

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
    total_timeout_urls = 0
    total_skipped_language = 0
    total_skipped_low_comments = 0
    ai_processed_total = 0
    fb_posts_count = 0
    ins_posts_count = 0
    cutoff_date = datetime.datetime.now(BEIJING_TZ) - datetime.timedelta(days=days_back)

    # 先落 discover 阶段的帖子级指标，供报告口径使用
    discovered_count = upsert_post_metrics(discovered_posts or [])

    min_cm = None
    try:
        if min_comments_for_actor is not None:
            min_cm = int(min_comments_for_actor)
            if min_cm <= 0:
                min_cm = None
    except (TypeError, ValueError):
        min_cm = None

    url_comment_counts = {}
    if min_cm and post_urls:
        ph = ','.join(['%s'] * len(post_urls))
        try:
            cm_rows = db.query_all(
                f"SELECT post_url, comments_count FROM fb_post_metrics WHERE post_url IN ({ph})",
                tuple(post_urls),
            )
            url_comment_counts = {
                (r.get('post_url') or ''): int(r.get('comments_count') or 0) for r in (cm_rows or [])
            }
        except Exception as _e:
            logger.warning(f"⚠️ 批量读取 comments_count 失败，将不跳过低评论帖: {_e}")

    def _update_progress(msg):
        if task_id:
            try:
                db.execute(
                    "UPDATE scrape_tasks SET result_summary = %s WHERE id = %s",
                    (msg, task_id)
                )
            except Exception as _e:
                logger.debug(f"进度更新失败(task_id={task_id}): {_e}")

    total_posts = len(post_urls)
    for post_idx, post_url in enumerate(post_urls):
        # 每5条帖子更新一次进度
        if post_idx % 5 == 0 or post_idx == total_posts - 1:
            _update_progress(f"正在处理第 {post_idx + 1}/{total_posts} 条帖子，已入库评论 {total_new} 条...")

        try:
            platform = _detect_social_platform(post_url)
            if platform not in ("facebook", "instagram"):
                total_skipped_unsupported += 1
                logger.info(f"⏭️ Skip unsupported URL in social scraper: {post_url}")
                continue

            logger.info(f"📥 Scraping comments from: {post_url}")

            if min_cm:
                known = url_comment_counts.get(post_url)
                if known is not None and known < min_cm:
                    total_skipped_low_comments += 1
                    logger.info(f"⏭️ 评论数不足({known}<{min_cm})，跳过 Actor: {post_url[:80]}")
                    continue

            if platform == "facebook":
                actor_id = os.environ.get("APIFY_FB_COMMENTS_ACTOR_ID", DEFAULT_FB_COMMENTS_ACTOR).strip()
                actor_timeout_secs = FB_COMMENTS_ACTOR_TIMEOUT_SECS
                candidate_inputs = [{
                "includeNestedComments": True,
                    "resultsLimit": int(results_limit),
                "startUrls": [{"url": post_url}],
                "viewOption": "RANKED_UNFILTERED"
                }]
            else:
                actor_id = os.environ.get("APIFY_IG_COMMENTS_ACTOR_ID", DEFAULT_IG_COMMENTS_ACTOR).strip()
                actor_timeout_secs = IG_COMMENTS_ACTOR_TIMEOUT_SECS
                candidate_inputs = [
                    {"directUrls": [post_url], "resultsLimit": int(results_limit)},
                    {"postUrls": [post_url], "resultsLimit": int(results_limit)},
                    {"startUrls": [{"url": post_url}], "resultsLimit": int(results_limit)}
                ]

            if not actor_id:
                logger.error(f"❌ Missing actor id for platform={platform}")
                continue

            logger.info(f"🚀 Starting comments actor ({platform}): {actor_id}")
            items, _ = _run_actor_with_inputs(actor_id, candidate_inputs, timeout_secs=actor_timeout_secs)
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

            # 语言过滤：在帖子级别过滤（用 discover 阶段存储的 post_content 或 items 第一条内容判断）
            if language_filter == 'th':
                # 尝试从 items 里取帖子正文
                post_text = ''
                for it in items[:3]:
                    t = (it or {}).get('text') or (it or {}).get('caption') or (it or {}).get('description') or ''
                    if t:
                        post_text = t
                        break
                if not post_text:
                    # 降级：用评论正文的语言多数投票
                    sample_texts = [r['content'] for r in normalized_rows[:10] if r.get('content')]
                    post_text = ' '.join(sample_texts)
                if not _is_thai_content(post_text):
                    total_skipped_language += 1
                    logger.info(f"⏭️ 非泰语帖子跳过: {post_url[:60]}")
                    continue

            # 通过语言过滤后才计入平台统计（摘要数字准确）
            if platform == "facebook":
                fb_posts_count += 1
            else:
                ins_posts_count += 1

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
                if enable_ai_analysis and ai_processed_total < max_ai_comments:
                    sentiment_score, category, language, brief_analysis = analyze_comment_sentiment(content)
                    ai_processed_total += 1
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

            # 每处理完一个帖子，尽量补齐帖子级 comments_count/engagement（基于本次 actor 返回）
            try:
                post_comments_count = len(normalized_rows)
                post_likes = max([_safe_int((it or {}).get("likes") or (it or {}).get("likeCount")) for it in items] + [0])
                post_shares = max([_safe_int((it or {}).get("shares") or (it or {}).get("shareCount")) for it in items] + [0])
                db.execute(
                    """
                    INSERT INTO fb_post_metrics (post_url, platform, comments_count, likes, shares, engagement, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (post_url) DO UPDATE SET
                        comments_count = GREATEST(COALESCE(fb_post_metrics.comments_count, 0), COALESCE(EXCLUDED.comments_count, 0)),
                        likes = GREATEST(COALESCE(fb_post_metrics.likes, 0), COALESCE(EXCLUDED.likes, 0)),
                        shares = GREATEST(COALESCE(fb_post_metrics.shares, 0), COALESCE(EXCLUDED.shares, 0)),
                        engagement = GREATEST(
                            COALESCE(fb_post_metrics.engagement, 0),
                            COALESCE(EXCLUDED.likes, 0) + COALESCE(EXCLUDED.comments_count, 0) + COALESCE(EXCLUDED.shares, 0)
                        ),
                        updated_at = NOW()
                    """,
                    (
                        post_url,
                        platform.upper()[:16],
                        post_comments_count,
                        post_likes,
                        post_shares,
                        post_likes + post_comments_count + post_shares
                    )
                )
            except Exception as e:
                logger.warning(f"⚠️ 更新帖子级 metrics 失败: {e}")

            # 写入 thai_report_datasets 打标签
            if dataset_name:
                try:
                    db.execute(
                        """
                        INSERT INTO thai_report_datasets (dataset_name, post_url)
                        VALUES (%s, %s)
                        ON CONFLICT (dataset_name, post_url) DO NOTHING
                        """,
                        (dataset_name, post_url)
                    )
                except Exception as e:
                    logger.warning(f"⚠️ 写入 thai_report_datasets 失败: {e}")

        except Exception as e:
            err = str(e)
            if "timed out" in err.lower():
                total_timeout_urls += 1
                logger.warning(f"⏱️ Skip timeout URL: {post_url} | {err[:220]}")
            else:
                logger.error(f"❌ Error scraping {post_url}: {err}")
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

    total_posts_scraped = fb_posts_count + ins_posts_count
    result = {
        "status": "success",
        "new_comments": total_new,
        "existing_comments": total_updated,
        "skipped_non_facebook": total_skipped_unsupported,  # backward compatibility
        "skipped_unsupported_urls": total_skipped_unsupported,
        "skipped_language": total_skipped_language,
        "skipped_low_comment_posts": total_skipped_low_comments,
        "timed_out_urls": total_timeout_urls,
        "discovered_posts_upserted": discovered_count,
        "ai_processed_total": ai_processed_total,
        "enable_ai_analysis": enable_ai_analysis,
        "max_ai_comments": max_ai_comments,
        "fb_posts": fb_posts_count,
        "ins_posts": ins_posts_count,
    }

    # Update task status to completed
    if task_id:
        try:
            summary = (
                f"共抓取 {total_posts_scraped} 条帖子"
                f"（FB: {fb_posts_count}，INS: {ins_posts_count}），"
                f"入库评论 {total_new} 条，共分析 {ai_processed_total} 条评论"
            )
            if total_skipped_language:
                summary += f"，跳过非目标语言 {total_skipped_language} 条帖子"
            if total_skipped_low_comments:
                summary += f"，跳过低评论帖 {total_skipped_low_comments} 条"
            db.execute(
                "UPDATE scrape_tasks SET status = 'completed', completed_at = NOW(), result_summary = %s WHERE id = %s",
                (summary, task_id)
            )
        except Exception as e:
            logger.error(f"❌ Failed to update task status: {e}")

    return result


def run_thai_scrape_job(
    scrape_task_id,
    game_type,
    dataset_name,
    skip_discover,
    seed_tags,
    platforms,
    days_back,
    results_limit,
    max_ai_comments,
    discover_max_posts,
    min_comments_for_actor,
    boolean_rule=None,
    source_dataset_name=None,
    dataset_start=None,
    dataset_end=None,
    re_raise=True,
):
    """泰国专题：发现帖子并抓取评论。供 Web 线程或 DB Worker 调用。

    re_raise: Worker 模式应为 True，便于 task_queue 标记失败；进程内线程可传 False（与旧行为一致，异常仅写 scrape_tasks）。
    """
    try:
        db.execute("UPDATE scrape_tasks SET status='running' WHERE id=%s", (scrape_task_id,))
        if skip_discover:
            source_ds = source_dataset_name or dataset_name
            db.execute(
                "UPDATE scrape_tasks SET result_summary=%s WHERE id=%s",
                (f"正在加载数据集「{source_ds}」中的帖子（跳过 Hashtag 发现）...", scrape_task_id),
            )
            rows = db.query_all("""
                SELECT m.post_url, m.platform, m.author, m.post_date, m.post_content,
                       m.thumbnail_url, m.views, m.shares, m.likes, m.comments_count, m.engagement
                FROM fb_post_metrics m
                INNER JOIN thai_report_datasets d
                  ON d.post_url = m.post_url AND d.dataset_name = %s
                ORDER BY m.engagement DESC NULLS LAST
            """, (source_ds,))
            if not rows:
                raise RuntimeError(
                    f'数据集「{source_ds}」中暂无帖子。请先在本页导入 JSON，'
                    f'或取消勾选「仅抓评论」以运行 Hashtag 发现（会产生 Apify 费用）。'
                )
            seen_urls = set()
            post_urls = []
            discovered_posts = []
            filter_terms = _normalize_list_input(seed_tags)
            for r in rows:
                u = r.get('post_url')
                if not u or u in seen_urls:
                    continue
                row_post_date = str(r.get('post_date') or '')
                if dataset_start and row_post_date and row_post_date < str(dataset_start):
                    continue
                if dataset_end and row_post_date and row_post_date > str(dataset_end):
                    continue
                if filter_terms:
                    post_text = str(r.get('post_content') or '')
                    post_hashtags = re.findall(r'#([^\s#]+)', post_text.lower())
                    if not _contains_any_tag_or_term(post_text, filter_terms, hashtags=post_hashtags):
                        continue
                seen_urls.add(u)
                post_urls.append(u)
                discovered_posts.append({
                    'post_url': u,
                    'platform': r.get('platform'),
                    'author': r.get('author'),
                    'post_date': r.get('post_date'),
                    'post_content': r.get('post_content'),
                    'thumbnail_url': r.get('thumbnail_url'),
                    'views': r.get('views') or 0,
                    'shares': r.get('shares') or 0,
                    'likes': r.get('likes') or 0,
                    'comments_count': r.get('comments_count') or 0,
                    'engagement': r.get('engagement') or 0,
                })
            if len(post_urls) > discover_max_posts:
                post_urls = post_urls[:discover_max_posts]
                discovered_posts = discovered_posts[:discover_max_posts]
            db.execute(
                "UPDATE scrape_tasks SET result_summary=%s WHERE id=%s",
                (f"已加载 {len(post_urls)} 条本地帖子（未跑 Hashtag），开始抓取评论...", scrape_task_id),
            )
        else:
            db.execute(
                "UPDATE scrape_tasks SET result_summary=%s WHERE id=%s",
                (f"正在通过 Hashtag 发现 {game_type} 帖子...", scrape_task_id),
            )
            discover_result = discover_posts_by_tags(
                seed_tags=seed_tags,
                platforms=platforms,
                days_back=days_back,
                max_posts=discover_max_posts,
                boolean_rule=boolean_rule or None,
                post_language_filter='th',
            )
            if discover_result.get('status') != 'success':
                raise RuntimeError(f"discover 失败: {discover_result.get('message')}")

            post_urls = discover_result.get('post_urls') or []
            discovered_posts = discover_result.get('posts') or []
            db.execute(
                "UPDATE scrape_tasks SET result_summary=%s WHERE id=%s",
                (f"Hashtag 发现完成，共 {len(post_urls)} 条帖子，开始抓取评论...", scrape_task_id),
            )

        scrape_fb_comments(
            post_urls=post_urls,
            discovered_posts=discovered_posts,
            days_back=days_back,
            task_id=scrape_task_id,
            results_limit=results_limit,
            enable_ai_analysis=True,
            max_ai_comments=max_ai_comments,
            allow_fallback_to_config=False,
            language_filter='th',
            dataset_name=dataset_name,
            min_comments_for_actor=min_comments_for_actor or None,
        )
    except Exception as e:
        logger.error(f"❌ 泰国专题抓取失败(task_id={scrape_task_id}): {e}")
        try:
            db.execute(
                "UPDATE scrape_tasks SET status='failed', completed_at=NOW(), error_message=%s WHERE id=%s",
                (str(e)[:500], scrape_task_id),
            )
        except Exception:
            pass
        if re_raise:
            raise


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

