import unittest
from unittest.mock import Mock, patch
import feedparser

from src.collectors.google_news_collector import GoogleNewsCollector
from src.collectors.ptt_collector import PTTCollector
import src.collectors.dcard_collector as dcard_module
from src.collectors.dcard_collector import DcardCollector


class CollectorResilienceTest(unittest.TestCase):
    def test_ptt_old_post_is_filtered(self):
        collector = PTTCollector("7-ELEVEN")
        self.assertFalse(collector._is_recent("2025-07-27 00:00"))
        self.assertTrue(collector._is_recent("2026-05-01 00:00"))

    def test_google_news_falls_back_to_direct_rss_when_scraperapi_fails(self):
        collector = GoogleNewsCollector("7-ELEVEN")

        failing_response = Mock()
        failing_response.raise_for_status.side_effect = Exception("403 forbidden")

        fallback_feed = Mock()
        fallback_feed.entries = [
            feedparser.FeedParserDict(
                title="7-ELEVEN 食安新聞",
                link="https://example.com/news1",
                published="2026-05-18",
                summary="摘要內容",
                source=feedparser.FeedParserDict(title="Google News"),
            )
        ]

        with patch("src.collectors.google_news_collector.os.getenv", return_value="fake-key"), \
             patch("src.collectors.google_news_collector.feedparser.parse", return_value=fallback_feed) as mock_parse, \
             patch("requests.get", return_value=failing_response):
            rows = collector._fetch_rss_feed(limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "7-ELEVEN 食安新聞")
        self.assertTrue(mock_parse.called)

    def test_dcard_scraperapi_mode_uses_bare_requests_path(self):
        original_mode = dcard_module._BYPASS_MODE
        try:
            dcard_module._BYPASS_MODE = "scraperapi"
            collector = DcardCollector("7-ELEVEN")
            resp = Mock()
            resp.status_code = 200
            collector._scraperapi_get = Mock(return_value=resp)
            collector.session.get = Mock(side_effect=AssertionError("session.get should not be used"))

            result = collector._get("https://www.dcard.tw/search/posts", params={"query": "7-ELEVEN"})

            self.assertEqual(result, resp)
            collector._scraperapi_get.assert_called_once()
        finally:
            dcard_module._BYPASS_MODE = original_mode


if __name__ == "__main__":
    unittest.main()
