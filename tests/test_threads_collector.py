import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.collectors.threads_collector import ThreadsCollector
from src.utils.db_manager import SentimentDB


SAMPLE_THREADS_SEARCH_HTML = """
<html><body>
<script type="application/json">
{
  "data": [
    {
      "username":"7eleventw",
      "full_name":"7-ELEVEn Taiwan",
      "code":"DKdhrm6v3NX",
      "taken_at":1749024000,
      "text_post_app_info":{"text_fragments":{"fragments":[{"fragment_type":"plaintext","plaintext":"7-ELEVEN 新品開箱，大家最想吃哪個？"}]}},
      "direct_reply_count":55,
      "repost_count":17,
      "quote_count":3,
      "reshare_count":38
    },
    {
      "username":"foody_amigo",
      "full_name":"美食帳號",
      "code":"DO2N7fQEoYw",
      "taken_at":1749027600,
      "text_post_app_info":{"text_fragments":{"fragments":[{"fragment_type":"plaintext","plaintext":"7-11 新品這次表現不錯，牛奶糖霜淇淋值得回購。"}]}},
      "direct_reply_count":6,
      "repost_count":2,
      "quote_count":1,
      "reshare_count":4
    },
    {
      "username":"plain_user",
      "full_name":"一般用戶",
      "code":"ABC123xyz",
      "taken_at":1749031200,
      "text_post_app_info":{"text_fragments":{"fragments":[{"fragment_type":"plaintext","plaintext":"小七店員態度真的很差，排隊排到火大。"}]}},
      "direct_reply_count":12,
      "repost_count":0,
      "quote_count":0,
      "reshare_count":1
    }
  ]
}
</script>
</body></html>
"""


class ThreadsCollectorConfigTest(unittest.TestCase):
    def test_disables_collection_when_key_absent(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": ""}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertEqual(collector._bypass_mode, "disabled")

    def test_parse_search_results_extracts_official_and_user_feedback_posts(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        rows = collector._parse_search_results(SAMPLE_THREADS_SEARCH_HTML, limit=5)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["username"], "7eleventw")
        self.assertTrue(rows[0]["is_official"])
        self.assertEqual(rows[0]["link"], "https://www.threads.com/@7eleventw/post/DKdhrm6v3NX")
        self.assertEqual(rows[0]["reply_count"], 55)
        self.assertIn("新品開箱", rows[0]["content"])
        self.assertFalse(rows[1]["is_official"])
        self.assertIn("店員態度", rows[2]["content"])

    def test_fetch_latest_posts_prioritizes_official_threads_before_public_feedback(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        collector._fetch_search_results = Mock(return_value=collector._parse_search_results(SAMPLE_THREADS_SEARCH_HTML, limit=5))
        collector._fetch_post_detail = Mock(side_effect=lambda row: row)

        rows = collector.fetch_latest_posts(limit=3)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["source"], "Threads")
        self.assertEqual(rows[0]["channel"], "threads")
        self.assertEqual(rows[0]["author"], "7eleventw")
        self.assertIn("7-11 新品", rows[1]["title"])
        self.assertIn("店員態度", rows[2]["title"])

    def test_fetch_latest_posts_uses_cached_search_results_within_ttl(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            db.set_collector_cache(
                "threads_search:v1:7-ELEVEN",
                {
                    "results": [
                        {
                            "username": "7eleventw",
                            "full_name": "7-ELEVEn Taiwan",
                            "link": "https://www.threads.com/@7eleventw/post/DKdhrm6v3NX",
                            "content": "7-ELEVEN 新品開箱，大家最想吃哪個？",
                            "title": "7-ELEVEN 新品開箱，大家最想吃哪個？",
                            "published": "2026-05-22T12:00:00+08:00",
                            "reply_count": 55,
                            "repost_count": 17,
                            "quote_count": 3,
                            "reshare_count": 38,
                            "is_official": True,
                        }
                    ]
                },
                ttl_minutes=30,
            )

            with patch.dict(
                os.environ,
                {"SCRAPERAPI_KEY": "scraper-key", "THREADS_SEARCH_CACHE_MINUTES": "30"},
                clear=False,
            ):
                collector = ThreadsCollector("7-ELEVEN", db=db)

            with patch.object(collector, "_fetch_search_results", side_effect=AssertionError("should not fetch search")):
                rows = collector.fetch_latest_posts(limit=3)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["link"], "https://www.threads.com/@7eleventw/post/DKdhrm6v3NX")
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
