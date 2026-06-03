from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://business-api.tiktok.com/open_api/v1.3"

VIDEO_FIELDS = [
    "item_id",
    "media_type",
    "is_ad",
    "thumbnail_url",
    "share_url",
    "embed_url",
    "caption",
    "video_duration",
    "likes",
    "comments",
    "shares",
    "favorites",
    "create_time",
    "reach",
    "video_views",
    "total_time_watched",
    "average_time_watched",
    "full_video_watched_rate",
    "new_followers",
    "profile_views",
    "website_clicks",
    "phone_number_clicks",
    "lead_submissions",
    "app_download_clicks",
    "email_clicks",
    "address_clicks",
    "video_view_retention",
    "impression_sources",
    "audience_genders",
    "audience_countries",
    "audience_cities",
    "audience_types",
    "engagement_likes",
]

PROFILE_FIELDS = [
    "is_business_account",
    "profile_image",
    "username",
    "profile_deep_link",
    "display_name",
    "bio_description",
    "is_verified",
    "following_count",
    "followers_count",
    "total_likes",
    "videos_count",
    "video_views",
    "unique_video_views",
    "profile_views",
    "likes",
    "comments",
    "shares",
    "phone_number_clicks",
    "lead_submissions",
    "app_download_clicks",
    "bio_link_clicks",
    "email_clicks",
    "address_clicks",
    "daily_total_followers",
    "daily_new_followers",
    "daily_lost_followers",
    "audience_activity",
    "engaged_audience",
    "audience_ages",
    "audience_genders",
    "audience_countries",
    "audience_cities",
]


@dataclass
class TikTokBusinessResponse:
    data: dict[str, Any]
    request_id: str | None
    log_id: str | None


