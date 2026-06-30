import json
import unittest

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
