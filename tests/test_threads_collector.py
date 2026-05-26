import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from src.collectors.threads_collector import ThreadsCollector
from src.utils.db_manager import SentimentDB


_NOW_TS = int(datetime.now(timezone.utc).timestamp())

SAMPLE_THREADS_SEARCH_HTML = """
<html><body>
<script type="application/json">
{
  "data": [
    {
      "username":"7eleventw",
      "full_name":"7-ELEVEn Taiwan",
      "code":"DKdhrm6v3NX",
      "taken_at":__NOW_TS_1__,
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
      "taken_at":__NOW_TS_2__,
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
      "taken_at":__NOW_TS_3__,
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
""".replace("__NOW_TS_1__", str(_NOW_TS)).replace("__NOW_TS_2__", str(_NOW_TS + 60)).replace("__NOW_TS_3__", str(_NOW_TS + 120))


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

    def test_ordered_search_terms_prioritize_official_handles(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertEqual(
            collector._ordered_search_terms(),
            ["7eleventw", "7-ELEVEn Taiwan", "統一超商", "小七", "7-11 台灣", "7-11 店員", "7-11 新品"],
        )

    def test_threads_relevance_rejects_overseas_generic_english_posts(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertFalse(
            collector._is_threads_relevant(
                username="rabeeabasheer",
                full_name="budget deals",
                content="Buy 1 Free 1 Cornetto Ice Cream for RM3.60 at 7-Eleven in Malaysia",
            )
        )

    def test_threads_relevance_accepts_taiwan_context_posts(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertTrue(
            collector._is_threads_relevant(
                username="plain_user",
                full_name="一般用戶",
                content="小七店員態度真的很差，排隊排到火大。",
            )
        )

    def test_threads_relevance_accepts_food_and_new_flavor_focus_posts(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertTrue(
            collector._is_threads_relevant(
                username="foody_amigo",
                full_name="美食帳號",
                content="7-11 新品這次表現不錯，牛奶糖霜淇淋值得回購。",
            )
        )

    def test_threads_relevance_rejects_official_handle_post_when_content_is_pure_english(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertFalse(
            collector._is_threads_relevant(
                username="7eleventw",
                full_name="7-ELEVEn Taiwan",
                content="Cruising to 7-ELEVEn obviously. Yall need anything?",
            )
        )

    def test_threads_relevance_accepts_official_handle_post_when_content_has_chinese(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertTrue(
            collector._is_threads_relevant(
                username="7eleventw",
                full_name="7-ELEVEn Taiwan",
                content="台灣 7-ELEVEN 門市推出牛奶糖霜淇淋新口味，歡迎大家回購。",
            )
        )

    def test_threads_relevance_rejects_official_giveaway_post(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertFalse(
            collector._is_threads_relevant(
                username="7eleventw",
                full_name="7-ELEVEn Taiwan",
                content="克拉們快留言應援你的擔，就有機會免費獲得一整套MINITEEN伸縮鑰匙扣零錢包！活動時間：即日起至1月5日，指定規則：追蹤官方帳號並留言。",
            )
        )

    def test_threads_relevance_accepts_official_service_complaint_post(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertTrue(
            collector._is_threads_relevant(
                username="7eleventw",
                full_name="7-ELEVEn Taiwan",
                content="我要投訴，彰化市有店家把餐盒上的卡移除，我有問店員，他還回說是另外送的。",
            )
        )

    def test_threads_relevance_rejects_brand_nickname_chatter_without_core_signal(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertFalse(
            collector._is_threads_relevant(
                username="akin_0927",
                full_name="一般用戶",
                content="為什麼 7-11 大家都叫小七，全家不叫小全？",
            )
        )

    def test_threads_relevance_rejects_generic_multi_brand_deals_post(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertFalse(
            collector._is_threads_relevant(
                username="jingyangtw55",
                full_name="省錢帳號",
                content="三大超商的每週優惠來囉 #超商優惠 #小七優惠 #全家優惠 #萊爾富優惠 #省錢",
            )
        )

    def test_threads_relevance_rejects_generic_ecommerce_post_without_cvs_context(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertFalse(
            collector._is_threads_relevant(
                username="couplelovemoney",
                full_name="官網優惠",
                content="台灣 7-ELEVEN 限定開賣，官方網站今晚七點補貨，歡迎上網購買。",
            )
        )

    def test_threads_relevance_rejects_unilions_only_post(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        self.assertFalse(
            collector._is_threads_relevant(
                username="youngwawacat",
                full_name="統一獅球迷",
                content="統一獅自1989年成軍至今，走過低潮、締造榮耀，如今隊史兩千勝即將達成。",
            )
        )

    def test_parse_search_results_rejects_stale_threads_posts(self):
        stale_html = """
        <html><body><script type="application/json">{
          "data": [{
            "username":"plain_user",
            "full_name":"一般用戶",
            "code":"OLD123xyz",
            "taken_at":1704067200,
            "text_post_app_info":{"text_fragments":{"fragments":[{"fragment_type":"plaintext","plaintext":"小七店員態度很差，排隊排到火大。"}]}},
            "direct_reply_count":12,
            "repost_count":0,
            "quote_count":0,
            "reshare_count":1
          }]
        }</script></body></html>
        """
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        rows = collector._parse_search_results(stale_html, limit=5)

        self.assertEqual(rows, [])

    def test_fetch_latest_posts_stops_after_collecting_enough_unique_candidates(self):
        with patch.dict(
            os.environ,
            {"SCRAPERAPI_KEY": "scraper-key", "THREADS_SEARCH_MAX_WORKERS": "2"},
            clear=False,
        ):
            collector = ThreadsCollector("7-ELEVEN")

        batch_one = [
            {
                "username": "official_1",
                "full_name": "Official One",
                "link": "https://www.threads.com/@official_1/post/1",
                "content": "7-ELEVEN 新品開箱",
                "title": "7-ELEVEN 新品開箱",
                "published": "2026-05-22T12:00:00+08:00",
                "reply_count": 50,
                "repost_count": 10,
                "quote_count": 2,
                "reshare_count": 5,
                "is_official": True,
            },
            {
                "username": "user_2",
                "full_name": "User Two",
                "link": "https://www.threads.com/@user_2/post/2",
                "content": "7-11 新品值得回購",
                "title": "7-11 新品值得回購",
                "published": "2026-05-22T12:01:00+08:00",
                "reply_count": 15,
                "repost_count": 3,
                "quote_count": 1,
                "reshare_count": 2,
                "is_official": False,
            },
            {
                "username": "user_3",
                "full_name": "User Three",
                "link": "https://www.threads.com/@user_3/post/3",
                "content": "小七店員態度差",
                "title": "小七店員態度差",
                "published": "2026-05-22T12:02:00+08:00",
                "reply_count": 8,
                "repost_count": 0,
                "quote_count": 0,
                "reshare_count": 1,
                "is_official": False,
            },
            {
                "username": "user_4",
                "full_name": "User Four",
                "link": "https://www.threads.com/@user_4/post/4",
                "content": "7-11 排隊很久",
                "title": "7-11 排隊很久",
                "published": "2026-05-22T12:03:00+08:00",
                "reply_count": 4,
                "repost_count": 0,
                "quote_count": 0,
                "reshare_count": 0,
                "is_official": False,
            },
        ]
        batch_two = [
            {
                "username": "user_5",
                "full_name": "User Five",
                "link": "https://www.threads.com/@user_5/post/5",
                "content": "7-11 咖啡優惠",
                "title": "7-11 咖啡優惠",
                "published": "2026-05-22T12:04:00+08:00",
                "reply_count": 9,
                "repost_count": 1,
                "quote_count": 0,
                "reshare_count": 1,
                "is_official": False,
            },
            {
                "username": "user_6",
                "full_name": "User Six",
                "link": "https://www.threads.com/@user_6/post/6",
                "content": "小七霜淇淋很好吃",
                "title": "小七霜淇淋很好吃",
                "published": "2026-05-22T12:05:00+08:00",
                "reply_count": 6,
                "repost_count": 1,
                "quote_count": 0,
                "reshare_count": 0,
                "is_official": False,
            },
        ]

        collector._fetch_search_results = Mock(side_effect=[batch_one, batch_two, AssertionError("should stop early")])
        collector._fetch_post_detail = Mock(side_effect=lambda row: row)

        rows = collector.fetch_latest_posts(limit=3, fresh_mode=True)

        self.assertEqual(len(rows), 3)
        self.assertEqual(collector._fetch_search_results.call_count, 2)

    def test_search_max_workers_defaults_and_minimum(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")
        self.assertEqual(collector._search_max_workers, 2)

        with patch.dict(
            os.environ,
            {"SCRAPERAPI_KEY": "scraper-key", "THREADS_SEARCH_MAX_WORKERS": "0"},
            clear=False,
        ):
            collector = ThreadsCollector("7-ELEVEN")
        self.assertEqual(collector._search_max_workers, 1)

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

    def test_fetch_post_detail_keeps_search_title_when_meta_title_is_generic(self):
        with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
            collector = ThreadsCollector("7-ELEVEN")

        resp = Mock()
        resp.status_code = 200
        resp.text = (
            '<meta property="og:title" content="7-ELEVEn Taiwan (&#064;7eleventw) on Threads">'
            '<meta name="description" content="7-11 新品這次表現不錯，牛奶糖霜淇淋值得回購。">'
        )
        collector._scraperapi_get = Mock(return_value=resp)

        row = {
            "link": "https://www.threads.com/@7eleventw/post/ABC123",
            "title": "7-11 新品這次表現不錯，牛奶糖霜淇淋值得回購。",
            "content": "7-11 新品這次表現不錯，牛奶糖霜淇淋值得回購。",
        }

        detailed = collector._fetch_post_detail(dict(row))

        self.assertEqual(detailed["title"], row["title"])
        self.assertIn("牛奶糖霜淇淋", detailed["content"])

    def test_fetch_latest_posts_persists_threads_when_db_present(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            with patch.dict(os.environ, {"SCRAPERAPI_KEY": "scraper-key"}, clear=False):
                collector = ThreadsCollector("7-ELEVEN", db=db)

            collector._fetch_search_results = Mock(
                return_value=collector._parse_search_results(SAMPLE_THREADS_SEARCH_HTML, limit=5)
            )
            collector._fetch_post_detail = Mock(side_effect=lambda row: row)

            rows = collector.fetch_latest_posts(limit=2, fresh_mode=True)

            self.assertEqual(len(rows), 2)

            conn = db._adapter.get_connection()
            try:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM threads WHERE channel = 'threads'")
                self.assertEqual(c.fetchone()[0], 2)
                c.execute("SELECT COUNT(*) FROM thread_items")
                self.assertEqual(c.fetchone()[0], 2)
            finally:
                conn.close()
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
