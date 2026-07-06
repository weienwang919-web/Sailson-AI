"""
Excel 视频链接批量拉取基础指标（Apify），并写回原表。
"""
from __future__ import annotations

import io
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests

import competitor_radar as radar
import etl_tools
import tasks

logger = logging.getLogger(__name__)

BATCH_SIZE = int(os.environ.get("ETL_VIDEO_METRICS_BATCH_SIZE", "80"))
BATCH_WORKERS = max(1, int(os.environ.get("ETL_VIDEO_METRICS_BATCH_WORKERS", "2")))
SHORT_URL_TIMEOUT_SECS = int(os.environ.get("ETL_SHORT_URL_TIMEOUT_SECS", "4"))
SHORT_URL_WORKERS = max(1, int(os.environ.get("ETL_SHORT_URL_WORKERS", "16")))
VIDEO_METRICS_PROFILE_FALLBACK_ENABLED = os.environ.get(
    "ETL_VIDEO_METRICS_PROFILE_FALLBACK_ENABLED",
    "false",
).strip().lower() in {"1", "true", "yes", "on"}

METRIC_FIELDS: Dict[str, str] = {
    "views": "播放量",
    "likes": "点赞量",
    "comments": "评论量",
    "shares": "转发量",
    "collects": "收藏量",
    "engagement": "互动量",
    "followers": "主页粉丝数",
    "author": "作者",
    "post_date": "发布日期",
    "caption": "视频文案",
    "duration": "视频时长",
}
DEFAULT_VIDEO_METRIC_FIELDS = list(METRIC_FIELDS.keys())
MANUAL_URL_COLUMN = "视频链接"
MANUAL_PLATFORM_COLUMN = "平台"
PROFILE_VIDEO_EXPORT_COLUMNS = [
    "视频链接",
    "达人主页链接",
    "达人名称",
    "视频发布时间",
    "视频标题",
    "视频时长",
    "播放量",
    "点赞量",
    "评论量",
    "转发量",
    "收藏量",
    "抓取状态",
]

VIDEO_ACTORS = {
    "TT": os.environ.get("APIFY_TIKTOK_PROFILE_ACTOR_ID", "clockworks/tiktok-scraper"),
    "IG": os.environ.get("APIFY_INSTAGRAM_PROFILE_ACTOR_ID", "apify/instagram-scraper"),
    "YTB": os.environ.get("APIFY_YOUTUBE_SCRAPER_ACTOR_ID", "streamers/youtube-scraper"),
    "FB": os.environ.get("APIFY_FB_POSTS_ACTOR_ID", "apify/facebook-posts-scraper"),
}

_YT_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com")
_FB_HOSTS = ("facebook.com", "fb.com", "fb.watch", "m.facebook.com")
_TT_SHORT_HOSTS = {"vt.tiktok.com", "vm.tiktok.com"}
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}


def detect_platform(url: str) -> str:
    if not url:
        return "UNKNOWN"
    u = url.lower()
    if any(h in u for h in radar._TT_HOSTS):
        return "TT"
    if any(h in u for h in radar._IG_HOSTS):
        return "IG"
    if any(h in u for h in _YT_HOSTS):
        return "YTB"
    if any(h in u for h in _FB_HOSTS):
        return "FB"
    return "UNKNOWN"


def normalize_url(url: str) -> str:
    s = (url or "").strip()
    if not s or s.lower() == "nan":
        return ""
    if not s.lower().startswith("http"):
        if s.lower().startswith("www."):
            s = "https://" + s
        elif "tiktok.com" in s.lower():
            s = "https://" + s
        elif "instagram.com" in s.lower():
            s = "https://" + s
        elif "youtube.com" in s.lower() or "youtu.be" in s.lower():
            s = "https://" + s
        elif "facebook.com" in s.lower() or "fb.watch" in s.lower():
            s = "https://" + s
    try:
        p = urlparse(s)
        host = (p.netloc or "").lower()
        path = (p.path or "").rstrip("/")
        return f"{p.scheme.lower()}://{host}{path}"
    except Exception:
        return s.rstrip("/")


