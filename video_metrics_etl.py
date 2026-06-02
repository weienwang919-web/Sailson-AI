"""
Excel 视频链接批量拉取基础指标（Apify），并写回原表。
"""
from __future__ import annotations

import io
import logging
import os
import re
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import pandas as pd

import competitor_radar as radar
import etl_tools
import tasks

logger = logging.getLogger(__name__)

BATCH_SIZE = int(os.environ.get("ETL_VIDEO_METRICS_BATCH_SIZE", "40"))

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

VIDEO_ACTORS = {
    "TT": os.environ.get("APIFY_TIKTOK_PROFILE_ACTOR_ID", "clockworks/tiktok-scraper"),
    "IG": os.environ.get("APIFY_INSTAGRAM_PROFILE_ACTOR_ID", "apify/instagram-scraper"),
    "YTB": os.environ.get("APIFY_YOUTUBE_SCRAPER_ACTOR_ID", "streamers/youtube-scraper"),
    "FB": os.environ.get("APIFY_FB_POSTS_ACTOR_ID", "apify/facebook-posts-scraper"),
}

_YT_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com")
_FB_HOSTS = ("facebook.com", "fb.com", "fb.watch", "m.facebook.com")


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


def _profile_actor_inputs(platform: str, profile_url: str) -> List[dict]:
    if platform == "TT":
        user = _tiktok_username(profile_url)
        profile_seed = user or profile_url
        return [
            {
                "profiles": [profile_seed],
                "resultsPerPage": 20,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
                "shouldDownloadSubtitles": False,
            },
            {
                "profileUrls": [profile_url],
                "resultsPerPage": 20,
                "shouldDownloadVideos": False,
            },
        ]
    if platform == "IG":
        return [
            {"directUrls": [profile_url], "resultsType": "posts", "resultsLimit": 20, "searchType": "user"},
            {"startUrls": [{"url": profile_url}], "resultsLimit": 20},
        ]
    if platform == "YTB":
        return [
            {"startUrls": [{"url": profile_url}], "maxResults": 20},
        ]
    if platform == "FB":
        return [
            {"startUrls": [{"url": profile_url}], "maxPosts": 20},
            {"startUrls": [profile_url], "resultsLimit": 20},
        ]
    return [{"startUrls": [{"url": profile_url}]}]


def _scrape_batch(platform: str, urls: List[str], apify_token: str) -> List[dict]:
    actor = VIDEO_ACTORS.get(platform)
    if not actor:
        raise RuntimeError(f"不支持的平台: {platform}")
    return radar._call_actor(actor, _actor_inputs(platform, urls), apify_token)


def _scrape_profile(platform: str, profile_url: str, apify_token: str) -> List[dict]:
    actor = VIDEO_ACTORS.get(platform)
    if not actor:
        raise RuntimeError(f"不支持的平台: {platform}")
    return radar._call_actor(actor, _profile_actor_inputs(platform, profile_url), apify_token)


def _index_items(items: Iterable[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for u in _item_url_keys(item):
            out.setdefault(u, item)
    return out


def _fallback_via_profile(platform: str, url: str, apify_token: str) -> dict:
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


def fetch_video_metrics(
    urls: List[str],
    apify_token: str,
    progress_hook: Optional[Callable[[str], None]] = None,
) -> Dict[str, dict]:
    """按平台分批调用 Apify，返回 {normalized_url: metrics_dict}。"""
    grouped: Dict[str, List[str]] = {}
    for raw in urls:
        u = normalize_url(raw)
        if not u:
            continue
        plat = detect_platform(u)
        grouped.setdefault(plat, []).append(u)

    results: Dict[str, dict] = {}
    total = sum(len(v) for v in grouped.values())
    done = 0

    for platform, plat_urls in grouped.items():
        if platform == "UNKNOWN":
            for u in plat_urls:
                results[u] = {"_error": "无法识别平台"}
            done += len(plat_urls)
            continue

        unique_urls = list(dict.fromkeys(plat_urls))
        for i in range(0, len(unique_urls), BATCH_SIZE):
            batch = unique_urls[i : i + BATCH_SIZE]
            msg = f"正在抓取 {platform} 视频数据 ({done + 1}-{min(done + len(batch), total)}/{total})..."
            if progress_hook:
                progress_hook(msg)
            try:
                items = _scrape_batch(platform, batch, apify_token)
                indexed = _index_items(items)
                for u in batch:
                    item = indexed.get(u)
                    if not item:
                        # 单条 URL 兜底再试一次
                        try:
                            single_items = _scrape_batch(platform, [u], apify_token)
                            indexed.update(_index_items(single_items))
                            item = indexed.get(u)
                        except Exception as e:
                            logger.warning("单条重试失败 %s: %s", u, e)
                    if item:
                        results[u] = _extract_metrics(item, platform)
                    else:
                        try:
                            fallback_metrics = _fallback_via_profile(platform, u, apify_token)
                        except Exception as e:
                            logger.warning("主页兜底失败 %s: %s", u, e)
                            fallback_metrics = {}
                        if fallback_metrics:
                            results[u] = fallback_metrics
                        else:
                            results[u] = {"_error": "Apify 未返回该链接数据，主页兜底也未返回可用数据"}
            except Exception as e:
                logger.error("批次抓取失败 platform=%s: %s", platform, e)
                for u in batch:
                    try:
                        fallback_metrics = _fallback_via_profile(platform, u, apify_token)
                    except Exception as fallback_exc:
                        logger.warning("批次失败后的主页兜底失败 %s: %s", u, fallback_exc)
                        fallback_metrics = {}
                    if fallback_metrics:
                        results[u] = fallback_metrics
                    else:
                        results[u] = {"_error": str(e)[:200]}
            done += len(batch)

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
) -> bytes:
    """在原 Excel 上追加/更新所选指标列。"""
    if sheet_name is not None and header_row is not None:
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

    selected = [f for f in selected_fields if f in METRIC_FIELDS]
    if not selected:
        selected = ["views", "likes", "comments"]

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
