import io
import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd

fake_tasks = types.ModuleType("tasks")


def _safe_int(value, default=0):
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(value)
    except Exception:
        return default


fake_tasks._safe_int = _safe_int
fake_tasks.APIFY_TOKEN = "token"
sys.modules.setdefault("tasks", fake_tasks)

import video_metrics_etl


class VideoMetricsEtlTests(unittest.TestCase):
    def test_fetch_video_metrics_resolves_tiktok_short_url(self):
        short_url = "https://vt.tiktok.com/ZSQoKaT60/"
        long_url = "https://www.tiktok.com/@bunnyhack04/video/1234567890"
        seen_batches = []

        def fake_scrape(platform, urls, _token):
            seen_batches.append((platform, list(urls)))
            self.assertEqual(platform, "TT")
            self.assertEqual(urls, [video_metrics_etl.normalize_url(long_url)])
            return [
                {
                    "webVideoUrl": long_url,
                    "playCount": 1234,
                    "diggCount": 56,
                    "commentCount": 7,
                    "shareCount": 8,
                    "collectCount": 9,
                    "authorMeta": {"uniqueId": "bunnyhack04", "fans": 1000},
                    "createTimeISO": "2026-06-18T00:00:00Z",
                    "text": "caption",
                }
            ]

        with patch.object(video_metrics_etl, "_resolve_redirect_url", return_value=long_url), patch.object(
            video_metrics_etl, "_scrape_batch", side_effect=fake_scrape
        ):
            result = video_metrics_etl.fetch_video_metrics([short_url], "token")

        short_key = video_metrics_etl.normalize_url(short_url)
        self.assertEqual(len(seen_batches), 1)
        self.assertIn(short_key, result)
        self.assertEqual(result[short_key]["views"], 1234)
        self.assertEqual(result[short_key]["likes"], 56)

    def test_merge_metrics_writes_short_url_row(self):
        short_url = "https://vt.tiktok.com/ZSQoKaT60/"
        df = pd.DataFrame({"短链": [short_url]})
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)

        metrics = {
            video_metrics_etl.normalize_url(short_url): {
                "views": 4321,
                "likes": 88,
                "comments": 6,
            }
        }

        out = video_metrics_etl.merge_metrics_into_excel(
            buf.getvalue(),
            "短链",
            metrics,
            ["views", "likes", "comments"],
        )
        out_df = pd.read_excel(io.BytesIO(out))

        self.assertEqual(int(out_df.loc[0, "播放量"]), 4321)
        self.assertEqual(int(out_df.loc[0, "点赞量"]), 88)
        self.assertEqual(int(out_df.loc[0, "评论量"]), 6)
        self.assertEqual(out_df.loc[0, "抓取状态"], "成功")

    def test_single_retry_uses_only_returned_item_when_url_keys_differ(self):
        short_url = "https://vt.tiktok.com/ZSQoKaT60/"
        calls = []

        def fake_scrape(_platform, urls, _token):
            calls.append(list(urls))
            if len(calls) == 1:
                return []
            return [
                {
                    "webVideoUrl": "https://www.tiktok.com/@someone/video/999",
                    "playCount": 2468,
                    "diggCount": 10,
                    "commentCount": 2,
                    "shareCount": 3,
                    "authorMeta": {"uniqueId": "someone"},
                }
            ]

        with patch.object(video_metrics_etl, "_resolve_redirect_url", return_value=""), patch.object(
            video_metrics_etl, "_scrape_batch", side_effect=fake_scrape
        ):
            result = video_metrics_etl.fetch_video_metrics([short_url], "token")

        short_key = video_metrics_etl.normalize_url(short_url)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result[short_key]["views"], 2468)

    def test_fetch_profile_video_metrics_extracts_video_rows(self):
        profile_url = "https://www.tiktok.com/@bunnyhack04"

        def fake_profile(platform, url, _token, results_limit=20):
            self.assertEqual(platform, "TT")
            self.assertEqual(url, video_metrics_etl.normalize_url(profile_url))
            self.assertEqual(results_limit, 3)
            return [
                {
                    "webVideoUrl": "https://www.tiktok.com/@bunnyhack04/video/123",
                    "playCount": 1000,
                    "diggCount": 20,
                    "commentCount": 3,
                    "shareCount": 4,
                    "collectCount": 5,
                    "authorMeta": {"uniqueId": "bunnyhack04", "fans": 777},
                    "createTimeISO": "2026-06-18T00:00:00Z",
                    "text": "hello",
                }
            ]

        with patch.object(video_metrics_etl, "_scrape_profile", side_effect=fake_profile):
            rows = video_metrics_etl.fetch_profile_video_metrics(
                [profile_url],
                "token",
                start_date="2026-06-01",
                max_videos=3,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["video_key"], "TT:123")
        self.assertEqual(rows[0]["views"], 1000)
        self.assertEqual(rows[0]["followers"], 777)


if __name__ == "__main__":
    unittest.main()
