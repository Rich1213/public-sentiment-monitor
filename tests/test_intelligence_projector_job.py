import os
import tempfile
import unittest

from src.intelligence.event_case_builder import EventCaseBuilder
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


class _FakeCursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


class _FakePostgresAdapter:
    def __init__(self, conn):
        self._conn = conn

    @property
    def is_postgres(self):
        return True

    @property
    def placeholder(self):
        return "%s"

    def get_connection(self):
        return self._conn

    def fetchall_dict(self, _cursor):
        return []


class IntelligenceProjectorPostgresCompatTest(unittest.TestCase):
    def test_get_intelligence_signal_rows_casts_postgres_analyzed_at_to_text(self):
        cursor = _FakeCursor()
        conn = _FakeConn(cursor)
        db = SentimentDB.__new__(SentimentDB)
        db._adapter = _FakePostgresAdapter(conn)

        rows = db.get_intelligence_signal_rows(since_date="2026-02-25")

        self.assertEqual(rows, [])
        self.assertIn("a.analyzed_at::text", cursor.sql)
        self.assertEqual(cursor.params, ("2026-02-25",))
        self.assertTrue(conn.closed)

    def test_build_cases_accepts_rfc2822_published_at(self):
        builder = EventCaseBuilder(db=None)

        cases = builder.build_cases(
            [
                {
                    "analysis_id": 1,
                    "thread_id": 101,
                    "keyword": "7-ELEVEN",
                    "channel": "news",
                    "sentiment": "負面",
                    "score": 3,
                    "theme": "便當價格",
                    "published_at": "Mon, 27 Apr 2026 07:00:00 GMT",
                },
                {
                    "analysis_id": 2,
                    "thread_id": 102,
                    "keyword": "7-ELEVEN",
                    "channel": "threads",
                    "sentiment": "負面",
                    "score": 2,
                    "theme": "便當價格",
                    "published_at": "2026-04-28 00:00",
                },
            ]
        )

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["evidence_count"], 2)
