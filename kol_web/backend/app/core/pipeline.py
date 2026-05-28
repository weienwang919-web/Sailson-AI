from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

SKIP_VALUES = {"", "/", "nan", "NaN", "Included", "included", "None"}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def is_empty_value(value: Any) -> bool:
    return clean_text(value) in SKIP_VALUES


def to_int(value: Any) -> int | None:
    s = clean_text(value).replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
        return int(f)
    except ValueError:
        pass
    suffix = s[-1:].lower()
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix)
    if mult:
        try:
            return int(float(s[:-1]) * mult)
        except ValueError:
            return None
    return None


def to_float(value: Any) -> float | None:
    if is_empty_value(value):
        return None
    s = clean_text(value).replace(",", "").replace("$", "")
    try:
        return float(s)
    except ValueError:
        return None


def tiktok_username(url: str) -> str:
    match = re.search(r"tiktok\.com/@([^/?#]+)", url, re.I)
    return match.group(1).strip() if match else ""


def instagram_username(url: str) -> str:
    path = urlparse(url).path.strip("/").split("/")[0]
    if path.lower() in {"p", "reel", "reels", "stories", "explore"}:
        return ""
    return sanitize_instagram_username(path)


def youtube_channel_seed(url: str) -> str:
    value = clean_text(url).split("?")[0].rstrip("/")
    for suffix in ("/videos", "/shorts", "/featured"):
        if suffix in value:
            value = value.split(suffix)[0]
    return value


def sanitize_instagram_username(user: str) -> str:
    value = user.strip().strip("\\/").strip()
    return re.sub(r"[^A-Za-z0-9._-]", "", value)


def normalize_link(platform: str, url: Any) -> str:
    value = _extract_platform_url(platform, clean_text(url).strip("\\"))
    if is_empty_value(value) or not value.lower().startswith("http"):
        return ""
    platform = platform.lower()
    if platform == "tiktok":
        user = tiktok_username(value)
        return f"https://www.tiktok.com/@{user}" if user else ""
    if platform in {"ins", "instagram"}:
        user = instagram_username(value)
        return f"https://www.instagram.com/{user}/" if user else ""
    if platform in {"youtube", "yt"}:
        return youtube_channel_seed(value)
    return value


def _extract_platform_url(platform: str, value: str) -> str:
    urls = re.findall(r"https?://[^\s,;，；)）]+", value)
    if not urls:
        return value
    platform = platform.lower()
    if platform == "tiktok":
        match = next((url for url in urls if "tiktok.com" in url.lower()), "")
    elif platform in {"ins", "instagram"}:
        match = next((url for url in urls if "instagram.com" in url.lower()), "")
    elif platform in {"youtube", "yt"}:
        match = next((url for url in urls if "youtube.com" in url.lower() or "youtu.be" in url.lower()), "")
    else:
        match = urls[0]
    return match


def link_matches_platform(link: str, platform: str) -> bool:
    value = clean_text(link).lower()
    if platform == "tiktok":
        return "tiktok.com" in value
    if platform in {"ins", "instagram"}:
        return "instagram.com" in value
    if platform in {"youtube", "yt"}:
        return "youtube.com" in value or "youtu.be" in value
    return True


def parse_platform_blocks(header0: list[Any], header1: list[Any]) -> dict[str, dict[str, Any]]:
    row0 = [clean_text(x).lower() for x in header0]
    row1 = [clean_text(x).lower() for x in header1]
    aliases = {"youtube": "youtube", "yt": "youtube", "tiktok": "tiktok", "ins": "ins", "instagram": "ins"}
    blocks: dict[str, dict[str, Any]] = {}
    i = 0
    while i < len(row0):
        key = aliases.get(row0[i])
        if not key:
            i += 1
            continue
        end = len(row0)
        for j in range(i + 1, len(row0)):
            if aliases.get(row0[j]):
                end = j
                break
        cols = {row1[c]: c for c in range(i, end) if row1[c]}
        blocks[key] = {"start": i, "end": end, "cols": cols}
        i = end
    return blocks


def avg_views(values: list[int], n: int) -> int | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    use = values[:n]
    return int(round(sum(use) / len(use)))


def aggregate_tiktok(items: list[dict[str, Any]], n: int) -> dict[str, dict[str, Any]]:
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    followers: dict[str, int] = {}
    for item in items:
        author = item.get("authorMeta") or {}
        user = ""
        follower = None
        if isinstance(author, dict):
            user = clean_text(author.get("name") or author.get("uniqueId")).lower()
            follower = to_int(author.get("fans") or author.get("followers"))
        if not user:
            user = clean_text(item.get("authorMeta.name")).lower()
            follower = to_int(item.get("authorMeta.fans"))
        if not user:
            user = tiktok_username(clean_text(item.get("webVideoUrl"))).lower()
        play = to_int(item.get("playCount"))
        if user and play is not None:
            by_user[user].append({"views": play, "date": item.get("createTimeISO") or ""})
        if user and follower is not None:
            followers[user] = follower
    return _aggregate_view_rows(by_user, followers, n)


