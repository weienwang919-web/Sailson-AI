import io
import sys
import time
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
import competitor_radar


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

    def test_short_url_resolution_runs_concurrently(self):
        short_urls = [f"https://vt.tiktok.com/ZSQoKaT6{i}/" for i in range(4)]
        normalized_short_urls = [video_metrics_etl.normalize_url(url) for url in short_urls]

        def fake_resolve(url):
            time.sleep(0.05)
            idx = normalized_short_urls.index(url)
            return f"https://www.tiktok.com/@user/video/{idx}"

        def fake_scrape(platform, urls, _token):
            self.assertEqual(platform, "TT")
            return [
                {
                    "webVideoUrl": url,
                    "playCount": 100 + idx,
                    "diggCount": 1,
                }
                for idx, url in enumerate(urls)
            ]

        started = time.perf_counter()
        with patch.object(video_metrics_etl, "SHORT_URL_WORKERS", 4), patch.object(
            video_metrics_etl, "_resolve_redirect_url", side_effect=fake_resolve
        ), patch.object(video_metrics_etl, "_scrape_batch", side_effect=fake_scrape):
            result = video_metrics_etl.fetch_video_metrics(short_urls, "token")
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.16)
        for raw in short_urls:
            self.assertIn(video_metrics_etl.normalize_url(raw), result)

    def test_apify_batches_run_concurrently(self):
        urls = [f"https://www.tiktok.com/@user/video/{i}" for i in range(5)]

        def fake_scrape(platform, batch, _token):
            self.assertEqual(platform, "TT")
            time.sleep(0.05)
            return [
                {
                    "webVideoUrl": url,
                    "playCount": 200 + idx,
                    "diggCount": 1,
                }
                for idx, url in enumerate(batch)
            ]

        started = time.perf_counter()
        with patch.object(video_metrics_etl, "BATCH_SIZE", 2), patch.object(
            video_metrics_etl, "BATCH_WORKERS", 2
        ), patch.object(video_metrics_etl, "_scrape_batch", side_effect=fake_scrape):
            result = video_metrics_etl.fetch_video_metrics(urls, "token")
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.13)
        self.assertEqual(result[video_metrics_etl.normalize_url(urls[0])]["views"], 200)

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

    def test_parse_manual_urls_accepts_multiplatform_lines(self):
        text = """
        1. https://www.tiktok.com/@user/video/123
        - https://www.instagram.com/reel/abc/
        https://youtu.be/xyz,
        www.facebook.com/reel/555
        not a url
        """

        urls = video_metrics_etl.parse_manual_urls(text)

        self.assertEqual(len(urls), 4)
        self.assertTrue(urls[0].startswith("https://www.tiktok.com"))
        self.assertIn("instagram.com/reel/abc", urls[1])
        self.assertIn("youtu.be/xyz", urls[2])
        self.assertEqual(video_metrics_etl.detect_platform(urls[3]), "FB")

    def test_build_manual_metrics_excel_creates_result_workbook(self):
        url = "https://www.youtube.com/watch?v=abc123"
        metrics = {
            video_metrics_etl.normalize_url(url): {
                "views": 1000,
                "likes": 20,
                "comments": 3,
            }
        }

        out = video_metrics_etl.build_manual_metrics_excel(
            [url],
            metrics,
            ["views", "likes", "comments"],
        )
        out_df = pd.read_excel(io.BytesIO(out))

        self.assertEqual(out_df.loc[0, "视频链接"], url)
        self.assertEqual(out_df.loc[0, "平台"], "YTB")
        self.assertEqual(int(out_df.loc[0, "播放量"]), 1000)
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

        def fake_profile(platform, url, _token, results_limit=20, **_kwargs):
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

    def test_call_actor_aborts_running_run_when_stop_requested(self):
        calls = {"get": 0, "abort": 0}

        class FakeResponse:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.text = str(payload)

            def json(self):
                return self._payload

        def fake_post(url, **kwargs):
            if url.endswith("/runs"):
                return FakeResponse(201, {"data": {"id": "run-1"}})
            if url.endswith("/actor-runs/run-1/abort"):
                calls["abort"] += 1
                return FakeResponse(200, {"data": {"status": "ABORTING"}})
            raise AssertionError(url)

        def fake_get(url, **kwargs):
            calls["get"] += 1
            return FakeResponse(200, {"data": {"status": "RUNNING"}})

        with patch.object(competitor_radar.requests, "post", side_effect=fake_post), patch.object(
            competitor_radar.requests, "get", side_effect=fake_get
        ), patch.object(competitor_radar, "ACTOR_POLL_INTERVAL", 1):
            with self.assertRaisesRegex(RuntimeError, "已因任务停止而中断"):
                competitor_radar._call_actor(
                    "clockworks/tiktok-scraper",
                    [{"profiles": ["demo"]}],
                    "token",
                    should_abort=lambda: calls["get"] >= 1,
                )

        self.assertEqual(calls["abort"], 1)


if __name__ == "__main__":
    unittest.main()
