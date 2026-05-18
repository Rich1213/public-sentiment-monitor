import os
import tempfile
import unittest

from src.utils.db_manager import SentimentDB
from src.analyzers.dashboard_narrator import DashboardNarrator


class DashboardNarratorTest(unittest.TestCase):
    def test_fallback_summary_focuses_on_single_event_and_top_channels(self):
        narrator = DashboardNarrator(router=None)
        articles = [
            {"title": "超商食品驚見活蟲", "channel": "youtube", "published": "2026-05-18", "source": "YouTube"},
            {"title": "超商義大利麵有蟲", "channel": "ptt", "published": "2026-05-18", "source": "PTT"},
            {"title": "中獎發票詐騙提醒", "channel": "ptt", "published": "2026-05-18", "source": "PTT"},
        ]
        analyses = [
            {"sentiment": "負面", "score": 5, "theme": "食品出現活蟲", "reason": "食安與品管疑慮", "voice_source": "YouTube"},
            {"sentiment": "負面", "score": 4, "theme": "義大利麵有蟲", "reason": "食安與品管疑慮", "voice_source": "PTT"},
            {"sentiment": "負面", "score": 3, "theme": "發票中獎詐騙", "reason": "詐騙風險", "voice_source": "PTT"},
        ]

        summary = narrator._fallback_summary("7-ELEVEN", articles, analyses)
        self.assertIn("今日最需要關注的事件是", summary)
        self.assertIn("主要出現在 YouTube、PTT", summary)
        self.assertIn("活蟲", summary)
        self.assertNotIn("發票中獎詐騙、", summary)

    def test_db_roundtrip_includes_dashboard_summary(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            run_id = db.create_run("7-ELEVEN")
            db.save_pr_report(
                run_id,
                "7-ELEVEN",
                "A",
                "長版報告",
                dashboard_summary="短版摘要",
            )
            row = db.get_run_pr_report(run_id)
            self.assertEqual(row["dashboard_summary"], "短版摘要")
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