def parse_manual_urls(text: str) -> List[str]:
    """Parse pasted links, one per line, with lightweight cleanup and de-dupe."""
    urls: List[str] = []
    seen = set()
    for line in str(text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        # Allow accidental numbered/bulleted lists copied from docs or chats.
        raw = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", raw).strip()
        if not raw:
            continue
        match = re.search(r"(?:https?://|www\.)\S+", raw, re.I)
        value = match.group(0) if match else raw
        value = value.strip().strip("<>\"'，,。；;)")
        normalized_cell = etl_tools._normalize_url_cell(value)
        if not normalized_cell or not etl_tools._looks_like_url(normalized_cell):
            continue
        normalized = normalize_url(normalized_cell)
        if normalized and normalized not in seen:
            seen.add(normalized)
            urls.append(normalized_cell)
    return urls


def parse_hashtag_terms(value) -> List[str]:
    """Normalize one or many hashtag values into bare, de-duplicated terms."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_parts = []
        for item in value:
            raw_parts.extend(re.split(r"[\s,，;；\n\r]+", str(item or "")))
    else:
        raw_parts = re.split(r"[\s,，;；\n\r]+", str(value or ""))

    terms: List[str] = []
    seen = set()
    for part in raw_parts:
        token = part.strip().strip("\"'`").lstrip("#").strip()
        if not token:
            continue
        key = token.lower()
        if key not in seen:
            seen.add(key)
            terms.append(token)
    return terms


def _caption_matches_hashtags(caption: str, hashtag_terms: List[str]) -> bool:
    if not hashtag_terms:
        return True
    text = str(caption or "").lower()
    return any(f"#{term.lower()}" in text for term in hashtag_terms if term)


def filter_profile_video_rows_by_hashtag(rows: List[dict], hashtag_terms) -> List[dict]:
    """Keep only video rows whose title/caption contains one of the hashtags."""
    terms = parse_hashtag_terms(hashtag_terms)
    if not terms:
        return list(rows or [])
    return [
        row for row in (rows or [])
        if not row.get("_error") and _caption_matches_hashtags(row.get("caption") or "", terms)
    ]


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def _is_tiktok_short_url(url: str) -> bool:
    try:
        p = urlparse(normalize_url(url))
        host = (p.netloc or "").lower()
        path = (p.path or "").strip("/")
    except Exception:
        return False
    return host in _TT_SHORT_HOSTS or (host.endswith("tiktok.com") and path.startswith("t/"))


def _is_tiktok_post_url(url: str) -> bool:
    try:
        p = urlparse(normalize_url(url))
        host = (p.netloc or "").lower()
        path = (p.path or "").strip("/")
    except Exception:
        return False
    if "tiktok.com" not in host:
        return False
    return bool(re.search(r"^@[^/]+/(video|photo)/[^/]+", path, re.I))


def _resolve_redirect_url(url: str) -> str:
    """展开社媒短链。失败时返回空字符串，由调用方使用原链接兜底。"""
    if not url:
        return ""
    try:
        resp = requests.head(
            url,
            allow_redirects=True,
            timeout=SHORT_URL_TIMEOUT_SECS,
            headers=_HTTP_HEADERS,
        )
        resolved = normalize_url(resp.url)
        if _is_tiktok_post_url(resolved):
            return resolved
    except Exception as e:
        logger.info("短链 HEAD 展开失败，改用 GET: %s (%s)", url, e)

    try:
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=SHORT_URL_TIMEOUT_SECS,
            headers=_HTTP_HEADERS,
            stream=True,
        )
        try:
            resolved = normalize_url(resp.url)
            if _is_tiktok_post_url(resolved):
                return resolved
        finally:
            resp.close()
    except Exception as e:
        logger.warning("短链 GET 展开失败: %s (%s)", url, e)
    return ""


def _resolve_input_urls(urls: List[str]) -> tuple[List[str], Dict[str, str]]:
    """返回原始规范化 URL 列表，以及 raw_url -> actor_url 的映射。"""
    input_urls: List[str] = []
    resolved_by_input: Dict[str, str] = {}
    seen = set()
    short_urls: List[str] = []

    for raw in urls:
        u = normalize_url(raw)
        if not u or u in seen:
            continue
        seen.add(u)
        input_urls.append(u)
        if _is_tiktok_short_url(u):
            short_urls.append(u)
        else:
            resolved_by_input[u] = u

    if short_urls:
        workers = min(SHORT_URL_WORKERS, len(short_urls))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_by_url = {executor.submit(_resolve_redirect_url, u): u for u in short_urls}
            for future in as_completed(future_by_url):
                u = future_by_url[future]
                resolved = ""
                try:
                    resolved = future.result() or ""
                except Exception as e:
                    logger.warning("短链并发展开失败: %s (%s)", u, e)
                if resolved and _host(resolved) != _host(u):
                    logger.info("TikTok 短链已展开: %s -> %s", u, resolved)
                resolved_by_input[u] = resolved or u

    return input_urls, resolved_by_input


def _tiktok_username(url: str) -> str:
    match = re.search(r"tiktok\.com/@([^/?#]+)", url, re.I)
    return match.group(1).strip() if match else ""


def _instagram_username(url: str) -> str:
    try:
        first = urlparse(url).path.strip("/").split("/")[0]
    except Exception:
        return ""
    if first.lower() in {"p", "reel", "reels", "tv", "stories", "explore"}:
        return ""
    return first.strip()


def _profile_url_from_url(url: str, platform: str) -> str:
    """从视频/帖子链接推导主页链接；无法可靠推导时返回空。"""
    u = normalize_url(url)
    if not u:
        return ""
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        parts = [x for x in (p.path or "").strip("/").split("/") if x]
    except Exception:
        return ""
    if platform == "TT":
        user = _tiktok_username(u)
        return f"https://www.tiktok.com/@{user}" if user else ""
    if platform == "IG":
        user = _instagram_username(u)
        return f"https://www.instagram.com/{user}" if user else ""
    if platform == "YTB":
        if len(parts) >= 2 and parts[0] in {"@", "channel", "c", "user"}:
            return f"https://{host}/{'/'.join(parts[:2])}"
        if parts and parts[0].startswith("@"):
            return f"https://{host}/{parts[0]}"
        return ""
    if platform == "FB":
        if host in {"fb.watch", "www.fb.watch"}:
            return ""
        if parts and parts[0].lower() not in {"watch", "reel", "videos", "photo", "posts"}:
            return f"https://{host}/{parts[0]}"
    return ""


def _is_profile_url(platform: str, url: str) -> bool:
    """Return true when a URL points at an account/channel page, not a post/video."""
    u = normalize_url(url)
    if not u:
        return False
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        parts = [x for x in (p.path or "").strip("/").split("/") if x]
    except Exception:
        return False
    if platform == "TT":
        return bool(_tiktok_username(u)) and not any(part.lower() in {"video", "photo"} for part in parts)
    if platform == "IG":
        return bool(_instagram_username(u))
    if platform == "YTB":
        return bool(parts and (parts[0].startswith("@") or parts[0].lower() in {"channel", "c", "user"}))
    if platform == "FB":
        if host in {"fb.watch", "www.fb.watch"} or not parts:
            return False
        return parts[0].lower() not in {"watch", "reel", "videos", "photo", "photos", "posts", "share"}
    return False


def _item_url_keys(item: dict) -> List[str]:
    keys = (
        "webVideoUrl", "url", "postUrl", "postURL", "inputUrl", "videoUrl",
        "permalink", "link", "pageUrl", "facebookUrl",
    )
    out = []
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip().startswith("http"):
            out.append(normalize_url(v))
    if item.get("shortCode"):
        out.append(normalize_url(f"https://www.instagram.com/p/{item['shortCode']}/"))
    return out


def _item_raw_urls(item: dict) -> List[str]:
    keys = (
        "webVideoUrl", "url", "postUrl", "postURL", "inputUrl", "videoUrl",
        "permalink", "link", "pageUrl", "facebookUrl",
    )
    out = []
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip().startswith("http"):
            out.append(v.strip())
    if item.get("shortCode"):
        out.append(f"https://www.instagram.com/p/{item['shortCode']}/")
    return out


def _looks_like_video_url(platform: str, url: str) -> bool:
    try:
        p = urlparse(url)
        path = (p.path or "").lower()
        query = parse_qs(p.query or "")
    except Exception:
        return False
    if platform == "TT":
        return "/video/" in path or "/photo/" in path
    if platform == "IG":
        return path.startswith(("/p/", "/reel/", "/reels/", "/tv/"))
    if platform == "YTB":
        return "v" in query or path.startswith(("/watch", "/shorts/")) or "youtu.be" in (p.netloc or "")
    if platform == "FB":
        return any(x in path for x in ("/videos/", "/reel/", "/posts/")) or "fb.watch" in (p.netloc or "")
    return bool(url)


def _synthesized_item_url(item: dict, platform: str) -> str:
    if platform == "TT":
        author = item.get("authorMeta") or {}
        username = ""
        if isinstance(author, dict):
            username = author.get("name") or author.get("uniqueId") or author.get("nickname") or ""
        video_id = item.get("id") or item.get("videoId") or item.get("awemeId")
        if username and video_id:
            return f"https://www.tiktok.com/@{username}/video/{video_id}"
    if platform == "YTB":
        video_id = item.get("id") or item.get("videoId")
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def _canonical_profile_item_url(item: dict, platform: str) -> str:
    for raw in _item_raw_urls(item):
        if _looks_like_video_url(platform, raw):
            return raw.strip()
    return _synthesized_item_url(item, platform)


def video_key_from_url(url: str, platform: str = "") -> str:
    raw = (url or "").strip()
    plat = platform or detect_platform(raw)
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        host = (p.netloc or "").lower()
        path = (p.path or "").strip("/")
        query = parse_qs(p.query or "")
    except Exception:
        return f"{plat}:{normalize_url(raw)}"

    if plat == "TT":
        m = re.search(r"(?:video|photo)/(\d+)", path)
        if m:
            return f"TT:{m.group(1)}"
    if plat == "IG":
        parts = [x for x in path.split("/") if x]
        if len(parts) >= 2 and parts[0].lower() in {"p", "reel", "reels", "tv"}:
            return f"IG:{parts[1]}"
    if plat == "YTB":
        vid = (query.get("v") or [""])[0]
        if not vid and "youtu.be" in host:
            vid = path.split("/")[0] if path else ""
        if not vid and path.startswith("shorts/"):
            vid = path.split("/")[1] if len(path.split("/")) > 1 else ""
        if vid:
            return f"YTB:{vid}"
    if plat == "FB":
        m = re.search(r"(?:videos|reel)/(\d+)", path)
        if m:
            return f"FB:{m.group(1)}"
    return f"{plat}:{normalize_url(raw)}"


def _extract_followers(item: dict, platform: str) -> int:
    if platform == "TT":
        author_meta = item.get("authorMeta") or {}
        return tasks._safe_int(
            (author_meta.get("fans") if isinstance(author_meta, dict) else None)
            or (author_meta.get("followers") if isinstance(author_meta, dict) else None)
            or item.get("authorMeta.fans")
            or item.get("authorMeta.followers")
            or item.get("followers")
            or item.get("followersCount")
        )
    if platform == "IG":
        owner = item.get("owner") or {}
        return tasks._safe_int(
            item.get("followersCount")
            or item.get("ownerFollowersCount")
            or (owner.get("followers") if isinstance(owner, dict) else None)
            or (owner.get("followersCount") if isinstance(owner, dict) else None)
        )
    if platform == "YTB":
        return tasks._safe_int(
            item.get("numberOfSubscribers")
            or item.get("subscriberCount")
            or item.get("channelSubscriberCount")
            or item.get("subscribers")
        )
    if platform == "FB":
        return tasks._safe_int(
            item.get("pageFollowers")
            or item.get("followers")
            or item.get("followersCount")
            or item.get("pageLikes")
        )
    return tasks._safe_int(item.get("followers") or item.get("followersCount"))


def _followers_only_metrics(item: dict, platform: str) -> dict:
    followers = _extract_followers(item, platform)
    if followers:
        return {"followers": followers}
    return {}


def _extract_youtube(item: dict) -> dict:
    create_dt = radar._to_beijing_dt(
        item.get("date") or item.get("uploadDate") or item.get("publishedAt")
    )
    duration = item.get("duration") or item.get("durationText") or ""
    likes = tasks._safe_int(item.get("likes") or item.get("likeCount"))
    comments = tasks._safe_int(
        item.get("commentsCount") or item.get("numberOfComments") or item.get("commentCount")
    )
    views = tasks._safe_int(item.get("viewCount") or item.get("views"))
    shares = tasks._safe_int(item.get("shares") or item.get("shareCount"))
    followers = _extract_followers(item, "YTB")
    return {
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "collects": tasks._safe_int(item.get("favoriteCount") or item.get("collectCount")),
        "engagement": likes + comments + shares,
        "followers": followers,
        "author": (item.get("channelName") or item.get("channelUsername") or "").strip(),
        "post_date": create_dt.date().isoformat() if create_dt else "",
        "caption": (item.get("title") or item.get("description") or "").strip()[:2000],
        "duration": str(duration).strip(),
    }


def _extract_facebook(item: dict) -> dict:
    row = tasks._extract_post_metrics(item, "FB")
    if not row:
        return {}
    likes = row.get("likes") or 0
    comments = row.get("comments_count") or 0
    shares = row.get("shares") or 0
    followers = _extract_followers(item, "FB")
    return {
        "views": row.get("views") or 0,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "collects": 0,
        "engagement": likes + comments + shares,
        "followers": followers,
        "author": row.get("author") or "",
        "post_date": row.get("post_date") or "",
        "caption": (row.get("post_content") or "")[:2000],
        "duration": "",
    }


def _extract_metrics(item: dict, platform: str) -> dict:
    if platform == "TT":
        row = radar._extract_tiktok_video(item)
        followers = _extract_followers(item, "TT")
        return {
            "views": row.get("views") or 0,
            "likes": row.get("likes") or 0,
            "comments": row.get("comments") or 0,
            "shares": row.get("shares") or 0,
            "collects": row.get("collects") or 0,
            "engagement": (row.get("likes") or 0) + (row.get("comments") or 0) + (row.get("shares") or 0),
            "followers": followers,
            "author": row.get("author") or "",
            "post_date": row.get("post_date") or "",
            "caption": (row.get("caption") or "")[:2000],
            "duration": row.get("duration") or "",
        }
    if platform == "IG":
        row = radar._extract_instagram_video(item)
        if not row:
            row = {
                "views": tasks._safe_int(item.get("videoPlayCount") or item.get("videoViewCount")),
                "likes": tasks._safe_int(item.get("likesCount") or item.get("likes")),
                "comments": tasks._safe_int(item.get("commentsCount") or item.get("comments")),
                "shares": 0,
                "collects": 0,
                "followers": _extract_followers(item, "IG"),
                "author": item.get("ownerUsername") or item.get("username") or "",
                "post_date": "",
                "caption": (item.get("caption") or item.get("text") or "")[:2000],
                "duration": "",
            }
        return {
            "views": row.get("views") or 0,
            "likes": row.get("likes") or 0,
            "comments": row.get("comments") or 0,
            "shares": row.get("shares") or 0,
            "collects": row.get("collects") or 0,
            "engagement": (row.get("likes") or 0) + (row.get("comments") or 0),
            "followers": row.get("followers") or _extract_followers(item, "IG"),
            "author": row.get("author") or "",
            "post_date": row.get("post_date") or "",
            "caption": (row.get("caption") or "")[:2000],
            "duration": row.get("duration") or "",
        }
    if platform == "YTB":
        return _extract_youtube(item)
    if platform == "FB":
        return _extract_facebook(item)
    row = tasks._extract_post_metrics(item, platform)
    if not row:
        return {}
    likes = row.get("likes") or 0
    comments = row.get("comments_count") or 0
    shares = row.get("shares") or 0
    return {
        "views": row.get("views") or 0,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "collects": 0,
        "engagement": likes + comments + shares,
        "followers": _extract_followers(item, platform),
        "author": row.get("author") or "",
        "post_date": row.get("post_date") or "",
        "caption": (row.get("post_content") or "")[:2000],
        "duration": "",
    }


def _actor_inputs(platform: str, urls: List[str]) -> List[dict]:
    if platform == "TT":
        return [
            {
                "postURLs": urls,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
                "shouldDownloadSubtitles": False,
            },
            {"postUrls": urls},
            {"startUrls": [{"url": u} for u in urls]},
        ]
    if platform == "IG":
        return [
            {"directUrls": urls, "resultsType": "posts", "resultsLimit": max(len(urls), 1)},
            {"postUrls": urls, "resultsLimit": max(len(urls), 1)},
            {"startUrls": [{"url": u} for u in urls], "resultsLimit": max(len(urls), 1)},
        ]
    if platform == "YTB":
        return [
            {"startUrls": [{"url": u} for u in urls], "maxResults": max(len(urls), 1)},
            {"videoUrls": urls, "maxResults": max(len(urls), 1)},
        ]
    if platform == "FB":
        return [
            {"startUrls": [{"url": u} for u in urls], "maxPosts": max(len(urls), 1)},
            {"startUrls": urls, "resultsLimit": max(len(urls), 1)},
        ]
    return [{"startUrls": [{"url": u} for u in urls]}]


def _profile_actor_inputs(platform: str, profile_url: str, results_limit: int = 20) -> List[dict]:
    return _profile_actor_inputs_batch(platform, [profile_url], results_limit)


def _profile_actor_inputs_batch(platform: str, profile_urls: List[str], results_limit: int = 20) -> List[dict]:
    limit = max(1, int(results_limit or 20))
    urls = [normalize_url(u) for u in profile_urls if normalize_url(u)]
    if platform == "TT":
        users = [u for u in (_tiktok_username(url) for url in urls) if u]
        candidates = [
            {
                "profiles": urls,
                "resultsPerPage": limit,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
                "shouldDownloadSubtitles": False,
            }
        ]
        if users:
            candidates.append({
                "profiles": users,
                "resultsPerPage": limit,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
                "shouldDownloadSubtitles": False,
            })
        candidates.append({
            "profileUrls": urls,
            "resultsPerPage": limit,
            "shouldDownloadVideos": False,
        })
        return candidates
    if platform == "IG":
        return [
            {"directUrls": urls, "resultsType": "posts", "resultsLimit": limit, "searchType": "user"},
            {"startUrls": [{"url": url} for url in urls], "resultsLimit": limit},
        ]
    if platform == "YTB":
        return [
            {"startUrls": [{"url": url} for url in urls], "maxResults": limit},
        ]
    if platform == "FB":
        return [
            {"startUrls": [{"url": url} for url in urls], "maxPosts": limit},
            {"startUrls": urls, "resultsLimit": limit},
        ]
    return [{"startUrls": [{"url": url} for url in urls]}]


def _scrape_batch(platform: str, urls: List[str], apify_token: str) -> List[dict]:
    actor = VIDEO_ACTORS.get(platform)
    if not actor:
        raise RuntimeError(f"不支持的平台: {platform}")
    return radar._call_actor(actor, _actor_inputs(platform, urls), apify_token)


def _scrape_profile(
    platform: str,
    profile_url: str,
    apify_token: str,
    results_limit: int = 20,
    *,
    should_abort: Optional[Callable[[], bool]] = None,
) -> List[dict]:
    actor = VIDEO_ACTORS.get(platform)
    if not actor:
        raise RuntimeError(f"不支持的平台: {platform}")
    return radar._call_actor(
        actor,
        _profile_actor_inputs(platform, profile_url, results_limit),
        apify_token,
        should_abort=should_abort,
        allow_input_fallback=True,
    )


def _scrape_profiles(
    platform: str,
    profile_urls: List[str],
    apify_token: str,
    results_limit: int = 1,
) -> List[dict]:
    actor = VIDEO_ACTORS.get(platform)
    if not actor:
        raise RuntimeError(f"不支持的平台: {platform}")
    return radar._call_actor(
        actor,
        _profile_actor_inputs_batch(platform, profile_urls, results_limit),
        apify_token,
        allow_input_fallback=True,
    )


def _index_items(items: Iterable[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for u in _item_url_keys(item):
            out.setdefault(u, item)
    return out


def _profile_url_keys_from_item(item: dict, platform: str) -> List[str]:
    keys: List[str] = []
    if platform == "TT":
        author = item.get("authorMeta") or {}
        if isinstance(author, dict):
            for user in (author.get("uniqueId"), author.get("name")):
                if user:
                    keys.append(normalize_url(f"https://www.tiktok.com/@{str(user).lstrip('@')}"))
    elif platform == "IG":
        owner = item.get("owner") or {}
        users = [
            item.get("ownerUsername"),
            item.get("username"),
            owner.get("username") if isinstance(owner, dict) else None,
        ]
        for user in users:
            if user:
                keys.append(normalize_url(f"https://www.instagram.com/{str(user).lstrip('@')}"))
    elif platform == "YTB":
        for url_key in ("channelUrl", "channelURL", "channelExternalUrl"):
            value = item.get(url_key)
            if isinstance(value, str) and value.startswith("http"):
                keys.append(normalize_url(value))
    elif platform == "FB":
        for url_key in ("pageUrl", "facebookUrl", "url"):
            value = item.get(url_key)
            if isinstance(value, str) and value.startswith("http"):
                profile = _profile_url_from_url(value, "FB")
                if profile:
                    keys.append(normalize_url(profile))

    for raw in _item_raw_urls(item):
        profile = _profile_url_from_url(raw, platform)
        if profile:
            keys.append(normalize_url(profile))
    return list(dict.fromkeys(k for k in keys if k))


def _extract_profile_metrics(item: dict, platform: str) -> dict:
    followers = _extract_followers(item, platform)
    if not followers:
        return {}
    metrics = {"followers": followers}
    extracted = _extract_metrics(item, platform) or {}
    if extracted.get("author"):
        metrics["author"] = extracted.get("author")
    return metrics


def _fetch_profile_metrics_batch(platform: str, profile_urls: List[str], apify_token: str) -> Dict[str, dict]:
    normalized_profiles = list(dict.fromkeys(normalize_url(u) for u in profile_urls if normalize_url(u)))
    results: Dict[str, dict] = {}
    try:
        items = _scrape_profiles(platform, normalized_profiles, apify_token, results_limit=1)
    except Exception as e:
        logger.error("主页粉丝抓取失败 platform=%s: %s", platform, e)
        return {url: {"_error": str(e)[:200]} for url in normalized_profiles}

    for item in items or []:
        if not isinstance(item, dict):
            continue
        metrics = _extract_profile_metrics(item, platform)
        if not metrics:
            continue
        for key in _profile_url_keys_from_item(item, platform):
            if key in normalized_profiles and key not in results:
                results[key] = metrics

    # 单主页 actor 有时只返回 item URL 不带可映射主页，保守兜底给单输入使用。
    if len(normalized_profiles) == 1 and normalized_profiles[0] not in results:
        for item in items or []:
            if isinstance(item, dict):
                metrics = _extract_profile_metrics(item, platform)
                if metrics:
                    results[normalized_profiles[0]] = metrics
                    break

    for profile_url in normalized_profiles:
        results.setdefault(profile_url, {"_error": "Apify 未返回主页粉丝数"})
    return results


def _fallback_via_profile(platform: str, url: str, apify_token: str) -> dict:
    if not VIDEO_METRICS_PROFILE_FALLBACK_ENABLED:
        return {}
    profile_url = _profile_url_from_url(url, platform)
    if not profile_url:
        return {}
    profile_items = _scrape_profile(platform, profile_url, apify_token)
    indexed = _index_items(profile_items)
    matched = indexed.get(normalize_url(url))
    if matched:
        return _extract_metrics(matched, platform)
    for item in profile_items:
        if isinstance(item, dict):
            follower_metrics = _followers_only_metrics(item, platform)
            if follower_metrics:
                return follower_metrics
    return {}


def _fetch_video_metrics_batch(platform: str, batch: List[str], apify_token: str) -> Dict[str, dict]:
    results: Dict[str, dict] = {}
    try:
        items = _scrape_batch(platform, batch, apify_token)
        indexed = _index_items(items)
        for actor_url in batch:
            item = indexed.get(actor_url)
            if not item:
                # 单条 URL 兜底再试一次
                try:
                    single_items = _scrape_batch(platform, [actor_url], apify_token)
                    indexed.update(_index_items(single_items))
                    item = indexed.get(actor_url)
                    if not item and len(single_items) == 1 and isinstance(single_items[0], dict):
                        item = single_items[0]
                except Exception as e:
                    logger.warning("单条重试失败 %s: %s", actor_url, e)
            if item:
                results[actor_url] = _extract_metrics(item, platform)
            else:
                if VIDEO_METRICS_PROFILE_FALLBACK_ENABLED:
                    try:
                        fallback_metrics = _fallback_via_profile(platform, actor_url, apify_token)
                    except Exception as e:
                        logger.warning("主页兜底失败 %s: %s", actor_url, e)
                        fallback_metrics = {}
                    results[actor_url] = fallback_metrics or {"_error": "Apify 未返回该链接数据，主页兜底也未返回可用数据"}
                else:
                    results[actor_url] = {"_error": "Apify 未返回该链接数据，已跳过主页兜底以避免额外主页抓取成本"}
    except Exception as e:
        logger.error("批次抓取失败 platform=%s: %s", platform, e)
        for actor_url in batch:
            if VIDEO_METRICS_PROFILE_FALLBACK_ENABLED:
                try:
                    fallback_metrics = _fallback_via_profile(platform, actor_url, apify_token)
                except Exception as fallback_exc:
                    logger.warning("批次失败后的主页兜底失败 %s: %s", actor_url, fallback_exc)
                    fallback_metrics = {}
                results[actor_url] = fallback_metrics or {"_error": str(e)[:200]}
            else:
                results[actor_url] = {"_error": str(e)[:200]}
    return results


def _in_date_window(post_date: str, start_date: Optional[str], end_date: Optional[str]) -> bool:
    if not (start_date or end_date):
        return True
    if not post_date:
        return False
    day = str(post_date)[:10]
    if start_date and day < start_date[:10]:
        return False
    if end_date and day > end_date[:10]:
        return False
    return True


def fetch_profile_video_metrics(
    profile_urls: List[str],
    apify_token: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_videos: int = 50,
    progress_hook: Optional[Callable[[str], None]] = None,
    should_abort: Optional[Callable[[], bool]] = None,
) -> List[dict]:
    """按主页链接抓取视频基础指标，返回一行一个视频的结构化数据。"""
    rows: List[dict] = []
    seen_keys = set()
    limit = max(1, int(max_videos or 50))
    normalized_profiles = [normalize_url(u) for u in profile_urls if normalize_url(u)]

    for idx, profile_url in enumerate(normalized_profiles, start=1):
        if should_abort and should_abort():
            raise RuntimeError("任务已停止，未继续抓取主页视频")
        platform = detect_platform(profile_url)
        if progress_hook:
            progress_hook(f"正在抓取主页视频 {idx}/{len(normalized_profiles)}: {profile_url}")
        if platform == "UNKNOWN":
            rows.append({
                "profile_url": profile_url,
                "platform": platform,
                "video_url": "",
                "video_key": f"UNKNOWN:{profile_url}",
                "_error": "无法识别平台",
            })
            continue
        try:
            items = _scrape_profile(platform, profile_url, apify_token, results_limit=limit, should_abort=should_abort)
        except Exception as e:
            if should_abort and should_abort():
                raise
            rows.append({
                "profile_url": profile_url,
                "platform": platform,
                "video_url": "",
                "video_key": f"{platform}:{profile_url}",
                "_error": str(e)[:300],
            })
            continue

        per_profile_count = 0
        for item in items or []:
            if not isinstance(item, dict):
                continue
            video_url = _canonical_profile_item_url(item, platform)
            if not video_url:
                continue
            video_key = video_key_from_url(video_url, platform)
            if not video_key or video_key in seen_keys:
                continue
            metrics = _extract_metrics(item, platform)
            if not metrics:
                continue
            if not _in_date_window(metrics.get("post_date") or "", start_date, end_date):
                continue
            seen_keys.add(video_key)
            row = {
                "profile_url": profile_url,
                "platform": platform,
                "video_url": video_url,
                "video_key": video_key,
                **metrics,
            }
            rows.append(row)
            per_profile_count += 1
            if per_profile_count >= limit:
                break

    return rows


def fetch_video_metrics(
    urls: List[str],
    apify_token: str,
    progress_hook: Optional[Callable[[str], None]] = None,
) -> Dict[str, dict]:
    """按平台分批调用 Apify，返回 {normalized_url: metrics_dict}。"""
    input_urls, actor_url_by_input = _resolve_input_urls(urls)
    grouped: Dict[str, List[str]] = {}
    profile_grouped: Dict[str, List[str]] = {}
    input_urls_by_actor_url: Dict[str, List[str]] = {}
    for input_url in input_urls:
        actor_url = actor_url_by_input.get(input_url) or input_url
        plat = detect_platform(actor_url)
        if _is_profile_url(plat, actor_url):
            profile_grouped.setdefault(plat, []).append(actor_url)
        else:
            grouped.setdefault(plat, []).append(actor_url)
        input_urls_by_actor_url.setdefault(actor_url, []).append(input_url)

    results: Dict[str, dict] = {}
    total = len(input_urls)
    done = 0

    for platform, plat_urls in grouped.items():
        if platform == "UNKNOWN":
            for actor_url in plat_urls:
                for input_url in input_urls_by_actor_url.get(actor_url, [actor_url]):
                    results[input_url] = {"_error": "无法识别平台"}
            done += sum(len(input_urls_by_actor_url.get(u, [u])) for u in plat_urls)
            continue

        unique_urls = list(dict.fromkeys(plat_urls))
        batches = [unique_urls[i : i + BATCH_SIZE] for i in range(0, len(unique_urls), BATCH_SIZE)]
        if not batches:
            continue

        if progress_hook:
            progress_hook(f"正在抓取 {platform} 视频数据，共 {len(unique_urls)} 条，{len(batches)} 批...")

        batch_workers = min(BATCH_WORKERS, len(batches))
        with ThreadPoolExecutor(max_workers=batch_workers) as executor:
            future_by_batch = {
                executor.submit(_fetch_video_metrics_batch, platform, batch, apify_token): batch
                for batch in batches
            }
            for future in as_completed(future_by_batch):
                batch = future_by_batch[future]
                batch_input_count = sum(len(input_urls_by_actor_url.get(u, [u])) for u in batch)
                try:
                    batch_results = future.result()
                except Exception as e:
                    logger.error("批次线程异常 platform=%s: %s", platform, e)
                    batch_results = {actor_url: {"_error": str(e)[:200]} for actor_url in batch}

                for actor_url in batch:
                    metrics = batch_results.get(actor_url) or {"_error": "未返回结果"}
                    for input_url in input_urls_by_actor_url.get(actor_url, [actor_url]):
                        results[input_url] = metrics
                    results[actor_url] = metrics

                done += batch_input_count
                if progress_hook:
                    progress_hook(f"正在抓取 {platform} 视频数据，已完成 {min(done, total)}/{total}...")

    for platform, profile_urls in profile_grouped.items():
        if platform == "UNKNOWN":
            for actor_url in profile_urls:
                for input_url in input_urls_by_actor_url.get(actor_url, [actor_url]):
                    results[input_url] = {"_error": "无法识别平台"}
            done += sum(len(input_urls_by_actor_url.get(u, [u])) for u in profile_urls)
            continue

        unique_profiles = list(dict.fromkeys(profile_urls))
        profile_batches = [unique_profiles[i : i + BATCH_SIZE] for i in range(0, len(unique_profiles), BATCH_SIZE)]
        if progress_hook:
            progress_hook(f"正在抓取 {platform} 主页粉丝，共 {len(unique_profiles)} 个主页...")
        for batch in profile_batches:
            batch_results = _fetch_profile_metrics_batch(platform, batch, apify_token)
            for actor_url in batch:
                metrics = batch_results.get(actor_url) or {"_error": "未返回结果"}
                for input_url in input_urls_by_actor_url.get(actor_url, [actor_url]):
                    results[input_url] = metrics
                results[actor_url] = metrics
            done += sum(len(input_urls_by_actor_url.get(u, [u])) for u in batch)
            if progress_hook:
                progress_hook(f"正在抓取 {platform} 主页粉丝，已完成 {min(done, total)}/{total}...")

    return results


def merge_metrics_into_excel(
    file_bytes: bytes,
    url_column: Optional[str],
    metrics_by_url: Dict[str, dict],
    selected_fields: List[str],
    *,
    sheet_name: Optional[str] = None,
    header_row: Optional[int] = None,
    resolved_url_column: Optional[str] = None,
    extra_urls: Optional[List[str]] = None,
) -> bytes:
    """在原 Excel 上追加/更新所选指标列。"""
    if sheet_name is not None and header_row is not None:
        if int(header_row) < 0:
            df, col, sheet_name, header_row = etl_tools.load_best_excel_table(
                file_bytes, resolved_url_column or url_column
            )
        else:
            df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name, header=header_row)
            col = resolved_url_column or url_column
            if not col or col not in df.columns:
                df, col, sheet_name, header_row = etl_tools.load_best_excel_table(
                    file_bytes, resolved_url_column or url_column
                )
    else:
        df, col, sheet_name, header_row = etl_tools.load_best_excel_table(file_bytes, url_column)

    row_links = etl_tools._hyperlink_rows_by_excel_row(file_bytes, sheet_name or "Sheet1")
    df = etl_tools._inject_row_hyperlinks(df, col, header_row or 0, row_links)

    if extra_urls:
        existing = set()
        for value in df[col]:
            raw = etl_tools._normalize_url_cell(value)
            if raw:
                existing.add(normalize_url(raw))
        extra_rows = []
        for raw_url in extra_urls:
            raw = etl_tools._normalize_url_cell(raw_url)
            normalized = normalize_url(raw)
            if raw and normalized and normalized not in existing:
                existing.add(normalized)
                extra_rows.append({col: raw})
        if extra_rows:
            df = pd.concat([df, pd.DataFrame(extra_rows)], ignore_index=True)

    selected = [f for f in selected_fields if f in METRIC_FIELDS]
    if not selected:
        selected = list(DEFAULT_VIDEO_METRIC_FIELDS)

    header_map = {f: METRIC_FIELDS[f] for f in selected}
    status_col = "抓取状态"
    if status_col not in df.columns:
        df[status_col] = None
    df[status_col] = df[status_col].astype(object)

    for field, header in header_map.items():
        if header not in df.columns:
            df[header] = None
        # 原表列可能是 string dtype，直接写 int 会报错，统一转为 object
        df[header] = df[header].astype(object)

    numeric_fields = {"views", "likes", "comments", "shares", "collects", "engagement", "followers"}

    for idx, val in df[col].items():
        raw = etl_tools._normalize_url_cell(val)
        if not raw:
            df.at[idx, status_col] = "无链接"
            continue
        key = normalize_url(raw)
        metrics = metrics_by_url.get(key) or metrics_by_url.get(raw)
        if not metrics:
            df.at[idx, status_col] = "未抓取"
            continue
        if metrics.get("_error"):
            df.at[idx, status_col] = metrics["_error"]
            continue
        for field, header in header_map.items():
            value = metrics.get(field, "")
            if field in numeric_fields and value not in ("", None):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    pass
            df.at[idx, header] = value
        df.at[idx, status_col] = "成功"

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="video_metrics", index=False)
    buf.seek(0)
    return buf.read()


def build_manual_metrics_excel(
    urls: List[str],
    metrics_by_url: Dict[str, dict],
    selected_fields: List[str],
) -> bytes:
    """Create a metrics workbook for links pasted manually without an input Excel."""
    selected = [f for f in selected_fields if f in METRIC_FIELDS]
    if not selected:
        selected = list(DEFAULT_VIDEO_METRIC_FIELDS)

    rows = []
    seen = set()
    for raw_url in urls:
        raw = etl_tools._normalize_url_cell(raw_url)
        if not raw:
            continue
        normalized = normalize_url(raw)
        if normalized in seen:
            continue
        seen.add(normalized)
        metrics = metrics_by_url.get(normalized) or metrics_by_url.get(raw) or {}
        row = {
            MANUAL_URL_COLUMN: raw,
            MANUAL_PLATFORM_COLUMN: detect_platform(raw),
        }
        if metrics.get("_error"):
            for field in selected:
                row[METRIC_FIELDS[field]] = None
            row["抓取状态"] = metrics["_error"]
        elif metrics:
            for field in selected:
                row[METRIC_FIELDS[field]] = metrics.get(field, "")
            row["抓取状态"] = "成功"
        else:
            for field in selected:
                row[METRIC_FIELDS[field]] = None
            row["抓取状态"] = "未抓取"
        rows.append(row)

    columns = [MANUAL_URL_COLUMN, MANUAL_PLATFORM_COLUMN] + [METRIC_FIELDS[f] for f in selected] + ["抓取状态"]
    df = pd.DataFrame(rows, columns=columns)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="video_metrics", index=False)
    buf.seek(0)
    return buf.read()


def build_profile_video_export_excel(rows: List[dict]) -> bytes:
    """Create a workbook for profile URL video exports."""
    out_rows = []
    numeric_fields = {
        "播放量": "views",
        "点赞量": "likes",
        "评论量": "comments",
        "转发量": "shares",
        "收藏量": "collects",
    }
    for row in rows or []:
        item = {
            "视频链接": row.get("video_url") or "",
            "达人主页链接": row.get("profile_url") or "",
            "达人名称": row.get("author") or "",
            "视频发布时间": row.get("post_date") or "",
            "视频标题": row.get("caption") or "",
            "视频时长": row.get("duration") or "",
            "抓取状态": row.get("_error") or "成功",
        }
        for header, key in numeric_fields.items():
            value = row.get(key)
            try:
                item[header] = int(value) if value not in ("", None) else None
            except (TypeError, ValueError):
                item[header] = value
        out_rows.append(item)

    df = pd.DataFrame(out_rows, columns=PROFILE_VIDEO_EXPORT_COLUMNS)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="profile_videos", index=False)
    buf.seek(0)
    return buf.read()
