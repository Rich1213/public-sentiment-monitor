import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from api.app import app
from src.utils.db_manager import SentimentDB


class IntelligenceApiTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.original_sqlite_path = os.environ.get("SQLITE_PATH")
        os.environ["SQLITE_PATH"] = path
        self.db = SentimentDB(db_path=path)
        self.client = TestClient(app)

        self.db.save_intel_event_case(
            {
                "id": "evt_api_001",
                "keyword": "7-ELEVEN",
                "canonical_theme": "便當價格",
                "label": "便當價格討論",
                "status": "active",
                "severity": 3,
                "first_seen_at": "2026-05-01T08:00:00+08:00",
                "last_seen_at": "2026-05-18T09:00:00+08:00",
                "evidence_count": 2,
                "source_mix_json": "{\"PTT\": 1}",
                "sentiment_mix_json": "{\"負面\": 2}",
                "metadata_json": "{}",
            }
        )
        self.db.save_intel_topic(
            {
                "id": "topic_api_001",
                "scope_key": "7-ELEVEN",
                "canonical_theme": "便當價格",
                "label": "便當價格",
                "first_seen_at": "2026-05-01T08:00:00+08:00",
                "last_seen_at": "2026-05-18T09:00:00+08:00",
                "event_count": 1,
                "signal_count": 2,
                "sentiment_mix_json": "{\"負面\": 2}",
                "source_mix_json": "{\"PTT\": 1}",
                "metadata_json": "{\"event_case_ids\": [\"evt_api_001\"]}",
            }
        )
        self.db.bind_event_case_to_intel_topic(
            topic_id="topic_api_001",
            event_case_id="evt_api_001",
            first_bound_at="2026-05-01T08:00:00+08:00",
            last_bound_at="2026-05-18T09:00:00+08:00",
        )
        self.db.save_intel_monthly_snapshot(
            {
                "snapshot_month": "2026-05",
                "scope_type": "brand",
                "scope_key": "7-ELEVEN",
                "snapshot_at": "2026-06-01T00:05:00+08:00",
                "active_risks_json": "[{\"label\": \"便當價格\"}]",
                "opportunity_topics_json": "[]",
                "top_topics_json": "[{\"label\": \"便當價格\"}]",
                "competitive_matrix_json": "{\"便當價格\": {\"7-ELEVEN\": 2}}",
                "narrative_summary": "本月主要議題為便當價格。",
                "payload_json": "{\"topics\": []}",
            }
        )

    def tearDown(self):
        if self.original_sqlite_path is None:
            os.environ.pop("SQLITE_PATH", None)
        else:
            os.environ["SQLITE_PATH"] = self.original_sqlite_path
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_intelligence_topics_endpoint_returns_period_scoped_topics(self):
        resp = self.client.get("/intelligence/topics?days=30&scope_key=7-ELEVEN")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("topics", body)
        self.assertIn("count", body)
        self.assertEqual(body["count"], 1)

    def test_intelligence_snapshot_endpoint_returns_monthly_snapshot(self):
        resp = self.client.get("/intelligence/snapshots/monthly?month=2026-05&scope_key=7-ELEVEN")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["snapshot_month"], "2026-05")
        self.assertEqual(body["scope_key"], "7-ELEVEN")

    def test_intelligence_topic_detail_returns_bound_events(self):
        resp = self.client.get("/intelligence/topics/topic_api_001")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["topic"]["id"], "topic_api_001")
        self.assertEqual(len(body["events"]), 1)

    def test_intelligence_event_detail_returns_bound_threads(self):
        resp = self.client.get("/intelligence/events/evt_api_001")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["event_case"]["id"], "evt_api_001")
        self.assertIn("threads", body)


if __name__ == "__main__":
    unittest.main()
