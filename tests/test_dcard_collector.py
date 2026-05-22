import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.collectors.dcard_collector import DcardCollector
from src.utils.db_manager import SentimentDB


class DcardCollectorConfigTest(unittest.TestCase):
    def test_uses_scraperapi_when_key_present(self):
        with patch.dict(
            os.environ,
            {"SCRAPERAPI_KEY": "scraper-key"},
            clear=False,
        ):
            collector = DcardCollector("7-ELEVEN")

        self.assertEqual(collector._bypass_mode, "scraperapi")

    def test_disables_collection_when_key_absent(self):
        with patch.dict(
            os.environ,
            {"SCRAPERAPI_KEY": ""},
            clear=False,
        ):
            collector = DcardCollector("7-ELEVEN")

        self.assertEqual(collector._bypass_mode, "disabled")

    def test_get_uses_scraperapi_only(self):
        with patch.dict(
            os.environ,
            {"SCRAPERAPI_KEY": "scraper-key"},
            clear=False,
        ):
            collector = DcardCollector("7-ELEVEN")

        resp = Mock()
        resp.status_code = 200
        collector._scraperapi_get = Mock(return_value=resp)

        result = collector._get("https://www.dcard.tw/search/posts", params={"query": "7-ELEVEN"})

        self.assertEqual(result, resp)
        collector._scraperapi_get.assert_called_once()

    def test_fetch_latest_posts_returns_empty_when_key_absent(self):
        with patch.dict(
            os.environ,
            {"SCRAPERAPI_KEY": ""},
            clear=False,
        ):
            collector = DcardCollector("7-ELEVEN")

        with patch.object(collector, "_fetch_pinned_posts") as mock_pinned, \
             patch.object(collector, "_fetch_search_term") as mock_search:
            rows = collector.fetch_latest_posts(limit=5)

        self.assertEqual(rows, [])
        mock_pinned.assert_not_called()
        mock_search.assert_not_called()

    def test_fetch_latest_posts_uses_cached_search_results_within_ttl(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            cached_posts = [
                {
                    "id": "123",
                    "title": "7-ELEVEN 測試文章",
                    "excerpt": "這是一篇夠長的測試摘要",
                    "forum": "超商",
                    "post_url": "https://www.dcard.tw/f/cvs/p/123",
                    "date_str": "2026-05-22T09:00:00",
                    "like_count": 3,
                    "comment_count": 2,
                }
            ]
            db.set_collector_cache("dcard_search:v1:7-ELEVEN", {"raw_posts": cached_posts}, ttl_minutes=30)

            with patch.dict(
                os.environ,
                {"SCRAPERAPI_KEY": "scraper-key", "DCARD_SEARCH_CACHE_MINUTES": "30"},
                clear=False,
            ):
                collector = DcardCollector("7-ELEVEN", db=db)

            collector._fetch_post_content = Mock(return_value={
                "content": "這是一篇超過五十字的內文測試，用來驗證快取命中後不需要重新打搜尋頁，而且仍然可以組出文章內容，同時避免被純圖貼文過濾邏輯誤判。",
                "like_count": 3,
                "comment_count": 2,
                "forum": "超商",
                "title": "7-ELEVEN 測試文章",
                "date_str": "2026-05-22T09:00:00",
            })

            with patch.object(collector, "_fetch_pinned_posts", side_effect=AssertionError("should not fetch pinned")), \
                 patch.object(collector, "_fetch_search_term", side_effect=AssertionError("should not fetch search")):
                rows = collector.fetch_latest_posts(limit=5)

            self.assertEqual(len(rows), 1)
            collector._fetch_post_content.assert_called_once()
        finally:
            os.remove(path)

    def test_fetch_latest_posts_refreshes_after_cache_expiry(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            db.set_collector_cache("dcard_search:v1:7-ELEVEN", {"raw_posts": []}, ttl_minutes=-1)

            with patch.dict(
                os.environ,
                {"SCRAPERAPI_KEY": "scraper-key", "DCARD_SEARCH_CACHE_MINUTES": "30"},
                clear=False,
            ):
                collector = DcardCollector("7-ELEVEN", db=db)

            collector._fetch_post_content = Mock(return_value={})
            with patch.object(collector, "_fetch_pinned_posts", return_value=[]), \
                 patch.object(collector, "_fetch_search_term", return_value=[]) as mock_search:
                rows = collector.fetch_latest_posts(limit=5)

            self.assertEqual(rows, [])
            mock_search.assert_called()
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
