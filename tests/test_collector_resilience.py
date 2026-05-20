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

    def test_google_news_prefers_direct_rss_before_scraperapi(self):
        collector = GoogleNewsCollector("7-ELEVEN")

        direct_feed = Mock()
        direct_feed.entries = [
            feedparser.FeedParserDict(
                title="7-ELEVEN 直連新聞",
                link="https://example.com/direct",
                published="2026-05-20",
                summary="直連摘要",
                source=feedparser.FeedParserDict(title="Google News"),
            )
        ]

        with patch("src.collectors.google_news_collector.os.getenv", return_value="fake-key"), \
             patch("src.collectors.google_news_collector.feedparser.parse", return_value=direct_feed) as mock_parse, \
             patch("src.collectors.google_news_collector.requests.get") as mock_get:
            rows = collector._fetch_rss_feed(limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "7-ELEVEN 直連新聞")
        mock_parse.assert_called_once()
        mock_get.assert_not_called()

    def test_google_news_falls_back_to_scraperapi_when_direct_rss_fails(self):
        collector = GoogleNewsCollector("7-ELEVEN")

        fallback_response = Mock()
        fallback_response.raise_for_status.return_value = None
        fallback_response.text = "<rss />"

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
             patch("src.collectors.google_news_collector.feedparser.parse", side_effect=[Exception("direct blocked"), fallback_feed]) as mock_parse, \
             patch("src.collectors.google_news_collector.requests.get", return_value=fallback_response) as mock_get:
            rows = collector._fetch_rss_feed(limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "7-ELEVEN 食安新聞")
        self.assertEqual(mock_parse.call_count, 2)
        mock_get.assert_called_once()

    def test_dcard_scraperapi_mode_uses_bare_requests_path(self):
        with patch("src.collectors.dcard_collector.os.getenv", side_effect=lambda key, default="": "fake-key" if key == "SCRAPERAPI_KEY" else ""), \
             patch.object(DcardCollector, "_init_session") as mock_init_session:
            mock_session = Mock()
            mock_init_session.return_value = mock_session
            collector = DcardCollector("7-ELEVEN")

        resp = Mock()
        resp.status_code = 200
        collector._scraperapi_get = Mock(return_value=resp)
        collector.session.get = Mock(side_effect=AssertionError("session.get should not be used"))

        result = collector._get("https://www.dcard.tw/search/posts", params={"query": "7-ELEVEN"})

        self.assertEqual(result, resp)
        self.assertEqual(collector._bypass_mode, "scraperapi")
        collector._scraperapi_get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
