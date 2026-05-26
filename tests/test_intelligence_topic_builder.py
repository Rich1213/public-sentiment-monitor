import os
import tempfile
import unittest

from src.intelligence.topic_builder import TopicBuilder
from src.utils.db_manager import SentimentDB


class TopicBuilderTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.db = SentimentDB(db_path=path)
        self.builder = TopicBuilder(self.db)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_build_topics_rolls_multiple_event_cases_into_one_long_horizon_topic(self):
        event_cases = [
            {"id": "evt_001", "keyword": "7-ELEVEN", "canonical_theme": "便當價格", "label": "便當價格討論", "first_seen_at": "2026-05-01T08:00:00+08:00", "last_seen_at": "2026-05-03T09:00:00+08:00", "evidence_count": 2, "source_mix_json": "{\"PTT\": 1}", "sentiment_mix_json": "{\"負面\": 2}"},
            {"id": "evt_002", "keyword": "7-ELEVEN", "canonical_theme": "便當價格", "label": "便當漲價再討論", "first_seen_at": "2026-05-15T10:00:00+08:00", "last_seen_at": "2026-05-18T11:00:00+08:00", "evidence_count": 3, "source_mix_json": "{\"Dcard\": 2}", "sentiment_mix_json": "{\"負面\": 2, \"中立\": 1}"},
        ]

        topics = self.builder.build_topics(event_cases)

        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["event_count"], 2)
        self.assertEqual(topics[0]["signal_count"], 5)

    def test_competitive_matrix_separates_brand_scopes(self):
        rows = [
            {"scope_key": "7-ELEVEN", "canonical_theme": "便當價格", "signal_count": 4},
            {"scope_key": "全家", "canonical_theme": "便當價格", "signal_count": 2},
        ]

        matrix = self.builder.build_competitive_matrix(rows)

        self.assertEqual(matrix["便當價格"]["7-ELEVEN"], 4)
        self.assertEqual(matrix["便當價格"]["全家"], 2)

    def test_project_recent_topics_persists_topic_and_event_bindings(self):
        self.db.save_intel_event_case(
            {
                "id": "evt_001",
                "keyword": "7-ELEVEN",
                "canonical_theme": "便當價格",
                "label": "便當價格討論",
                "status": "active",
                "severity": 3,
                "first_seen_at": "2026-05-01T08:00:00+08:00",
                "last_seen_at": "2026-05-03T09:00:00+08:00",
                "evidence_count": 2,
                "source_mix_json": "{\"PTT\": 1}",
                "sentiment_mix_json": "{\"負面\": 2}",
                "metadata_json": "{}",
            }
        )
        self.db.save_intel_event_case(
            {
                "id": "evt_002",
                "keyword": "7-ELEVEN",
                "canonical_theme": "便當價格",
                "label": "便當漲價再討論",
                "status": "active",
                "severity": 4,
                "first_seen_at": "2026-05-15T10:00:00+08:00",
                "last_seen_at": "2026-05-18T11:00:00+08:00",
                "evidence_count": 3,
                "source_mix_json": "{\"Dcard\": 2}",
                "sentiment_mix_json": "{\"負面\": 2, \"中立\": 1}",
                "metadata_json": "{}",
            }
        )

        written = self.builder.project_recent_topics(since_date="2026-05-01")

        self.assertEqual(written, 1)
        topics = self.db.get_intel_topics(scope_key="7-ELEVEN", days=30)
        self.assertEqual(len(topics), 1)
        bindings = self.db.get_intel_topic_events(topics[0]["id"])
        self.assertEqual({row["event_case_id"] for row in bindings}, {"evt_001", "evt_002"})


if __name__ == "__main__":
    unittest.main()
