import os
import tempfile
import unittest

from src.jobs.monthly_intelligence_snapshot_job import run_monthly_intelligence_snapshot_capture
from src.utils.db_manager import SentimentDB


class MonthlyIntelligenceSnapshotJobTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.db = SentimentDB(db_path=path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_capture_monthly_snapshot_writes_brand_and_market_rows(self):
        self.db.save_intel_topic(
            {
                "id": "topic_7",
                "scope_key": "7-ELEVEN",
                "canonical_theme": "便當價格",
                "label": "便當價格",
                "first_seen_at": "2026-05-01T08:00:00+08:00",
                "last_seen_at": "2026-05-18T09:00:00+08:00",
                "event_count": 2,
                "signal_count": 5,
                "sentiment_mix_json": "{\"負面\": 4, \"中立\": 1}",
                "source_mix_json": "{\"PTT\": 2, \"Dcard\": 3}",
                "metadata_json": "{}",
            }
        )
        self.db.save_intel_topic(
            {
                "id": "topic_market",
                "scope_key": "market",
                "canonical_theme": "超商文化",
                "label": "超商文化",
                "first_seen_at": "2026-05-02T08:00:00+08:00",
                "last_seen_at": "2026-05-19T09:00:00+08:00",
                "event_count": 1,
                "signal_count": 4,
                "sentiment_mix_json": "{\"正面\": 2, \"中立\": 2}",
                "source_mix_json": "{\"Threads\": 4}",
                "metadata_json": "{}",
            }
        )

        result = run_monthly_intelligence_snapshot_capture(
            db=self.db,
            snapshot_month="2026-05",
            scope_keys=["7-ELEVEN", "market"],
        )

        self.assertEqual(result["snapshot_month"], "2026-05")
        self.assertGreaterEqual(result["written"], 2)
        brand_row = self.db.get_intel_monthly_snapshot("2026-05", "brand", "7-ELEVEN")
        market_row = self.db.get_intel_monthly_snapshot("2026-05", "market", "market")
        self.assertIsNotNone(brand_row)
        self.assertIsNotNone(market_row)


if __name__ == "__main__":
    unittest.main()