def aggregate_instagram(items: list[dict[str, Any]], n: int) -> dict[str, dict[str, Any]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    followers: dict[str, int] = {}
    for item in items:
        owner = clean_text(item.get("ownerUsername") or (item.get("owner") or {}).get("username")).lower()
        if not owner and item.get("inputUrl"):
            owner = instagram_username(clean_text(item.get("inputUrl"))).lower()
        if not owner:
            continue
        follower = to_int(item.get("followersCount") or (item.get("owner") or {}).get("followers"))
        if follower is not None:
            followers[owner] = follower
        view = to_int(item.get("videoViewCount") or item.get("viewCount") or item.get("playCount"))
        if view is not None:
            rows[owner].append({"views": view, "date": item.get("timestamp") or item.get("takenAt") or ""})
    return _aggregate_view_rows(rows, followers, n)


def aggregate_youtube(items: list[dict[str, Any]], n: int) -> dict[str, dict[str, Any]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    followers: dict[str, int] = {}
    for item in items:
        handle = clean_text(item.get("channelUsername")).lstrip("@").lower()
        if not handle:
            for field in ("inputChannelUrl", "input", "fromYTUrl", "channelUrl"):
                source = clean_text(item.get(field))
                match = re.search(r"youtube\.com/@([^/?#]+)", source, re.I)
                if match:
                    handle = match.group(1).lower()
                    break
        if not handle:
            handle = clean_text(item.get("channelName")).lower()
        view = to_int(item.get("viewCount") or item.get("views"))
        if handle and view is not None:
            rows[handle].append({"views": view, "date": item.get("date") or item.get("uploadDate") or ""})
        follower = to_int(item.get("numberOfSubscribers") or item.get("subscriberCount") or item.get("channelSubscriberCount"))
        if handle and follower is not None:
            followers[handle] = follower
    return _aggregate_view_rows(rows, followers, n)


def _aggregate_view_rows(rows: dict[str, list[dict[str, Any]]], followers: dict[str, int], n: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, values in rows.items():
        values.sort(key=lambda x: clean_text(x.get("date")), reverse=True)
        views = [v["views"] for v in values if v.get("views") is not None]
        out[key] = {
            "followers": followers.get(key),
            "avv": avg_views(views, n),
            "video_count": min(len(views), n),
        }
    for key, follower in followers.items():
        out.setdefault(key, {"followers": follower, "avv": None, "video_count": 0})
    return out


def build_apify_inputs(records: list[Any], videos_per_profile: int) -> dict[str, dict[str, Any]]:
    tt_profiles = sorted({tiktok_username(r.tt_link) for r in records if r.tt_link})
    ins_urls = sorted({normalize_link("ins", r.ins_link) for r in records if r.ins_link})
    yt_urls = sorted({normalize_link("youtube", r.yt_link) for r in records if r.yt_link})
    return {
        "tiktok": {
            "actor": "clockworks/tiktok-scraper",
            "input": {
                "profiles": [x for x in tt_profiles if x],
                "profileScrapeSections": ["videos"],
                "profileSorting": "latest",
                "resultsPerPage": videos_per_profile,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
                "shouldDownloadSubtitles": False,
                "shouldDownloadSlideshowImages": False,
            },
        },
        "ins": {
            "actor": "apify/instagram-scraper",
            "input": {
                "directUrls": [x for x in ins_urls if x],
                "resultsType": "posts",
                "searchType": "user",
                "addParentData": True,
                "resultsLimit": videos_per_profile,
            },
        },
        "youtube": {
            "actor": "streamers/youtube-scraper",
            "input": {
                "startUrls": [{"url": x} for x in yt_urls if x],
                "maxResults": videos_per_profile,
                "maxResultsShorts": 0,
                "maxResultStreams": 0,
            },
        },
    }


def call_apify(actor_id: str, run_input: dict[str, Any], timeout_secs: int = 3600) -> list[dict[str, Any]]:
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError("APIFY_TOKEN is not configured")
    from apify_client import ApifyClient

    client = ApifyClient(token)
    run = client.actor(actor_id).call(run_input=run_input, run_timeout=timedelta(seconds=timeout_secs))
    if not run:
        raise RuntimeError(f"Apify actor did not return a run: {actor_id}")
    dataset_id = getattr(run, "default_dataset_id", None)
    if not dataset_id and isinstance(run, dict):
        dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError(f"Apify actor did not return a dataset id: {actor_id}")
    return list(client.dataset(dataset_id).iterate_items())


def metrics_from_raw(raw: dict[str, list[dict[str, Any]]], videos_per_profile: int) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "tiktok": aggregate_tiktok(raw.get("tiktok", []), videos_per_profile),
        "ins": aggregate_instagram(raw.get("ins", []), videos_per_profile),
        "youtube": aggregate_youtube(raw.get("youtube", []), videos_per_profile),
    }


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def now_utc() -> datetime:
    return datetime.utcnow()
