from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import Base
from app.models import DataRefreshJob, KOLRecord
from app.services import data_refresh_service


class DataRefreshServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine, future=True)
        self.tmp = tempfile.TemporaryDirectory()
        self.old_output_dir = data_refresh_service.OUTPUT_DIR
        data_refresh_service.OUTPUT_DIR = Path(self.tmp.name)

    def tearDown(self):
        data_refresh_service.OUTPUT_DIR = self.old_output_dir
        self.tmp.cleanup()

    def test_excel_refresh_preserves_original_columns_and_does_not_sync_by_default(self):
        db = self.Session()
        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Input"
            ws.append(["达人", "Link", "备注"])
            ws.append(["Alice", "https://www.tiktok.com/@alice", "keep me"])
            rows = data_refresh_service.rows_from_workbook(wb)
            job = DataRefreshJob(input_type="excel", status="running", total=len(rows), include_acv=1)
            db.add(job)
            db.commit()
            db.refresh(job)

            old_fetch = data_refresh_service._fetch_raw
            data_refresh_service._fetch_raw = lambda _records, _n: (
                {
                    "tiktok": [
                        {
                            "authorMeta": {"name": "alice", "fans": 1000},
                            "webVideoUrl": "https://www.tiktok.com/@alice/video/1",
                            "playCount": 200,
                            "avgLiveViewers": 55,
                        }
                    ],
                    "ins": [],
                    "youtube": [],
                },
                {},
            )
            try:
                data_refresh_service._run_refresh(db, job, rows, wb, False, True, 10)
            finally:
                data_refresh_service._fetch_raw = old_fetch

            self.assertEqual(db.query(KOLRecord).count(), 0)
            out = load_workbook(job.output_path)
            out_ws = out["Input"]
            self.assertEqual(out_ws.cell(2, 1).value, "Alice")
            self.assertEqual(out_ws.cell(2, 3).value, "keep me")
            headers = [out_ws.cell(1, col).value for col in range(1, out_ws.max_column + 1)]
            for header in data_refresh_service.OUTPUT_COLUMNS:
                self.assertIn(header, headers)
            self.assertEqual(out_ws.cell(2, headers.index("粉丝数") + 1).value, 1000)
            self.assertEqual(out_ws.cell(2, headers.index("AVV / 均观看量") + 1).value, 200)
            self.assertEqual(out_ws.cell(2, headers.index("ACV / 平均直播观看人数") + 1).value, 55)
            self.assertEqual(out_ws.cell(2, headers.index("抓取状态") + 1).value, "成功")
        finally:
            db.close()

    def test_sync_to_pool_adds_record_and_acv_only_when_explicitly_returned(self):
        db = self.Session()
        try:
            rows = data_refresh_service.rows_from_links("https://www.youtube.com/@creator")
            job = DataRefreshJob(input_type="links", status="running", total=len(rows), include_acv=1)
            db.add(job)
            db.commit()
            db.refresh(job)

            old_fetch = data_refresh_service._fetch_raw
            data_refresh_service._fetch_raw = lambda _records, _n: (
                {
                    "youtube": [
                        {
                            "channelUsername": "@creator",
                            "numberOfSubscribers": 5000,
                            "viewCount": 300,
                        }
                    ],
                    "tiktok": [],
                    "ins": [],
                },
                {},
            )
            try:
                data_refresh_service._run_refresh(db, job, rows, None, True, True, 10)
            finally:
                data_refresh_service._fetch_raw = old_fetch

            record = db.query(KOLRecord).one()
            self.assertEqual(record.yt_follower, 5000)
            self.assertEqual(record.yt_avv, 300)
            self.assertIsNone(record.yt_acv)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
