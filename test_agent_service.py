import unittest

import agent_service


class AgentServiceTests(unittest.TestCase):
    def test_detects_sentiment_comment_task(self):
        draft = agent_service.build_draft(
            "帮我分析这个视频评论舆情 https://www.tiktok.com/@game/video/123",
            qwen_client=None,
        )

        self.assertEqual(draft.intent, "sentiment_comments")
        self.assertTrue(draft.needs_confirmation)
        self.assertEqual(draft.card["link_count"], 1)
        self.assertEqual(draft.card["platform_counts"]["TT"], 1)

    def test_detects_video_metrics_task(self):
        draft = agent_service.build_draft(
            "拉一下播放量和基础视频数据 https://vt.tiktok.com/ZSQoKaT60/",
            qwen_client=None,
        )

        self.assertEqual(draft.intent, "video_metrics")
        self.assertEqual(draft.card["task_type"], "拉视频数据")

    def test_normalize_keeps_youtube_watch_query(self):
        url = agent_service.normalize_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=x")

        self.assertEqual(url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=x")

    def test_detects_profile_sync_with_schedule_and_feishu(self):
        draft = agent_service.build_draft(
            "每天 9 点同步这些主页近 14 天视频数据到飞书 https://www.youtube.com/@YouTube",
            qwen_client=None,
        )

        self.assertEqual(draft.intent, "profile_video_sync")
        self.assertTrue(draft.params["schedule"])
        self.assertTrue(draft.params["write_feishu"])
        self.assertEqual(draft.params["schedule_hour"], 9)
        self.assertEqual(draft.params["recent_days"], 14)
        self.assertIn("飞书多维表格", draft.card["write_target"])

    def test_detects_kol_refresh_without_pool_sync(self):
        draft = agent_service.build_draft(
            "帮我更新这些达人粉丝、AVV、ACV，先只导出 Excel 不入库 https://www.youtube.com/@YouTube",
            qwen_client=None,
        )

        self.assertEqual(draft.intent, "kol_data_refresh_links")
        self.assertFalse(draft.params["sync_to_pool"])
        self.assertTrue(draft.params["include_acv"])
        self.assertIn("仅导出 Excel", draft.card["write_target"])

    def test_parse_llm_json_from_fenced_block(self):
        payload = agent_service.parse_llm_json(
            '```json\n{"intent":"task_query","params":{"schedule":false}}\n```'
        )

        self.assertEqual(payload["intent"], "task_query")
        self.assertFalse(payload["params"]["schedule"])


if __name__ == "__main__":
    unittest.main()
