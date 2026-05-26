import os
import tempfile
import unittest

from src.intelligence.event_case_builder import EventCaseBuilder
from src.utils.db_manager import SentimentDB


class EventCaseBuilderTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.db = SentimentDB(db_path=path)
        self.builder = EventCaseBuilder(self.db)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _save_signal(self, thread_id: str, keyword: str, theme: str, channel: str, published_at: str, score: int = 3):
        run_id = self.db.create_run(keyword)
        saved_thread_id = self.db.save_thread(
            url=f"https://example.com/{thread_id}",
            source_name={"ptt": "PTT", "dcard": "Dcard", "threads": "Threads"}.get(channel, "Google News"),
            channel=channel,
            title=f"{keyword}-{theme}-{thread_id}",
            keyword=keyword,
            published_at=published_at,
        )
        self.db.save_analysis(
            saved_thread_id,
            run_id,
            {
                "sentiment": "負面",
                "score": score,
                "theme": theme,
                "reason": "測試",
                "voice_source": channel,
                "analyzed_with": "title",
                "model_used": "test",
            },
            analyzed_content="測試內容",
        )
        self.db.close_run(run_id, articles_found=1, articles_new=1)
        return saved_thread_id

    def test_build_cases_groups_same_brand_same_theme_within_window(self):
        signals = [
            {
                "thread_id": "t1",
                "analysis_id": 1,
                "keyword": "7-ELEVEN",
                "theme": "便當價格",
                "sentiment": "負面",
                "score": 3,
                "channel": "ptt",
                "published_at": "2026-05-01T08:00:00+08:00",
            },
            {
                "thread_id": "t2",
                "analysis_id": 2,
                "keyword": "7-ELEVEN",
                "theme": "便當價格",
                "sentiment": "負面",
                "score": 4,
                "channel": "dcard",
                "published_at": "2026-05-03T09:00:00+08:00",
            },
        ]

        cases = self.builder.build_cases(signals)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["evidence_count"], 2)
        self.assertEqual(cases[0]["canonical_theme"], "便當價格")

    def test_build_cases_splits_different_brand_or_different_theme(self):
        signals = [
            {"thread_id": "t1", "analysis_id": 1, "keyword": "7-ELEVEN", "theme": "便當價格", "sentiment": "負面", "score": 3, "channel": "ptt", "published_at": "2026-05-01T08:00:00+08:00"},
            {"thread_id": "t2", "analysis_id": 2, "keyword": "全家", "theme": "便當價格", "sentiment": "負面", "score": 3, "channel": "ptt", "published_at": "2026-05-01T08:30:00+08:00"},
            {"thread_id": "t3", "analysis_id": 3, "keyword": "7-ELEVEN", "theme": "服務態度", "sentiment": "負面", "score": 3, "channel": "ptt", "published_at": "2026-05-01T09:00:00+08:00"},
        ]

        cases = self.builder.build_cases(signals)

        self.assertEqual(len(cases), 3)

    def test_project_recent_cases_persists_cases_and_thread_bindings(self):
        thread_1 = self._save_signal("thread-1", "7-ELEVEN", "便當價格", "ptt", "2026-05-01T08:00:00+08:00")
        thread_2 = self._save_signal("thread-2", "7-ELEVEN", "便當價格", "dcard", "2026-05-03T09:00:00+08:00")

        written = self.builder.project_recent_cases(since_date="2026-05-01")

        self.assertEqual(written, 1)
        cases = self.db.get_intel_event_cases(since_date="2026-05-01")
        self.assertEqual(len(cases), 1)
        bindings = self.db.get_intel_event_case_threads(cases[0]["id"])
        self.assertEqual({row["thread_id"] for row in bindings}, {thread_1, thread_2})


if __name__ == "__main__":
    unittest.main()
