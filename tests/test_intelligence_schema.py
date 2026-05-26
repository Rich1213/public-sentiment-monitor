import os
import tempfile
import unittest

from src.utils.db_manager import SentimentDB


class IntelligenceSchemaTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.db = SentimentDB(db_path=path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _table_exists(self, table_name: str) -> bool:
        conn = self.db._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            )
            return c.fetchone() is not None
        finally:
            conn.close()

    def test_schema_migrations_create_intelligence_tables(self):
        for table in (
            "intel_event_cases",
            "intel_event_case_threads",
            "intel_topics",
            "intel_topic_events",
            "intel_monthly_snapshots",
        ):
            self.assertTrue(self._table_exists(table), table)

    def test_save_intel_event_case_roundtrip(self):
        case_id = self.db.save_intel_event_case(
            {
                "id": "evt_test_001",
                "keyword": "7-ELEVEN",
                "canonical_theme": "便當價格",
                "label": "便當價格討論",
                "status": "active",
                "severity": 2,
                "first_seen_at": "2026-05-01T08:00:00+08:00",
                "last_seen_at": "2026-05-05T09:00:00+08:00",
                "evidence_count": 3,
                "source_mix_json": "{\"PTT\": 1, \"Dcard\": 2}",
                "sentiment_mix_json": "{\"負面\": 2, \"中立\": 1}",
                "metadata_json": "{\"origin\": \"test\"}",
            }
        )

        row = self.db.get_intel_event_case(case_id)

        self.assertEqual(row["label"], "便當價格討論")
        self.assertEqual(row["keyword"], "7-ELEVEN")
        self.assertEqual(row["evidence_count"], 3)

    def test_save_intel_topic_roundtrip(self):
        topic_id = self.db.save_intel_topic(
            {
                "id": "topic_test_001",
                "scope_key": "7-ELEVEN",
                "canonical_theme": "便當價格",
                "label": "便當價格",
                "first_seen_at": "2026-05-01T08:00:00+08:00",
                "last_seen_at": "2026-05-31T09:00:00+08:00",
                "event_count": 2,
                "signal_count": 5,
                "sentiment_mix_json": "{\"負面\": 4, \"中立\": 1}",
                "source_mix_json": "{\"PTT\": 2, \"Dcard\": 3}",
                "metadata_json": "{\"window\": \"2026-05\"}",
            }
        )

        row = self.db.get_intel_topic(topic_id)

        self.assertEqual(row["label"], "便當價格")
        self.assertEqual(row["signal_count"], 5)

    def test_save_intel_monthly_snapshot_roundtrip(self):
        snapshot_id = self.db.save_intel_monthly_snapshot(
            {
                "snapshot_month": "2026-05",
                "scope_type": "brand",
                "scope_key": "7-ELEVEN",
                "snapshot_at": "2026-06-01T00:05:00+08:00",
                "active_risks_json": "[{\"label\": \"便當價格\"}]",
                "opportunity_topics_json": "[{\"label\": \"超商文化\"}]",
                "top_topics_json": "[{\"label\": \"便當價格\"}]",
                "competitive_matrix_json": "{\"便當價格\": {\"7-ELEVEN\": 5}}",
                "narrative_summary": "本月主要議題為便當價格。",
                "payload_json": "{\"topics\": []}",
            }
        )

        row = self.db.get_intel_monthly_snapshot("2026-05", "brand", "7-ELEVEN")

        self.assertIsInstance(snapshot_id, int)
        self.assertEqual(row["snapshot_month"], "2026-05")
        self.assertEqual(row["narrative_summary"], "本月主要議題為便當價格。")


if __name__ == "__main__":
    unittest.main()
