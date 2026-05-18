import os
import tempfile
import unittest

from src.utils.db_manager import SentimentDB


class DashboardTodayTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.db = SentimentDB(db_path=path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _seed_analysis(self, run_id: int, keyword: str, url: str, title: str, score: int = 4, sentiment: str = "負面"):
        thread_id = self.db.save_thread(
            url=url,
            source_name="YouTube",
            channel="youtube",
            title=title,
            keyword=keyword,
        )
        self.db.save_analysis(
            thread_id,
            run_id,
            {
                "sentiment": sentiment,
                "score": score,
                "theme": "食安疑慮",
                "reason": "測試",
                "voice_source": "YouTube",
                "analyzed_with": "標題",
                "model_used": "test",
            },
        )
        self.db.save_pr_report(run_id, keyword, "A", "PR 報告", dashboard_summary="今日摘要")

    def test_dashboard_day_summary_deduplicates_same_thread_within_day(self):
        run_1 = self.db.create_run("7-ELEVEN")
        run_2 = self.db.create_run("7-ELEVEN")
        self._seed_analysis(run_1, "7-ELEVEN", "https://example.com/post-1", "同一篇文章")
        self._seed_analysis(run_2, "7-ELEVEN", "https://example.com/post-1", "同一篇文章")
        self.db.close_run(run_1, articles_found=1, articles_new=1)
        self.db.close_run(run_2, articles_found=1, articles_new=0)

        summary = self.db.get_dashboard_day_summary()

        self.assertEqual(summary["total_articles"], 1)
        self.assertEqual(summary["brand_map"]["7-ELEVEN"]["total"], 1)
        self.assertEqual(len(summary["all_alerts"]), 1)

    def test_monitor_batch_roundtrip(self):
        batch_id = self.db.create_monitor_batch(["7-ELEVEN", "全家"])
        active = self.db.get_active_monitor_batch()
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], batch_id)
        self.assertEqual(active["keywords"], ["7-ELEVEN", "全家"])

        self.db.close_monitor_batch(batch_id)
        self.assertIsNone(self.db.get_active_monitor_batch())

    def test_save_daily_snapshots_persists_today_rows(self):
        run_id = self.db.create_run("7-ELEVEN")
        self._seed_analysis(run_id, "7-ELEVEN", "https://example.com/post-2", "今天文章")
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        written = self.db.save_daily_snapshots()
        rows = self.db.get_daily_snapshots(limit=5, keyword="7-ELEVEN")

        self.assertEqual(written, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["article_count"], 1)
        self.assertGreaterEqual(rows[0]["risk_score"], 1)


if __name__ == "__main__":
    unittest.main()
