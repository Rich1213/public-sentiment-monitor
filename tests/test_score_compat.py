import os
import tempfile
import unittest

from src.utils.db_manager import SentimentDB
from src.utils.score_utils import normalize_score


class ScoreCompatTest(unittest.TestCase):
    def test_normalize_score_supports_legacy_and_new_scale(self):
        self.assertEqual(normalize_score(None), 0)
        self.assertEqual(normalize_score(0.1), 1)
        self.assertEqual(normalize_score(0.5), 3)
        self.assertEqual(normalize_score(0.7), 4)
        self.assertEqual(normalize_score(0.85), 5)
        self.assertEqual(normalize_score(3), 3)
        self.assertEqual(normalize_score(5), 5)

    def test_save_analysis_normalizes_and_backfill_updates_existing_rows(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            run_id = db.create_run("7-ELEVEN")
            thread_id = db.save_thread(
                url="https://example.com/post-1",
                source_name="PTT",
                channel="ptt",
                title="測試文章",
                keyword="7-ELEVEN",
            )

            db.save_analysis(
                thread_id,
                run_id,
                {
                    "sentiment": "負面",
                    "score": 0.7,
                    "theme": "食安",
                    "reason": "測試",
                    "voice_source": "PTT",
                    "analyzed_with": "標題",
                    "model_used": "test",
                },
            )

            rows = db.get_run_analyses(run_id)
            self.assertEqual(rows[0]["score"], 4)
            self.assertEqual(rows[0]["raw_score"], 4)

            conn = db.adapter.get_connection()
            try:
                c = conn.cursor()
                c.execute("UPDATE analyses SET score = 0.5")
                conn.commit()
            finally:
                conn.close()

            updated = db.backfill_legacy_scores()
            self.assertEqual(updated, 1)

            rows = db.get_run_analyses(run_id)
            self.assertEqual(rows[0]["score"], 3)
            self.assertEqual(rows[0]["raw_score"], 3)
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
