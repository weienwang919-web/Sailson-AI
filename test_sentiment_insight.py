import json
import unittest
from unittest.mock import Mock, patch

import sentiment_insight


class SentimentInsightTests(unittest.TestCase):
    def test_extract_comment_like_count_from_common_fields(self):
        item = {
            "text": "great update",
            "commentId": "abc",
            "likesCount": "1.2K",
            "author": {"name": "player"},
        }

        comment = sentiment_insight._extract_comment(item, "TT", "https://www.tiktok.com/@a/video/1")

        self.assertEqual(comment["comment_id"], "TT:abc")
        self.assertEqual(comment["like_count"], 1200)

    def test_ai_alignment_leaves_missing_translation_blank(self):
        comments = [
            {"_analysis_id": "C0", "comment_id": "p:c0", "text": "first comment"},
            {"_analysis_id": "C1", "comment_id": "p:c1", "text": "second comment"},
            {"_analysis_id": "C2", "comment_id": "p:c2", "text": "third comment"},
        ]

        def ai_call(_prompt, _timeout):
            return json.dumps(
                [
                    {
                        "idx": 0,
                        "id": "C0",
                        "translation_zh": "第一条",
                        "sentiment": "中立",
                        "category": "其他",
                    },
                    {
                        "idx": 2,
                        "id": "C2",
                        "translation_zh": "第三条",
                        "sentiment": "正向",
                        "category": "产品体验",
                    },
                ],
                ensure_ascii=False,
            ), 10

        results, tokens = sentiment_insight._run_ai_for_comments(comments, ai_call)

        self.assertEqual(tokens, 10)
        self.assertEqual(results[0]["translation_zh"], "第一条")
        self.assertEqual(results[1]["translation_zh"], "")
        self.assertEqual(results[1]["sentiment"], "中立")
        self.assertEqual(results[2]["translation_zh"], "第三条")
        self.assertEqual(results[2]["sentiment"], "正向")

    def test_ai_progress_reports_batch_numbers(self):
        original_batch = sentiment_insight.AI_BATCH_SIZE
        sentiment_insight.AI_BATCH_SIZE = 2
        comments = [
            {"_analysis_id": f"C{i}", "comment_id": f"p:c{i}", "text": f"comment {i}"}
            for i in range(3)
        ]
        progress_messages = []

        try:
            def ai_call(prompt, _timeout):
                payload = []
                section = prompt.split("《待处理评论》", 1)[1].split("只输出 JSON 数组", 1)[0]
                for line in section.splitlines():
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    data = json.loads(line)
                    payload.append(
                        {
                            "idx": data["idx"],
                            "id": data["id"],
                            "translation_zh": "翻译",
                            "sentiment": "中立",
                            "category": "其他",
                        }
                    )
                return json.dumps(payload, ensure_ascii=False), 1

            sentiment_insight._run_ai_for_comments(comments, ai_call, progress_messages.append)
        finally:
            sentiment_insight.AI_BATCH_SIZE = original_batch

        self.assertEqual(len(progress_messages), 2)
        self.assertIn("第 1/2 批", progress_messages[0])
        self.assertIn("第 2/2 批", progress_messages[1])

    def test_default_ai_batch_size_reduces_request_count(self):
        self.assertEqual(sentiment_insight.AI_BATCH_SIZE, 30)

    def test_resolve_facebook_share_url_cleans_redirect_url(self):
        response = Mock()
        response.headers = {
            "location": (
                "https://www.facebook.com/SolitaireClashAvia/posts/"
                "pfbid0PZNDkBbrYdTsQ8qLoUM2Z6ouqdGf8skhSWEud3xZFCX4ytHzSb3wz5yXjtJ7pasnl"
                "?rdid=1oSLtJZIMxGbaYMB&share_url=https%3A%2F%2Fwww.facebook.com%2Fshare%2Fp%2F185zpnK9rv%2F"
            )
        }

        with patch("sentiment_insight.requests.head", return_value=response):
            resolved = sentiment_insight._resolve_facebook_url("https://www.facebook.com/share/p/185zpnK9rv/")

        self.assertEqual(
            resolved,
            "https://www.facebook.com/SolitaireClashAvia/posts/"
            "pfbid0PZNDkBbrYdTsQ8qLoUM2Z6ouqdGf8skhSWEud3xZFCX4ytHzSb3wz5yXjtJ7pasnl",
        )

    def test_facebook_actor_does_not_repeat_fallback_schemas(self):
        starts = []

        def fake_start(_actor_id, run_input, _token):
            starts.append(run_input)
            return {"id": "run-1"}

        with patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}), \
             patch("sentiment_insight._fetch_dataset", return_value=[{"commentId": "c1", "commentText": "real comment"}]):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertEqual(len(starts), 1)
        self.assertEqual(result["items"], [{"commentId": "c1", "commentText": "real comment"}])

    def test_facebook_actor_tries_original_share_url_before_resolved_url(self):
        starts = []

        def fake_start(_actor_id, run_input, _token):
            starts.append(run_input)
            return {"id": f"run-{len(starts)}"}

        def fake_fetch(_dataset_id, _token):
            if len(starts) == 1:
                return [{"inputUrl": "https://www.facebook.com/share/p/abc/", "error": "Could not extract feedback ID"}]
            return [{"commentId": "c1", "commentText": "real comment"}]

        with patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}), \
             patch("sentiment_insight._fetch_dataset", side_effect=fake_fetch):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0], {
            "startUrls": [{"url": "https://www.facebook.com/share/p/abc/"}],
            "resultsLimit": 500,
            "includeNestedComments": True,
            "viewOption": "RANKED_UNFILTERED",
        })
        self.assertEqual(starts[1]["startUrls"], [{"url": "https://www.facebook.com/page/posts/pfbid1"}])
        self.assertEqual(result["items"], [{"commentId": "c1", "commentText": "real comment"}])
        self.assertEqual(result["actor_meta"]["run_id"], "run-2")
        self.assertEqual(result["actor_meta"]["attempts"][0]["accepted"], False)

    def test_scrape_summary_includes_actor_diagnostics_for_empty_facebook_items(self):
        payload = {
            "platform": "FB",
            "url": "https://www.facebook.com/share/p/abc/",
            "resolved_url": "https://www.facebook.com/page/posts/pfbid1",
            "items": [
                {
                    "inputUrl": "https://www.facebook.com/share/p/abc/",
                    "error": "Could not extract feedback ID",
                }
            ],
            "actor_meta": {
                "run_id": "run-1",
                "dataset_id": "dataset-1",
            },
        }

        summary = sentiment_insight._scrape_summary_item(1, payload)

        self.assertEqual(summary["item_count"], 1)
        self.assertEqual(summary["comment_count"], 0)
        self.assertEqual(summary["actor_run_id"], "run-1")
        self.assertEqual(summary["actor_dataset_id"], "dataset-1")
        self.assertIn("Could not extract feedback ID", summary["error"])

    def test_extract_comment_keeps_media_only_comment(self):
        item = {
            "commentId": "img1",
            "profileName": "player",
            "attachment": {"media": {"image": {"uri": "https://cdn.example.com/comment.jpg"}}},
        }

        comment = sentiment_insight._extract_comment(item, "FB", "https://www.facebook.com/page/posts/pfbid1")

        self.assertEqual(comment["comment_id"], "FB:img1")
        self.assertIn("图片评论", comment["text"])
        self.assertIn("https://cdn.example.com/comment.jpg", comment["text"])

    def test_extract_comment_supports_cookie_fallback_shape(self):
        item = {
            "source": {"url": "https://www.facebook.com/page/posts/pfbid1"},
            "comment": {
                "id": "c1",
                "text": "fallback comment",
                "author": {"name": "player"},
                "created_at": "2026-07-01T09:00:00.000Z",
                "url": "https://www.facebook.com/page/posts/pfbid1?comment_id=c1",
                "like_count": 7,
            },
        }

        comment = sentiment_insight._extract_comment(item, "FB", "https://www.facebook.com/page/posts/pfbid1")

        self.assertEqual(comment["comment_id"], "FB:c1")
        self.assertEqual(comment["author"], "player")
        self.assertEqual(comment["text"], "fallback comment")
        self.assertEqual(comment["like_count"], 7)
        self.assertEqual(comment["comment_url"], "https://www.facebook.com/page/posts/pfbid1?comment_id=c1")
        self.assertEqual(comment["created_str"], "2026-07-01 17:00")

    def test_extract_comment_supports_premiumscraper_shape(self):
        item = {
            "source_post_url": "https://www.facebook.com/page/posts/pfbid1",
            "comment_id": "c1",
            "comment_legacy_fbid": "legacy-c1",
            "message_text": "premium fallback comment",
            "comment_links": ["https://www.facebook.com/page/posts/pfbid1?comment_id=c1"],
            "author": {"profile_name": "player"},
            "engagement": {"reaction_count_total": 8},
            "created_at": "2026-07-01T09:00:00.000Z",
            "all_replies": [
                {
                    "comment_id": "r1",
                    "message_text": "nested reply",
                    "author": {"profile_name": "reply player"},
                    "created_at": "2026-07-01T09:01:00.000Z",
                }
            ],
        }

        flattened = sentiment_insight._flatten_comment_items(
            [item],
            "FB",
            "https://www.facebook.com/page/posts/pfbid1",
        )
        comments = [
            sentiment_insight._extract_comment(raw, "FB", "https://www.facebook.com/page/posts/pfbid1")
            for _, raw in flattened
        ]

        self.assertEqual([comment["text"] for comment in comments], ["premium fallback comment", "nested reply"])
        self.assertEqual(comments[0]["author"], "player")
        self.assertEqual(comments[0]["like_count"], 8)
        self.assertEqual(comments[0]["comment_url"], "https://www.facebook.com/page/posts/pfbid1?comment_id=c1")

    def test_facebook_cookie_fallback_runs_when_primary_has_no_comments(self):
        starts = []

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            return {"id": f"run-{len(starts)}"}

        def fake_fetch(_dataset_id, _token):
            if starts[-1][0] != sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID:
                return [{"url": "https://www.facebook.com/page/posts/pfbid1", "error": "not_available"}]
            return [
                {
                    "source": {"url": "https://www.facebook.com/page/posts/pfbid1"},
                    "comment": {"id": "c1", "text": "fallback comment", "author": {"name": "player"}},
                }
            ]

        with patch.dict(
            "os.environ",
            {"FB_COMMENT_FALLBACK_COOKIES_JSON": json.dumps([{"name": "c_user", "value": "1"}])},
        ), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}), \
             patch("sentiment_insight._fetch_dataset", side_effect=fake_fetch):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertEqual(len(starts), 3)
        self.assertEqual(starts[2][0], sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID)
        self.assertEqual(starts[2][1]["customCookies"], [{"name": "c_user", "value": "1"}])
        self.assertEqual(starts[2][1]["maxCommentsPerUrl"], 500)
        self.assertTrue(result["actor_meta"]["used_fallback"])
        self.assertEqual(result["actor_meta"]["input"]["customCookies"][0]["value"], "[REDACTED]")
        self.assertEqual(result["actor_meta"]["fallback"]["input"]["customCookies"][0]["value"], "[REDACTED]")
        self.assertEqual(result["actor_meta"]["attempts"][-1]["input"]["customCookies"][0]["value"], "[REDACTED]")
        self.assertEqual(result["items"][0]["comment"]["text"], "fallback comment")
        self.assertEqual(result["error"], "")

    def test_facebook_cookie_fallback_uses_resolved_post_url(self):
        starts = []

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            return {"id": f"run-{len(starts)}"}

        def fake_fetch(_dataset_id, _token):
            actor_id, run_input = starts[-1]
            if actor_id != sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID:
                return [{"url": "https://www.facebook.com/page/posts/pfbid1", "error": "not_available"}]
            if run_input["urls"] == [{"url": "https://www.facebook.com/page/posts/pfbid1"}]:
                return [
                    {
                        "source": {"url": "https://www.facebook.com/page/posts/pfbid1"},
                        "comment": {"id": "c1", "text": "fallback comment", "author": {"name": "player"}},
                    }
                ]
            return []

        with patch.dict(
            "os.environ",
            {"FB_COMMENT_FALLBACK_COOKIES_JSON": json.dumps([{"name": "c_user", "value": "1"}])},
        ), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}), \
             patch("sentiment_insight._fetch_dataset", side_effect=fake_fetch):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertEqual(starts[2][1]["urls"], [{"url": "https://www.facebook.com/page/posts/pfbid1"}])
        self.assertEqual(result["items"][0]["comment"]["text"], "fallback comment")
        self.assertEqual(result["error"], "")

    def test_facebook_cookie_fallback_preserves_empty_dataset_diagnostics(self):
        starts = []

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            return {"id": f"run-{len(starts)}"}

        def fake_fetch(_dataset_id, _token):
            if starts[-1][0] != sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID:
                return [{"url": "https://www.facebook.com/page/posts/pfbid1", "error": "not_available"}]
            return []

        with patch.dict(
            "os.environ",
            {"FB_COMMENT_FALLBACK_COOKIES_JSON": json.dumps([{"name": "c_user", "value": "1"}])},
        ), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-empty"}), \
             patch("sentiment_insight._fetch_dataset", side_effect=fake_fetch), \
             patch("sentiment_insight._fetch_actor_log", return_value=""):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertTrue(result["actor_meta"]["used_fallback"])
        self.assertEqual(result["actor_meta"]["dataset_id"], "dataset-empty")
        self.assertTrue(
            any(
                attempt.get("actor_id") == sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID
                and attempt.get("dataset_id") == "dataset-empty"
                for attempt in result["actor_meta"]["attempts"]
            )
        )
        self.assertIn("public fallback", result["error"])
        self.assertNotIn("所有候选 input 均失败: None", result["error"])

    def test_facebook_cookie_fallback_retries_mobile_url_variants(self):
        starts = []

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            return {"id": f"run-{len(starts)}"}

        def fake_fetch(_dataset_id, _token):
            actor_id, run_input = starts[-1]
            if actor_id != sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID:
                return [{"url": "https://www.facebook.com/page/posts/pfbid1", "error": "not_available"}]
            urls = [entry["url"] for entry in run_input["urls"]]
            if "https://m.facebook.com/page/posts/pfbid1" in urls:
                return [
                    {
                        "source": {"url": "https://m.facebook.com/page/posts/pfbid1"},
                        "comment": {"id": "c1", "text": "mobile fallback comment", "author": {"name": "player"}},
                    }
                ]
            return []

        with patch.dict(
            "os.environ",
            {"FB_COMMENT_FALLBACK_COOKIES_JSON": json.dumps([{"name": "c_user", "value": "1"}])},
        ), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}), \
             patch("sentiment_insight._fetch_dataset", side_effect=fake_fetch), \
             patch("sentiment_insight._fetch_actor_log", return_value=""):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        fallback_calls = [call for call in starts if call[0] == sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID]
        self.assertEqual(fallback_calls[0][1]["urls"], [{"url": "https://www.facebook.com/page/posts/pfbid1"}])
        second_urls = [entry["url"] for entry in fallback_calls[1][1]["urls"]]
        self.assertIn("https://m.facebook.com/page/posts/pfbid1", second_urls)
        self.assertIn("https://m.facebook.com/share/p/abc/", second_urls)
        self.assertNotIn("https://mbasic.facebook.com/page/posts/pfbid1", second_urls)
        self.assertEqual(result["items"][0]["comment"]["text"], "mobile fallback comment")
        self.assertEqual(result["error"], "")

    def test_facebook_cookie_fallback_empty_dataset_includes_log_diagnostics(self):
        starts = []
        cookie_value = "secret_cookie_value"

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            return {"id": f"run-{len(starts)}"}

        def fake_fetch(_dataset_id, _token):
            if starts[-1][0] != sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID:
                return [{"url": "https://www.facebook.com/page/posts/pfbid1", "error": "not_available"}]
            return []

        log_text = "\n".join(
            [
                "INFO Session active: Authenticated",
                f"WARN customCookies c_user={cookie_value}",
                "INFO feedbackId not found (attempt 1/4) — login wall likely",
                "ERROR Could not extract fb_dtsg from page",
            ]
        )

        with patch.dict(
            "os.environ",
            {"FB_COMMENT_FALLBACK_COOKIES_JSON": json.dumps([{"name": "c_user", "value": cookie_value}])},
        ), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-empty"}), \
             patch("sentiment_insight._fetch_dataset", side_effect=fake_fetch), \
             patch("sentiment_insight._fetch_actor_log", return_value=log_text):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertIn("log:", result["error"])
        self.assertIn("feedbackId not found", result["error"])
        self.assertIn("[REDACTED]", result["error"])
        self.assertNotIn(cookie_value, result["error"])
        meta_text = json.dumps(result["actor_meta"], ensure_ascii=False)
        self.assertIn("feedbackId not found", meta_text)
        self.assertNotIn(cookie_value, meta_text)

    def test_facebook_public_fallback_runs_when_cookie_fallback_empty(self):
        starts = []

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            return {"id": f"run-{len(starts)}"}

        def fake_fetch(_dataset_id, _token):
            actor_id, run_input = starts[-1]
            if actor_id == sentiment_insight.DEFAULT_ACTORS["FB"]:
                return [{"url": "https://www.facebook.com/page/posts/pfbid1", "error": "not_available"}]
            if actor_id == sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID:
                return []
            if actor_id == sentiment_insight.FB_PUBLIC_FALLBACK_ACTOR_ID:
                self.assertEqual(run_input["startUrls"], [
                    {"url": "https://www.facebook.com/share/p/abc/"},
                    {"url": "https://www.facebook.com/page/posts/pfbid1"},
                ])
                self.assertEqual(run_input["maxItems"], 500)
                return [
                    {
                        "facebookUrl": "https://www.facebook.com/page/posts/pfbid1",
                        "commentUrl": "https://www.facebook.com/page/posts/pfbid1?comment_id=c1",
                        "commentId": "c1",
                        "text": "public fallback comment",
                        "profileName": "player",
                        "likesCount": 4,
                        "date": "2026-07-01T09:00:00.000Z",
                    }
                ]
            return []

        with patch.dict(
            "os.environ",
            {"FB_COMMENT_FALLBACK_COOKIES_JSON": json.dumps([{"name": "c_user", "value": "1"}])},
        ), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}), \
             patch("sentiment_insight._fetch_dataset", side_effect=fake_fetch), \
             patch("sentiment_insight._fetch_actor_log", return_value=""):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertEqual(starts[-1][0], sentiment_insight.FB_PUBLIC_FALLBACK_ACTOR_ID)
        self.assertTrue(result["actor_meta"]["used_fallback"])
        self.assertEqual(result["items"][0]["text"], "public fallback comment")
        self.assertEqual(result["error"], "")

    def test_facebook_public_fallback_tries_premiumscraper_input(self):
        starts = []

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            return {"id": f"run-{len(starts)}"}

        def fake_fetch(_dataset_id, _token):
            actor_id, run_input = starts[-1]
            if actor_id == "premiumscraper/facebook-comments-scraper":
                self.assertEqual(run_input["facebook_urls"], [
                    {"url": "https://www.facebook.com/share/p/abc/"},
                    {"url": "https://www.facebook.com/page/posts/pfbid1"},
                ])
                self.assertEqual(run_input["comments_limit"], 500)
                self.assertEqual(run_input["comment_filter"], "all_comments")
                return [
                    {
                        "source_post_url": "https://www.facebook.com/page/posts/pfbid1",
                        "comment_id": "c1",
                        "message_text": "premium fallback comment",
                        "author": {"profile_name": "player"},
                    }
                ]
            return [{"url": "https://www.facebook.com/page/posts/pfbid1", "error": "not_available"}]

        with patch("sentiment_insight.FB_PUBLIC_FALLBACK_ACTOR_IDS", [
            "crawlerbros/facebook-comments-scraper",
            "premiumscraper/facebook-comments-scraper",
        ]), \
             patch("sentiment_insight._load_fb_comment_fallback_cookies", return_value=[]), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}), \
             patch("sentiment_insight._fetch_dataset", side_effect=fake_fetch), \
             patch("sentiment_insight._fetch_actor_log", return_value=""):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertEqual(starts[-1][0], "premiumscraper/facebook-comments-scraper")
        self.assertEqual(result["items"][0]["message_text"], "premium fallback comment")
        self.assertEqual(result["error"], "")

    def test_facebook_cookie_fallback_redacts_cookie_from_failure_metadata(self):
        starts = []
        cookie_value = "secret_cookie_value"

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            if actor_id == sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID:
                raise RuntimeError(f"bad cookie {cookie_value}")
            return {"id": "run-1"}

        with patch.dict(
            "os.environ",
            {"FB_COMMENT_FALLBACK_COOKIES_JSON": json.dumps([{"name": "c_user", "value": cookie_value}])},
        ), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}), \
             patch("sentiment_insight._fetch_dataset", return_value=[{"url": "https://www.facebook.com/page/posts/pfbid1", "error": "not_available"}]), \
             patch("sentiment_insight._fetch_actor_log", return_value=""):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        meta_text = json.dumps(result["actor_meta"], ensure_ascii=False)
        cookie_call = next(call for call in starts if call[0] == sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID)
        self.assertEqual(cookie_call[1]["customCookies"][0]["value"], cookie_value)
        self.assertNotIn(cookie_value, meta_text)
        self.assertIn("[REDACTED]", meta_text)

    def test_actor_log_summary_keeps_late_failure_lines(self):
        log_text = "\n".join(
            [
                "INFO Initialising session from user-provided cookies",
                "INFO Session established from user-provided cookies",
                "INFO Session active: YES",
                "WARN Rejected 2 URL(s) — only facebook.com URLs are accepted",
                "WARN invalid domain: mbasic.facebook.com",
                "WARN Could not extract page tokens",
                "ERROR Could not extract feedbackId from page HTML. Cannot proceed without it.",
            ]
        )

        summary = sentiment_insight._summarize_actor_log(log_text)

        self.assertIn("Session active", summary)
        self.assertIn("Could not extract feedbackId", summary)

    def test_facebook_cookie_fallback_runs_when_primary_actor_raises(self):
        starts = []

        def fake_start(actor_id, run_input, _token):
            starts.append((actor_id, run_input))
            if actor_id != sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID:
                raise RuntimeError("primary boom")
            return {"id": "run-fallback"}

        with patch.dict(
            "os.environ",
            {"FB_COMMENT_FALLBACK_COOKIES_JSON": json.dumps([{"name": "c_user", "value": "1"}])},
        ), \
             patch("sentiment_insight._resolve_facebook_url", return_value="https://www.facebook.com/page/posts/pfbid1"), \
             patch("sentiment_insight._start_actor", side_effect=fake_start), \
             patch("sentiment_insight._wait_actor", return_value={"status": "SUCCEEDED", "defaultDatasetId": "dataset-fallback"}), \
             patch("sentiment_insight._fetch_dataset", return_value=[
                 {
                     "source": {"url": "https://www.facebook.com/page/posts/pfbid1"},
                     "comment": {"id": "c1", "text": "fallback comment", "author": {"name": "player"}},
                 }
             ]):
            result = sentiment_insight._scrape_facebook("https://www.facebook.com/share/p/abc/", "token", 500)

        self.assertEqual(len(starts), 3)
        self.assertEqual(starts[-1][0], sentiment_insight.FB_COOKIE_FALLBACK_ACTOR_ID)
        self.assertTrue(result["actor_meta"]["used_fallback"])
        self.assertEqual(result["actor_meta"]["fallback"]["run_id"], "run-fallback")
        self.assertEqual(result["error"], "")

    def test_pipeline_flattens_nested_facebook_comments_from_single_post_item(self):
        original_scraper = sentiment_insight.PLATFORM_SCRAPERS["FB"]
        try:
            sentiment_insight.PLATFORM_SCRAPERS["FB"] = lambda _url, _token, _limit: {
                "platform": "FB",
                "url": "https://www.facebook.com/share/p/abc/",
                "items": [
                    {
                        "facebookUrl": "https://www.facebook.com/page/posts/pfbid1",
                        "postTitle": "Post shell",
                        "comments": [
                            {
                                "commentId": "c1",
                                "text": "first nested",
                                "profileName": "player one",
                                "likesCount": "2",
                            },
                            {
                                "commentId": "c2",
                                "text": "second nested",
                                "profileName": "player two",
                                "likesCount": "3",
                            },
                        ],
                    }
                ],
            }

            def ai_call(prompt, _timeout):
                payload = []
                section = prompt.split("《待处理评论》", 1)[1].split("只输出 JSON 数组", 1)[0]
                for line in section.splitlines():
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    data = json.loads(line)
                    payload.append({
                        "idx": data["idx"],
                        "id": data["id"],
                        "translation_zh": f"翻译{data['id']}",
                        "sentiment": "中立",
                        "category": "其他",
                    })
                return json.dumps(payload, ensure_ascii=False), 1

            result = sentiment_insight.run_insight_pipeline(
                ["https://www.facebook.com/share/p/abc/"],
                "token",
                ai_call,
                comments_per_post_limit=500,
            )

            self.assertEqual(result["total_comments"], 2)
            self.assertEqual([r["comment_id"] for r in result["structured"]], ["FB:c1", "FB:c2"])
            self.assertEqual(result["structured"][0]["post_url"], "https://www.facebook.com/page/posts/pfbid1")
            self.assertEqual(result["structured"][1]["comment_like_count"], 3)
        finally:
            sentiment_insight.PLATFORM_SCRAPERS["FB"] = original_scraper

    def test_pipeline_passes_comment_limit_to_scraper(self):
        original_scraper = sentiment_insight.PLATFORM_SCRAPERS["TT"]
        seen_limits = []
        try:
            def fake_scraper(_url, _token, comments_per_post_limit):
                seen_limits.append(comments_per_post_limit)
                return {
                    "platform": "TT",
                    "url": "https://www.tiktok.com/@game/video/123",
                    "items": [{"commentId": "one", "text": "first"}],
                }

            sentiment_insight.PLATFORM_SCRAPERS["TT"] = fake_scraper

            result = sentiment_insight.run_insight_pipeline(
                ["https://www.tiktok.com/@game/video/123"],
                "token",
                lambda _prompt, _timeout=60: (
                    json.dumps(
                        [
                            {
                                "idx": 0,
                                "id": "C0",
                                "translation_zh": "第一条",
                                "sentiment": "中立",
                                "category": "其他",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    1,
                ),
                comments_per_post_limit=sentiment_insight.UNLIMITED_COMMENTS_PER_POST_LIMIT,
            )

            self.assertEqual(seen_limits, [sentiment_insight.UNLIMITED_COMMENTS_PER_POST_LIMIT])
            self.assertEqual(result["total_comments"], 1)
        finally:
            sentiment_insight.PLATFORM_SCRAPERS["TT"] = original_scraper

    def test_pipeline_truncation_prioritizes_high_like_comments(self):
        original_max = sentiment_insight.MAX_AI_COMMENTS
        original_scraper = sentiment_insight.PLATFORM_SCRAPERS["TT"]
        sentiment_insight.MAX_AI_COMMENTS = 2

        try:
            sentiment_insight.PLATFORM_SCRAPERS["TT"] = lambda _url, _token: {
                "platform": "TT",
                "url": "https://www.tiktok.com/@game/video/123",
                "items": [
                    {"commentId": "low", "text": "low liked", "likesCount": 1},
                    {"commentId": "high", "text": "high liked", "likesCount": 500},
                    {"commentId": "mid", "text": "mid liked", "likesCount": 20},
                ],
            }

            def ai_call(prompt, _timeout):
                payload = []
                section = prompt.split("《待处理评论》", 1)[1].split("只输出 JSON 数组", 1)[0]
                for line in section.splitlines():
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    data = json.loads(line)
                    payload.append(
                        {
                            "idx": data["idx"],
                            "id": data["id"],
                            "translation_zh": f"翻译{data['id']}",
                            "sentiment": "中立",
                            "category": "其他",
                        }
                    )
                return json.dumps(payload, ensure_ascii=False), 5

            result = sentiment_insight.run_insight_pipeline(
                ["https://www.tiktok.com/@game/video/123"],
                "token",
                ai_call,
            )

            comments = [r["comment_id"] for r in result["structured"]]
            likes = [r["comment_like_count"] for r in result["structured"]]

            self.assertEqual(comments, ["TT:high", "TT:mid"])
            self.assertEqual(likes, [500, 20])
        finally:
            sentiment_insight.MAX_AI_COMMENTS = original_max
            sentiment_insight.PLATFORM_SCRAPERS["TT"] = original_scraper

    def test_build_excel_includes_comment_like_count_column(self):
        wb = sentiment_insight.build_excel(
            [
                {
                    "_schema": sentiment_insight.SCHEMA_VERSION,
                    "source_index": 1,
                    "platform": "TT",
                    "post_url": "https://www.tiktok.com/@game/video/123",
                    "post_date": "2026-06-18 12:00",
                    "post_title": "Post",
                    "comment_time": "2026-06-18 12:01",
                    "time_bucket": "中午",
                    "comment_like_count": 42,
                    "author": "player",
                    "content": "nice",
                    "translation_zh": "不错",
                    "sentiment_ai": "正向",
                    "sentiment_manual": "",
                    "category": "产品体验",
                    "comment_id": "TT:abc",
                    "comment_url": "https://www.tiktok.com/comment/abc",
                    "scrape_status": "成功",
                }
            ]
        )

        ws = wb[wb.sheetnames[0]]
        headers = [cell.value for cell in ws[1]]

        self.assertIn("评论点赞数", headers)
        self.assertEqual(ws.cell(row=2, column=headers.index("评论点赞数") + 1).value, 42)
        self.assertIn("来源序号", headers)
        self.assertIn("评论ID", headers)
        self.assertIn("评论链接", headers)
        self.assertIn("抓取状态", headers)
        self.assertEqual(ws.cell(row=2, column=headers.index("评论ID") + 1).value, "TT:abc")

    def test_run_insight_pipeline_reports_empty_scrape_summary(self):
        original_scraper = sentiment_insight.PLATFORM_SCRAPERS["TT"]
        try:
            sentiment_insight.PLATFORM_SCRAPERS["TT"] = lambda url, token: {
                "platform": "TT",
                "url": url,
                "items": [],
                "error": "启动 actor 失败 status=401",
            }

            result = sentiment_insight.run_insight_pipeline(
                ["https://www.tiktok.com/@game/video/123"],
                "token",
                lambda _prompt, _timeout=60: ("[]", 0),
            )

            self.assertEqual(result["structured"], [])
            self.assertEqual(result["scrape_summary"][0]["item_count"], 0)
            self.assertEqual(result["scrape_summary"][0]["comment_count"], 0)
            self.assertIn("401", result["scrape_summary"][0]["error"])
        finally:
            sentiment_insight.PLATFORM_SCRAPERS["TT"] = original_scraper


if __name__ == "__main__":
    unittest.main()
