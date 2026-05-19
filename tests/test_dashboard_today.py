import os
import tempfile
import unittest
from datetime import datetime

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
        return thread_id

    def _execute(self, sql: str, params=()):
        conn = self.db._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _query_value(self, sql: str, params=()):
        conn = self.db._adapter.get_connection()
        try:
            c = conn.cursor()
            c.execute(sql, params)
            row = c.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

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

    def test_save_thread_preserves_first_seen_at_on_upsert(self):
        thread_id = self.db.save_thread(
            url="https://example.com/thread-stable",
            source_name="PTT",
            channel="ptt",
            title="第一次看到",
            keyword="7-ELEVEN",
        )
        self._execute(
            "UPDATE threads SET first_seen_at = ? WHERE id = ?",
            ("2026-05-01T08:00:00", thread_id),
        )

        self.db.save_thread(
            url="https://example.com/thread-stable",
            source_name="PTT",
            channel="ptt",
            title="第二次掃到",
            keyword="7-ELEVEN",
        )

        self.assertEqual(
            self._query_value("SELECT first_seen_at FROM threads WHERE id = ?", (thread_id,)),
            "2026-05-01T08:00:00",
        )

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

    def test_dashboard_day_summary_excludes_old_thread_without_today_signal(self):
        run_id = self.db.create_run("7-ELEVEN")
        thread_id = self._seed_analysis(run_id, "7-ELEVEN", "https://example.com/old-post", "三月舊文")
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        self._execute(
            "UPDATE threads SET published_at = ?, first_seen_at = ? WHERE id = ?",
            ("2026-03-01T09:00:00", "2026-03-01T09:00:00", thread_id),
        )

        summary = self.db.get_dashboard_day_summary(snapshot_date="2026-05-19")

        self.assertEqual(summary["total_articles"], 0)
        self.assertEqual(summary["all_alerts"], [])
        self.assertEqual(summary["brand_map"], {})

    def test_dashboard_day_summary_includes_old_thread_with_today_comment_activity(self):
        run_id = self.db.create_run("7-ELEVEN")
        thread_id = self._seed_analysis(run_id, "7-ELEVEN", "https://example.com/reheated-post", "三月舊文今天延燒")
        self.db.close_run(run_id, articles_found=1, articles_new=1)
        self._execute(
            "UPDATE threads SET published_at = ?, first_seen_at = ? WHERE id = ?",
            ("2026-03-20T08:00:00", "2026-03-20T08:00:00", thread_id),
        )
        self.db.save_thread_item(
            thread_id,
            content="今天突然爆大量留言",
            item_type="comment",
            author="tester",
            platform_item_id="comment-1",
            published_at="2026-05-19T10:32:00",
        )

        summary = self.db.get_dashboard_day_summary(snapshot_date="2026-05-19")

        self.assertEqual(summary["total_articles"], 1)
        brand = summary["brand_map"]["7-ELEVEN"]
        self.assertEqual(brand["total"], 1)
        self.assertEqual(len(summary["all_alerts"]), 1)
        self.assertEqual(summary["all_alerts"][0]["recent_activity_at"], "2026-05-19T10:32:00")
        self.assertEqual(summary["all_alerts"][0]["ongoing_days"], 60)

    def test_get_dashboard_trend_uses_snapshots_for_past_days_and_live_today(self):
        for date, avg_score in [
            ("2026-05-13", 2.1),
            ("2026-05-14", 2.4),
            ("2026-05-15", 2.8),
            ("2026-05-16", 3.2),
            ("2026-05-17", 3.7),
            ("2026-05-18", 4.1),
        ]:
            self._execute(
                """
                INSERT INTO daily_snapshots (
                    snapshot_date, keyword, snapshot_at, risk_score, article_count,
                    pos_count, neu_count, neg_count, high_risk_count, avg_score,
                    channel_breakdown, top_themes, dashboard_summary, payload_json
                ) VALUES (?, ?, ?, 50, 1, 0, 0, 1, 1, ?, '{}', '[]', 'snapshot', '{}')
                """,
                (date, "7-ELEVEN", f"{date}T00:05:00", avg_score),
            )

        run_id = self.db.create_run("7-ELEVEN")
        thread_id = self._seed_analysis(run_id, "7-ELEVEN", "https://example.com/today-live", "今天新文", score=4)
        self.db.close_run(run_id, articles_found=1, articles_new=1)
        self._execute(
            "UPDATE threads SET first_seen_at = ?, published_at = ? WHERE id = ?",
            ("2026-05-19T09:00:00", "2026-05-19T09:00:00", thread_id),
        )

        trend = self.db.get_dashboard_trend(days=7, keywords=["7-ELEVEN"], today="2026-05-19")

        self.assertEqual(set(trend["7-ELEVEN"].keys()), {
            "2026-05-13",
            "2026-05-14",
            "2026-05-15",
            "2026-05-16",
            "2026-05-17",
            "2026-05-18",
            "2026-05-19",
        })
        self.assertEqual(trend["7-ELEVEN"]["2026-05-18"], 4.1)
        self.assertEqual(trend["7-ELEVEN"]["2026-05-19"], 4.0)

    def test_json_dumps_supports_datetime_payloads(self):
        payload = {"when": datetime(2026, 5, 18, 23, 59, 0)}
        text = self.db._json_dumps(payload)
        self.assertIn("2026-05-18T23:59:00", text)


if __name__ == "__main__":
    unittest.main()
