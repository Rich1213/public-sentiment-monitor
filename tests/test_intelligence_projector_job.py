import os
import tempfile
import unittest

from src.jobs.intelligence_projector_job import run_intelligence_projector
from src.utils.db_manager import SentimentDB


class IntelligenceProjectorJobTest(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = path
        self.db = SentimentDB(db_path=path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_project_recent_intelligence_materializes_cases_and_topics(self):
        run_id = self.db.create_run("7-ELEVEN")
        thread_id = self.db.save_thread(
            url="https://example.com/evt-1",
            source_name="PTT",
            channel="ptt",
            title="便當價格討論",
            keyword="7-ELEVEN",
            published_at="2026-05-01T08:00:00+08:00",
        )
        self.db.save_analysis(
            thread_id,
            run_id,
            {
                "sentiment": "負面",
                "score": 3,
                "theme": "便當價格",
                "reason": "測試",
                "voice_source": "ptt",
                "analyzed_with": "title",
                "model_used": "test",
            },
            analyzed_content="便當價格變高",
        )
        self.db.close_run(run_id, articles_found=1, articles_new=1)

        written = run_intelligence_projector(db=self.db, since_date="2026-05-01")

        self.assertGreaterEqual(written["event_cases"], 1)
        self.assertGreaterEqual(written["topics"], 1)


if __name__ == "__main__":
    unittest.main()
