import os
import tempfile
import unittest
import hashlib
import urllib.parse
from datetime import datetime, timedelta
from unittest.mock import patch

from src.collectors.google_news_collector import GoogleNewsCollector
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

    def test_save_article_keeps_google_news_thread_and_analysis_on_same_record(self):
        run_id = self.db.create_run("超商食安")
        title = "超商繳費不蓋章了"
        title_key = f"gnews_title:超商食安:{title}"
        article_link = "https://news.google.com/articles/example-redirect"
        saved_thread_id = self.db.save_thread(
            url=article_link,
            source_name="Google News",
            channel="google_news",
            title=title,
            board="brand_signal",
            thread_key=title_key,
            keyword="超商食安",
            published_at="2026-05-19T06:30:21",
        )

        self.db.save_article(
            {
                "title": title,
                "link": article_link,
                "source": "Google News",
                "published": "2026-05-19T06:30:21",
                "content": "超商繳費不蓋章引發討論",
                "channel": "google_news",
                "keyword": "超商食安",
                "narrative_type": "brand_signal",
                "storage_key": title_key,
            },
            {
                "sentiment": "負面",
                "score": 3,
                "theme": "服務流程變動",
                "reason": "測試",
                "voice_source": "Google News",
                "analyzed_with": "標題",
                "model_used": "test",
            },
            run_id=run_id,
        )
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        analyses = self.db.get_run_analyses(run_id)
        self.assertEqual(len(analyses), 1)
        self.assertEqual(analyses[0]["title"], title)
        self.assertEqual(
            self._query_value("SELECT COUNT(*) FROM threads WHERE id = ?", (saved_thread_id,)),
            1,
        )
        self.assertEqual(
            self._query_value("SELECT url FROM threads WHERE id = ?", (saved_thread_id,)),
            article_link,
        )

    def test_google_news_storage_key_matches_worker_analysis_thread_id(self):
        run_id = self.db.create_run("超商食安")
        title = "超商繳費不蓋章了"
        title_key = f"gnews_title:超商食安:{title}"
        saved_thread_id = self.db.save_thread(
            url=title_key,
            source_name="Google News",
            channel="google_news",
            title=title,
            board="brand_signal",
            keyword="超商食安",
            published_at="2026-05-19T06:30:21",
        )

        worker_thread_id = hashlib.md5(title_key.encode()).hexdigest()
        self.assertEqual(saved_thread_id, worker_thread_id)

        self.db.save_analysis(
            worker_thread_id,
            run_id,
            {
                "sentiment": "負面",
                "score": 3,
                "theme": "服務流程變動",
                "reason": "測試",
                "voice_source": "Google News",
                "analyzed_with": "標題",
                "model_used": "test",
            },
            analyzed_content="超商繳費不蓋章引發討論",
        )
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        analyses = self.db.get_run_analyses(run_id)
        self.assertEqual(len(analyses), 1)
        self.assertEqual(analyses[0]["channel"], "google_news")

    def test_dashboard_summary_uses_real_google_news_link_for_new_records(self):
        run_id = self.db.create_run("超商食安")
        title = "超商雞胸肉又出事"
        title_key = f"gnews_title:超商食安:{title}"
        article_link = "https://news.google.com/articles/example-google-news-link"
        thread_id = self.db.save_thread(
            url=article_link,
            source_name="Google News",
            channel="google_news",
            title=title,
            board="brand_signal",
            thread_key=title_key,
            keyword="超商食安",
            published_at="2026-05-19T06:30:21",
        )
        self.db.save_analysis(
            thread_id,
            run_id,
            {
                "sentiment": "負面",
                "score": 4,
                "theme": "食安疑慮",
                "reason": "測試",
                "voice_source": "Google News",
                "analyzed_with": "標題",
                "model_used": "test",
            },
            analyzed_content="超商雞胸肉又出事",
        )
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        summary = self.db.get_dashboard_day_summary()

        self.assertEqual(summary["all_alerts"][0]["url"], article_link)

    def test_dashboard_summary_falls_back_for_legacy_google_news_storage_key_urls(self):
        run_id = self.db.create_run("超商食安")
        title = "超商雞胸肉又出事"
        title_key = f"gnews_title:超商食安:{title}"
        thread_id = self.db.save_thread(
            url=title_key,
            source_name="Google News",
            channel="google_news",
            title=title,
            board="brand_signal",
            keyword="超商食安",
            published_at="2026-05-19T06:30:21",
        )
        self.db.save_analysis(
            thread_id,
            run_id,
            {
                "sentiment": "負面",
                "score": 4,
                "theme": "食安疑慮",
                "reason": "測試",
                "voice_source": "Google News",
                "analyzed_with": "標題",
                "model_used": "test",
            },
            analyzed_content="超商雞胸肉又出事",
        )
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        summary = self.db.get_dashboard_day_summary()
        expected_url = (
            "https://news.google.com/search"
            f"?q={urllib.parse.quote(title)}&hl=zh-TW&gl=TW&ceid=TW%3Azh-Hant"
        )

        self.assertEqual(summary["all_alerts"][0]["url"], expected_url)

    def test_google_news_reprocesses_existing_thread_without_analysis(self):
        title = "全家便利商店攜手特爾電力"
        title_key = f"gnews_title:全家:{title}"
        self.db.save_thread(
            url=title_key,
            source_name="Google News",
            channel="google_news",
            title=title,
            board="企業公告",
            keyword="全家",
            published_at="2026-05-19T07:09:18",
        )

        collector = GoogleNewsCollector("全家", db=self.db)
        collector._fetch_rss_feed = lambda limit=30: [
            {
                "title": title,
                "link": "https://news.google.com/articles/familymart-energy",
                "published": "2026-05-19T07:09:18",
                "summary": "全家便利商店與特爾電力合作推動充電服務。",
                "source": "Google News",
            }
        ]

        articles = collector.fetch_latest_posts(limit=5, fresh_mode=False)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["storage_key"], title_key)

    def test_google_news_skips_existing_thread_when_analysis_already_exists(self):
        run_id = self.db.create_run("全家")
        title = "全家便利商店攜手特爾電力"
        title_key = f"gnews_title:全家:{title}"
        thread_id = self.db.save_thread(
            url=title_key,
            source_name="Google News",
            channel="google_news",
            title=title,
            board="企業公告",
            keyword="全家",
            published_at="2026-05-19T07:09:18",
        )
        self.db.save_analysis(
            thread_id,
            run_id,
            {
                "sentiment": "中立",
                "score": 1,
                "theme": "合作消息",
                "reason": "測試",
                "voice_source": "Google News",
                "analyzed_with": "標題",
                "model_used": "test",
            },
            analyzed_content="全家便利商店與特爾電力合作推動充電服務。",
        )

        collector = GoogleNewsCollector("全家", db=self.db)
        collector._fetch_rss_feed = lambda limit=30: [
            {
                "title": title,
                "link": "https://news.google.com/articles/familymart-energy",
                "published": "2026-05-19T07:09:18",
                "summary": "全家便利商店與特爾電力合作推動充電服務。",
                "source": "Google News",
            }
        ]

        articles = collector.fetch_latest_posts(limit=5, fresh_mode=False)

        self.assertEqual(articles, [])

    def test_monitor_batch_roundtrip(self):
        batch_id = self.db.create_monitor_batch(["7-ELEVEN", "全家"])
        active = self.db.get_active_monitor_batch()
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], batch_id)
        self.assertEqual(active["keywords"], ["7-ELEVEN", "全家"])

        self.db.close_monitor_batch(batch_id)
        self.assertIsNone(self.db.get_active_monitor_batch())

    def test_dashboard_day_summary_freezes_at_last_completed_state_while_batch_running(self):
        now = datetime.now()
        stable_end = (now - timedelta(minutes=10)).replace(microsecond=0)
        batch_start = (now - timedelta(minutes=5)).replace(microsecond=0)
        in_progress_end = (now - timedelta(minutes=1)).replace(microsecond=0)

        old_run = self.db.create_run("7-ELEVEN")
        old_thread_id = self._seed_analysis(old_run, "7-ELEVEN", "https://example.com/old-stable", "上一版完整結果", score=3)
        self.db.close_run(old_run, articles_found=1, articles_new=1)
        self._execute(
            "UPDATE threads SET published_at = ?, first_seen_at = ? WHERE id = ?",
            ("2026-05-19T08:00:00", "2026-05-19T08:05:00", old_thread_id),
        )
        self._execute(
            "UPDATE monitoring_runs SET ended_at = ? WHERE id = ?",
            (stable_end.isoformat(), old_run),
        )

        batch_id = self.db.create_monitor_batch(["7-ELEVEN"])
        self._execute(
            "UPDATE monitor_batches SET started_at = ? WHERE id = ?",
            (batch_start.isoformat(), batch_id),
        )

        new_run = self.db.create_run("7-ELEVEN")
        new_thread_id = self._seed_analysis(new_run, "7-ELEVEN", "https://example.com/in-progress", "更新中的半套結果", score=4)
        self.db.close_run(new_run, articles_found=1, articles_new=1)
        self._execute(
            "UPDATE threads SET published_at = ?, first_seen_at = ? WHERE id = ?",
            ("2026-05-19T09:10:00", "2026-05-19T09:10:00", new_thread_id),
        )
        self._execute(
            "UPDATE monitoring_runs SET ended_at = ? WHERE id = ?",
            (in_progress_end.isoformat(), new_run),
        )

        summary = self.db.get_dashboard_day_summary(snapshot_date="2026-05-19")

        self.assertIsNotNone(summary["active_batch"])
        self.assertEqual(summary["updated_at"], stable_end.isoformat())
        self.assertEqual(summary["total_articles"], 1)
        self.assertIn("7-ELEVEN", summary["brand_map"])
        self.assertEqual(summary["brand_map"]["7-ELEVEN"]["analyses"][0]["title"], "上一版完整結果")

    def test_dashboard_day_summary_returns_timezone_aware_updated_at_for_new_runs(self):
        run_id = self.db.create_run("7-ELEVEN")
        self._seed_analysis(run_id, "7-ELEVEN", "https://example.com/tz-run", "時區測試文章")
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        summary = self.db.get_dashboard_day_summary()

        self.assertIsNotNone(summary["updated_at"])
        self.assertRegex(summary["updated_at"], r"[+-]\d{2}:\d{2}$")
        self.assertTrue(summary["updated_at"].endswith("+08:00"))

    def test_dashboard_day_summary_converts_legacy_postgres_naive_utc_to_taipei_time(self):
        run_id = self.db.create_run("7-ELEVEN")
        self._seed_analysis(run_id, "7-ELEVEN", "https://example.com/legacy-utc", "舊版 UTC 資料")
        self._execute(
            "UPDATE monitoring_runs SET ended_at = ? WHERE id = ?",
            ("2026-05-21T05:48:48", run_id),
        )

        with patch.object(type(self.db._adapter), "is_postgres", new=property(lambda _self: True)):
            summary = self.db.get_dashboard_day_summary(snapshot_date="2026-05-21")

        self.assertEqual(summary["updated_at"], "2026-05-21T13:48:48+08:00")

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

        self.assertEqual(summary["total_articles"], 1)
        self.assertEqual(summary["channel_counts"]["youtube"], 1)
        self.assertIn("7-ELEVEN", summary["brand_map"])
        self.assertEqual(len(summary["all_alerts"]), 1)
        self.assertEqual(summary["all_alerts"][0]["title"], "三月舊文")
        self.assertEqual(summary["all_alerts"][0]["recent_activity_at"], "2026-03-01T09:00:00")

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

    def test_dashboard_day_summary_accepts_same_day_space_separated_timestamps(self):
        run_id = self.db.create_run("全家")
        thread_id = self._seed_analysis(run_id, "全家", "https://example.com/familymart-ptt", "同日 PTT 文章", score=3)
        self.db.close_run(run_id, articles_found=1, articles_new=1)
        self._execute(
            "UPDATE threads SET channel = ?, published_at = ?, first_seen_at = ? WHERE id = ?",
            ("ptt", "2026-05-19 00:00", "2026-05-19T08:30:00+08:00", thread_id),
        )

        summary = self.db.get_dashboard_day_summary(snapshot_date="2026-05-19")

        self.assertEqual(summary["total_articles"], 1)
        self.assertEqual(summary["channel_counts"]["ptt"], 1)
        self.assertIn("全家", summary["brand_map"])

    def test_dashboard_day_summary_excludes_old_youtube_video_without_today_comment_activity(self):
        run_id = self.db.create_run("7-ELEVEN")
        thread_id = self._seed_analysis(
            run_id,
            "7-ELEVEN",
            "https://www.youtube.com/watch?v=legacy-video",
            "兩年前的 YouTube 舊片",
        )
        self.db.close_run(run_id, articles_found=1, articles_new=1)
        self._execute(
            "UPDATE threads SET channel = ?, first_seen_at = ?, published_at = ? WHERE id = ?",
            ("youtube", "2026-05-19T08:21:00", "2024-05-19T08:00:00", thread_id),
        )
        self._execute(
            "UPDATE thread_items SET published_at = ? WHERE thread_id = ?",
            ("2026-04-28T12:00:00", thread_id),
        )

        summary = self.db.get_dashboard_day_summary(snapshot_date="2026-05-19")

        self.assertEqual(summary["total_articles"], 1)
        self.assertEqual(summary["channel_counts"]["youtube"], 1)
        self.assertEqual(len(summary["all_alerts"]), 1)
        self.assertEqual(summary["all_alerts"][0]["channel"], "youtube")
        self.assertEqual(summary["all_alerts"][0]["recent_activity_at"], "2024-05-19T08:00:00")

    def test_get_dashboard_trend_uses_negative_ratio_for_past_days_and_live_today(self):
        for date, article_count, neg_count in [
            ("2026-05-13", 10, 2),
            ("2026-05-14", 10, 3),
            ("2026-05-15", 10, 4),
            ("2026-05-16", 10, 5),
            ("2026-05-17", 10, 6),
            ("2026-05-18", 10, 7),
        ]:
            self._execute(
                """
                INSERT INTO daily_snapshots (
                    snapshot_date, keyword, snapshot_at, risk_score, article_count,
                    pos_count, neu_count, neg_count, high_risk_count, avg_score,
                    channel_breakdown, top_themes, dashboard_summary, payload_json
                ) VALUES (?, ?, ?, 50, 1, 0, 0, 1, 1, ?, '{}', '[]', 'snapshot', '{}')
                """,
                (date, "7-ELEVEN", f"{date}T00:05:00", 0),
            )
            self._execute(
                "UPDATE daily_snapshots SET article_count = ?, neg_count = ? WHERE snapshot_date = ? AND keyword = ?",
                (article_count, neg_count, date, "7-ELEVEN"),
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
        self.assertEqual(trend["7-ELEVEN"]["2026-05-18"], 70.0)
        self.assertEqual(trend["7-ELEVEN"]["2026-05-19"], 100.0)

    def test_json_dumps_supports_datetime_payloads(self):
        payload = {"when": datetime(2026, 5, 18, 23, 59, 0)}
        text = self.db._json_dumps(payload)
        self.assertIn("2026-05-18T23:59:00", text)


if __name__ == "__main__":
    unittest.main()
