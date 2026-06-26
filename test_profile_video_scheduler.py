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
    records = []
    found = {}

    def find_existing_records(self, _app_token, _table_id, video_keys):
        return {"TT:old": "rec_old"} if "TT:old" in video_keys else {}

    def find_records_by_field(self, _app_token, _table_id, _field_name, values):
        return {str(v): self.found[str(v)] for v in values if str(v) in self.found}

    def list_records(self, _app_token, _table_id, page_size=500):
        return list(self.records)

    def batch_create_records(self, _app_token, _table_id, fields_list):
        self.created.extend(fields_list)
        return [f"rec_new_{idx}" for idx, _ in enumerate(fields_list)]

    def batch_update_records(self, _app_token, _table_id, records):
        self.updated.extend(records)


class ProfileVideoSchedulerTests(unittest.TestCase):
    def setUp(self):
        FakeFeishuClient.created = []
        FakeFeishuClient.updated = []
        FakeFeishuClient.records = []
        FakeFeishuClient.found = {}

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

        with patch.dict(profile_video_scheduler.os.environ, {"PROFILE_VIDEO_SYNC_ENABLED": "true"}, clear=False), patch.object(
            profile_video_scheduler, "_load_task_configs", return_value=[]
        ), patch.object(
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

    def test_profile_video_sync_disabled_does_not_run(self):
        updates = []

        def update_task(_task_id, **kwargs):
            updates.append(kwargs)

        with patch.dict(profile_video_scheduler.os.environ, {"PROFILE_VIDEO_SYNC_ENABLED": "false"}, clear=False), patch.object(
            profile_video_scheduler.video_metrics_etl, "fetch_profile_video_metrics"
        ) as fetch_metrics:
            profile_video_scheduler.run_profile_video_sync_task("task-disabled", {}, update_task)

        self.assertEqual(updates[-1]["status"], "failed")
        self.assertIn("PROFILE_VIDEO_SYNC_ENABLED=true", updates[-1]["error"])
        fetch_metrics.assert_not_called()

    def test_load_feishu_configs_parses_enabled_profile_rows(self):
        FakeFeishuClient.records = [
            {
                "record_id": "rec1",
                "fields": {
                    "是否启用": True,
                    "平台": "TikTok",
                    "达人主页链接": "https://www.tiktok.com/@demo/",
                    "达人名称": "Demo",
                    "抓取范围": "近N天",
                    "近N天": 3,
                    "最大视频数": 20,
                    "抓取小时": 9,
                    "项目/品牌": "PUBG",
                },
            },
            {
                "record_id": "rec2",
                "fields": {
                    "是否启用": False,
                    "平台": "Instagram",
                    "达人主页链接": "https://www.instagram.com/off",
                },
            },
        ]
        with patch.dict(
            profile_video_scheduler.os.environ,
            {
                "FEISHU_VIDEO_BASE_TOKEN": "base",
                "FEISHU_VIDEO_CONFIG_TABLE_ID": "cfg",
                "FEISHU_VIDEO_LATEST_TABLE_ID": "latest",
                "FEISHU_VIDEO_SNAPSHOT_TABLE_ID": "snap",
                "FEISHU_VIDEO_LOG_TABLE_ID": "log",
            },
            clear=False,
        ), patch.object(profile_video_scheduler, "FeishuBitableClient", return_value=FakeFeishuClient()):
            rows = profile_video_scheduler.load_feishu_profile_configs(enabled_only=True)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "TT")
        self.assertEqual(rows[0]["sync_scope"], "recent")
        self.assertEqual(rows[0]["recent_days"], 3)
        self.assertEqual(rows[0]["max_videos"], 20)
        self.assertEqual(rows[0]["creator_key"], "TT:@demo")

    def test_feishu_profile_sync_disabled_does_not_enqueue(self):
        with patch.dict(profile_video_scheduler.os.environ, {"FEISHU_PROFILE_VIDEO_SYNC_ENABLED": "false"}, clear=False):
            task_ids = profile_video_scheduler.enqueue_due_feishu_profile_video_sync(
                lambda *args, **kwargs: None,
                update_task_params_fn=lambda *args, **kwargs: None,
                hour=9,
            )

        self.assertEqual(task_ids, [])

    def test_feishu_profile_sync_blocks_oversized_manual_run(self):
        updates = []

        def update_task(_task_id, **kwargs):
            updates.append(kwargs)

        configs = [
            {"record_id": f"rec{i}", "profile_url": f"https://www.tiktok.com/@demo{i}", "platform": "TT", "enabled": True}
            for i in range(3)
        ]

        with patch.dict(
            profile_video_scheduler.os.environ,
            {
                "FEISHU_PROFILE_VIDEO_SYNC_ENABLED": "true",
                "FEISHU_VIDEO_BASE_TOKEN": "base",
                "FEISHU_VIDEO_CONFIG_TABLE_ID": "cfg",
                "FEISHU_VIDEO_LATEST_TABLE_ID": "latest",
                "FEISHU_VIDEO_SNAPSHOT_TABLE_ID": "snap",
                "FEISHU_VIDEO_LOG_TABLE_ID": "log",
            },
            clear=False,
        ), patch.object(profile_video_scheduler, "_configs_for_feishu_task", return_value=configs), patch.object(
            profile_video_scheduler.video_metrics_etl, "fetch_profile_video_metrics"
        ) as fetch_metrics:
            profile_video_scheduler.run_feishu_profile_video_sync_task(
                "task-oversize",
                {"trigger_type": "manual", "max_profiles_per_run": 2},
                update_task,
            )

        self.assertEqual(updates[-1]["status"], "failed")
        self.assertIn("超过安全上限 2", updates[-1]["error"])
        fetch_metrics.assert_not_called()

    def test_clean_max_videos_uses_hard_cap(self):
        with patch.object(profile_video_scheduler, "DEFAULT_PROFILE_VIDEO_HARD_MAX_VIDEOS", 30):
            self.assertEqual(profile_video_scheduler._clean_max_videos(500), 30)

    def test_sync_rows_to_feishu_video_tables_writes_latest_and_snapshot(self):
        rows = [
            {
                "video_key": "TT:123",
                "creator_key": "TT:demo",
                "profile_url": "https://www.tiktok.com/@demo",
                "platform": "TT",
                "video_url": "https://www.tiktok.com/@demo/video/123",
                "author": "demo",
                "post_date": "2026-06-25",
                "caption": "#tag hi",
                "duration": "00:30",
                "views": 100,
                "likes": 10,
                "comments": 2,
                "shares": 1,
                "collects": 3,
                "engagement": 13,
                "followers": 1000,
                "project": "PUBG",
            }
        ]
        with patch.object(profile_video_scheduler, "FeishuBitableClient", return_value=FakeFeishuClient()):
            result = profile_video_scheduler.sync_rows_to_feishu_video_tables(
                rows,
                task_id="task-1",
                table_config={
                    "base_token": "base",
                    "latest_table_id": "latest",
                    "snapshot_table_id": "snap",
                    "log_table_id": "log",
                },
                client=FakeFeishuClient(),
            )

        self.assertEqual(result["latest_created"], 1)
        self.assertEqual(result["snapshot_created"], 1)
        latest_fields = FakeFeishuClient.created[0]
        snapshot_fields = FakeFeishuClient.created[1]
        self.assertEqual(latest_fields["视频唯一键"], "TT:123")
        self.assertEqual(latest_fields["平台"], "TikTok")
        self.assertEqual(latest_fields["Hashtag"], "#tag")
        self.assertEqual(snapshot_fields["抓取任务ID"], "task-1")
        self.assertEqual(snapshot_fields["视频链接"], "https://www.tiktok.com/@demo/video/123")
        self.assertIn("快照唯一键", snapshot_fields)

    def test_sync_rows_to_feishu_video_tables_calculates_growth_from_snapshots(self):
        rows = [
            {
                "video_key": "TT:123",
                "creator_key": "TT:demo",
                "profile_url": "https://www.tiktok.com/@demo",
                "platform": "TT",
                "video_url": "https://www.tiktok.com/@demo/video/123",
                "views": 180,
                "likes": 20,
                "comments": 4,
                "shares": 1,
                "engagement": 25,
            }
        ]

        def local_state(keys, _app_token, _table_id):
            out = {}
            for key in keys:
                if key.endswith(":2026-06-25"):
                    out[key] = {"views": 100, "engagement": 10}
                if key.endswith(":2026-06-19"):
                    out[key] = {"views": 40, "engagement": 5}
            return out

        with patch.object(profile_video_scheduler, "FeishuBitableClient", return_value=FakeFeishuClient()), patch.object(
            profile_video_scheduler, "_today_text", return_value="2026-06-26"
        ), patch.object(profile_video_scheduler, "_local_state_rows", side_effect=local_state):
            profile_video_scheduler.sync_rows_to_feishu_video_tables(
                rows,
                task_id="task-1",
                table_config={
                    "base_token": "base",
                    "latest_table_id": "latest",
                    "snapshot_table_id": "snap",
                    "log_table_id": "log",
                },
                client=FakeFeishuClient(),
            )

        latest_fields = FakeFeishuClient.created[0]
        snapshot_fields = FakeFeishuClient.created[1]
        self.assertEqual(latest_fields["近1日新增播放"], 80)
        self.assertEqual(latest_fields["近7日新增播放"], 140)
        self.assertEqual(snapshot_fields["日增播放"], 80)
        self.assertEqual(snapshot_fields["日增互动"], 15)
        self.assertEqual(snapshot_fields["互动率"], round(25 / 180, 6))


if __name__ == "__main__":
    unittest.main()