class TikTokBusinessError(RuntimeError):
    def __init__(self, message: str, request_id: str | None = None, log_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.log_id = log_id


class TikTokBusinessClient:
    def __init__(self, access_token: str | None = None, api_base: str = API_BASE) -> None:
        self.access_token = access_token or os.getenv("TIKTOK_BUSINESS_ACCESS_TOKEN", "").strip()
        self.api_base = api_base.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.access_token)

    def list_videos(self, business_id: str, max_pages: int = 5) -> TikTokBusinessResponse:
        if not self.configured:
            return self._mock_video_response(business_id)
        cursor: int | None = None
        all_videos: list[dict[str, Any]] = []
        last_request_id = None
        last_log_id = None
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "business_id": business_id,
                "fields": json.dumps(VIDEO_FIELDS, separators=(",", ":")),
                "max_count": 20,
            }
            if cursor is not None:
                params["cursor"] = cursor
            response = self._get("/business/video/list/", params)
            data = response.data.get("data") or {}
            videos = data.get("videos") or []
            if isinstance(videos, list):
                all_videos.extend(videos)
            cursor = data.get("cursor")
            last_request_id = response.request_id
            last_log_id = response.log_id
            if not data.get("has_more") or cursor is None:
                break
        return TikTokBusinessResponse(
            data={"videos": all_videos},
            request_id=last_request_id,
            log_id=last_log_id,
        )

    def get_profile(self, business_id: str, start_date: date | None = None, end_date: date | None = None) -> TikTokBusinessResponse:
        if not self.configured:
            return self._mock_profile_response(business_id, start_date, end_date)
        end = end_date or (date.today() - timedelta(days=1))
        start = start_date or (end - timedelta(days=6))
        params = {
            "business_id": business_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "fields": json.dumps(PROFILE_FIELDS, separators=(",", ":")),
        }
        return self._get("/business/get/", params)

    def _get(self, path: str, params: dict[str, Any]) -> TikTokBusinessResponse:
        url = f"{self.api_base}{path}?{urlencode(params)}"
        req = Request(url, headers={"Access-Token": self.access_token})
        with urlopen(req, timeout=60) as resp:  # noqa: S310 - URL is fixed TikTok API base.
            log_id = resp.headers.get("X-Tt-Logid")
            payload = json.loads(resp.read().decode("utf-8"))
        request_id = payload.get("request_id")
        if payload.get("code") not in (0, "0", None):
            raise TikTokBusinessError(
                f"TikTok API error {payload.get('code')}: {payload.get('message')}",
                request_id=request_id,
                log_id=log_id,
            )
        return TikTokBusinessResponse(data=payload, request_id=request_id, log_id=log_id)

    def _mock_video_response(self, business_id: str) -> TikTokBusinessResponse:
        now = int(datetime.utcnow().timestamp())
        return TikTokBusinessResponse(
            data={
                "videos": [
                    {
                        "item_id": f"mock_{business_id}_001",
                        "media_type": "VIDEO",
                        "is_ad": False,
                        "thumbnail_url": "",
                        "share_url": "https://www.tiktok.com/@sailson/video/mock001",
                        "embed_url": "",
                        "caption": "Mock official post for dashboard preview",
                        "video_duration": 18.2,
                        "likes": 1280,
                        "comments": 96,
                        "shares": 48,
                        "favorites": 210,
                        "create_time": str(now - 86400),
                        "reach": 18000,
                        "video_views": 35600,
                        "total_time_watched": 122000.0,
                        "average_time_watched": 6.8,
                        "full_video_watched_rate": 0.214,
                        "new_followers": 132,
                        "profile_views": 580,
                        "video_view_retention": [{"second": "3", "percentage": 0.72}],
                        "impression_sources": [{"impression_source": "For You", "percentage": 0.81}],
                        "audience_countries": [{"country": "ID", "percentage": 0.63}],
                        "engagement_likes": [{"second": "4", "percentage": 0.18}],
                    },
                    {
                        "item_id": f"mock_{business_id}_002",
                        "media_type": "VIDEO",
                        "is_ad": False,
                        "share_url": "https://www.tiktok.com/@sailson/video/mock002",
                        "caption": "Mock campaign recap post",
                        "video_duration": 24.0,
                        "likes": 820,
                        "comments": 41,
                        "shares": 22,
                        "favorites": 94,
                        "create_time": str(now - 172800),
                        "reach": 9400,
                        "video_views": 18200,
                        "average_time_watched": 5.3,
                        "full_video_watched_rate": 0.167,
                        "new_followers": 54,
                        "video_view_retention": [{"second": "3", "percentage": 0.66}],
                        "engagement_likes": [{"second": "5", "percentage": 0.12}],
                    },
                ]
            },
            request_id="mock-request-id",
            log_id="mock-log-id",
        )

    def _mock_profile_response(
        self, business_id: str, start_date: date | None, end_date: date | None
    ) -> TikTokBusinessResponse:
        end = end_date or (date.today() - timedelta(days=1))
        start = start_date or (end - timedelta(days=6))
        daily = []
        current = start
        idx = 0
        while current <= end:
            daily.append(
                {
                    "date": current.isoformat(),
                    "video_views": 10000 + idx * 1200,
                    "unique_video_views": 7200 + idx * 850,
                    "profile_views": 320 + idx * 18,
                    "likes": 520 + idx * 42,
                    "comments": 35 + idx * 3,
                    "shares": 18 + idx * 2,
                    "daily_new_followers": 40 + idx * 4,
                    "daily_lost_followers": 8 + idx,
                    "daily_total_followers": 128000 + idx * 32,
                }
            )
            current += timedelta(days=1)
            idx += 1
        return TikTokBusinessResponse(
            data={
                "data": {
                    "business_id": business_id,
                    "username": "sailson_official",
                    "display_name": "Sailson Official",
                    "profile_deep_link": "https://www.tiktok.com/@sailson_official",
                    "is_business_account": True,
                    "is_verified": False,
                    "followers_count": 128000,
                    "following_count": 128,
                    "total_likes": 2580000,
                    "videos_count": 216,
                    "daily_metrics": daily,
                    "audience_genders": [{"gender": "Female", "percentage": 0.54}],
                    "audience_countries": [{"country": "ID", "percentage": 0.68}],
                    "audience_activity": [{"hour": "20", "percentage": 0.16}],
                }
            },
            request_id="mock-request-id",
            log_id="mock-log-id",
        )
