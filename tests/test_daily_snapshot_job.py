import os
import tempfile
import unittest
from datetime import datetime

from src.jobs.daily_snapshot_job import resolve_snapshot_date, run_daily_snapshot_capture
from src.utils.db_manager import SentimentDB


class DailySnapshotJobTest(unittest.TestCase):
    def test_resolve_snapshot_date_uses_taipei_yesterday_for_utc_cron_runtime(self):
        snapshot_date = resolve_snapshot_date(
            now=datetime.fromisoformat("2026-05-19T16:05:00+00:00"),
            timezone_name="Asia/Taipei",
            offset_days=-1,
        )

        self.assertEqual(snapshot_date, "2026-05-19")

    def test_run_daily_snapshot_capture_writes_yesterday_snapshot(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            run_id = db.create_run("7-ELEVEN")
            thread_id = db.save_thread(
                url="https://example.com/yesterday-hot",
                source_name="PTT",
                channel="ptt",
                title="昨天延燒文章",
                keyword="7-ELEVEN",
            )
            db.save_analysis(
                thread_id,
                run_id,
                {
                    "sentiment": "負面",
                    "score": 4,
                    "theme": "危機測試",
                    "reason": "測試",
                    "voice_source": "PTT",
                    "analyzed_with": "標題",
                    "model_used": "test",
                },
            )
            db.close_run(run_id, articles_found=1, articles_new=1)

            conn = db.adapter.get_connection()
            try:
                c = conn.cursor()
                c.execute(
                    "UPDATE threads SET first_seen_at = ?, published_at = ? WHERE id = ?",
                    ("2026-05-20T08:00:00", "2026-05-20T08:00:00", thread_id),
                )
                conn.commit()
            finally:
                conn.close()

            result = run_daily_snapshot_capture(
                db_path=path,
                timezone_name="Asia/Taipei",
                now=datetime.fromisoformat("2026-05-20T16:05:00+00:00"),
            )

            self.assertEqual(result["snapshot_date"], "2026-05-20")
            self.assertEqual(result["written"], 1)

            rows = db.get_daily_snapshots(limit=5, keyword="7-ELEVEN")
            self.assertEqual(rows[0]["snapshot_date"], "2026-05-20")
            self.assertEqual(rows[0]["article_count"], 1)
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
