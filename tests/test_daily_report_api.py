import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import app
from src.utils.db_manager import SentimentDB


class DailyReportApiTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.original_sqlite_path = os.environ.get("SQLITE_PATH")
        os.environ["SQLITE_PATH"] = path
        self.db = SentimentDB(db_path=path)
        self.client = TestClient(app)

        self.db.save_intel_daily_report(
            {
                "report_date": "2026-05-26",
                "scope_type": "brand",
                "scope_key": "7-ELEVEN",
                "snapshot_at": "2026-05-27T08:00:00+08:00",
                "headline_summary": "昨日 7-ELEVEN 輿情以商品反饋與價格討論為主。",
                "payload_json": "{\"section_order\": [\"product_feedback\"]}",
            }
        )
        self.db.save_intel_daily_report_section(
            {
                "report_date": "2026-05-26",
                "scope_key": "7-ELEVEN",
                "section_key": "product_feedback",
                "section_label": "商品反饋",
                "signal_count": 3,
                "pos_count": 1,
                "neu_count": 1,
                "neg_count": 1,
                "high_risk_count": 0,
                "summary_text": "鮮食與聯名商品的口味和配料調整討論較多。",
                "top_threads_json": "[{\"title\": \"[商品] 日東紅茶 皇家奶茶\", \"channel\": \"ptt\"}]",
                "evidence_quotes_json": "[{\"quote\": \"沒有筍乾沒有蔥\"}]",
                "payload_json": "{}",
            }
        )

    def tearDown(self):
        if self.original_sqlite_path is None:
            os.environ.pop("SQLITE_PATH", None)
        else:
            os.environ["SQLITE_PATH"] = self.original_sqlite_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_daily_report_endpoint_returns_headline_and_sections(self):
        resp = self.client.get("/daily-report?date=2026-05-26&scope_key=7-ELEVEN")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["report_date"], "2026-05-26")
        self.assertEqual(body["scope_key"], "7-ELEVEN")
        self.assertEqual(body["headline_summary"], "昨日 7-ELEVEN 輿情以商品反饋與價格討論為主。")
        self.assertEqual(len(body["sections"]), 1)
        self.assertEqual(body["sections"][0]["section_key"], "product_feedback")
        self.assertIn("top_threads", body["sections"][0])
        self.assertIn("evidence_quotes", body["sections"][0])


if __name__ == "__main__":
    unittest.main()
