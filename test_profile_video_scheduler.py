import sys
import types
import unittest
from unittest.mock import patch

fake_tasks = types.ModuleType("tasks")
fake_tasks.APIFY_TOKEN = "token"
sys.modules.setdefault("tasks", fake_tasks)

fake_db = types.ModuleType("database")
fake_db.query_all = lambda *args, **kwargs: []
fake_db.execute = lambda *args, **kwargs: 0
fake_db.execute_and_fetch_one = lambda *args, **kwargs: None
fake_db.query_one = lambda *args, **kwargs: None
sys.modules.setdefault("database", fake_db)

fake_usage = types.ModuleType("usage_service")
fake_usage.record_usage_event = lambda *args, **kwargs: None
sys.modules.setdefault("usage_service", fake_usage)

import profile_video_scheduler


class FakeFeishuClient:
    created = []
    updated = []

    def find_existing_records(self, _app_token, _table_id, video_keys):
        return {"TT:old": "rec_old"} if "TT:old" in video_keys else {}

    def batch_create_records(self, _app_token, _table_id, fields_list):
        self.created.extend(fields_list)
        return [f"rec_new_{idx}" for idx, _ in enumerate(fields_list)]

    def batch_update_records(self, _app_token, _table_id, records):
        self.updated.extend(records)


class ProfileVideoSchedulerTests(unittest.TestCase):
    def setUp(self):
        FakeFeishuClient.created = []
        FakeFeishuClient.updated = []

    def test_sync_rows_to_feishu_creates_and_updates_by_video_key(self):
        rows = [
            {
                "video_key": "TT:new",
                "profile_url": "https://www.tiktok.com/@a",
                "platform": "TT",
                "video_url": "https://www.tiktok.com/@a/video/1",
                "views": 10,
            },
            {
                "video_key": "TT:old",
                "profile_url": "https://www.tiktok.com/@a",
                "platform": "TT",
                "video_url": "https://www.tiktok.com/@a/video/2",
                "views": 20,
            },
        ]

        with patch.object(profile_video_scheduler, "FeishuBitableClient", return_value=FakeFeishuClient()), patch.object(
            profile_video_scheduler, "_local_existing_records", return_value={}
        ), patch.object(profile_video_scheduler, "_upsert_video_state") as upsert_state:
            result = profile_video_scheduler.sync_rows_to_feishu(rows, app_token="app", table_id="tbl")

        self.assertEqual(result, {"created": 1, "updated": 1})
        self.assertEqual(FakeFeishuClient.created[0]["视频唯一键"], "TT:new")
        self.assertEqual(FakeFeishuClient.updated[0]["record_id"], "rec_old")
        self.assertEqual(upsert_state.call_count, 2)

    def test_run_task_accepts_inline_configs_for_agent_oneoff_sync(self):
        updates = []

        def update_task(_task_id, **kwargs):
            updates.append(kwargs)

        rows = [
            {
                "video_key": "TT:new",
                "profile_url": "https://www.tiktok.com/@a",
                "platform": "TT",
                "video_url": "https://www.tiktok.com/@a/video/1",
                "views": 10,
            }
        ]

        with patch.object(profile_video_scheduler, "_load_task_configs", return_value=[]), patch.object(
            profile_video_scheduler, "_configs_missing_feishu_target", return_value=[]
        ), patch.object(profile_video_scheduler.video_metrics_etl, "fetch_profile_video_metrics", return_value=rows), patch.object(
            profile_video_scheduler, "sync_rows_to_feishu", return_value={"created": 1, "updated": 0}
        ), patch.object(profile_video_scheduler, "mark_task_for_configs"), patch.object(
            profile_video_scheduler, "_mark_config_results"
        ):
            profile_video_scheduler.run_profile_video_sync_task(
                "task-1",
                {
                    "user_id": 1,
                    "trigger_type": "agent",
                    "inline_configs": [
                        {
                            "profile_url": "https://www.tiktok.com/@a",
                            "sync_scope": "range",
                            "start_date": "2026-06-01",
                            "end_date": "2026-06-24",
                            "max_videos": 5,
                        }
                    ],
                },
                update_task,
            )

        self.assertEqual(updates[-1]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
