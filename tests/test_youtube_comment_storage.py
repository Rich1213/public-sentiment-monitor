import os
import tempfile
import unittest

from src.collectors.youtube_collector import YouTubeCollector
from src.utils.db_manager import SentimentDB


class StubYouTubeCollector(YouTubeCollector):
    def __init__(self, keyword: str, db=None):
        self.keyword = keyword
        self.db = db
        self.api_key = "test-key"
        self.search_terms = ["7-11"]

    def _search_videos(self, max_results=20):
        return [
            {
                "video_id": "video-1",
                "title": "7-11 活蟲事件",
                "channel": "NewsChannel",
                "published": "2026-05-18T10:00:00Z",
                "description": "描述",
                "matched_term": "7-11",
            }
        ]

    def _get_video_stats(self, video_ids):
        return {"video-1": 50000}

    def _get_comments(self, video_id, max_comments=30):
        return [
            {
                "comment_id": "c1",
                "author": "user1",
                "content": "這也太扯了吧",
                "like_count": 12,
                "published_at": "2026-05-18T10:01:00Z",
            },
            {
                "comment_id": "c2",
                "author": "user2",
                "content": "看起來像食安問題",
                "like_count": 7,
                "published_at": "2026-05-18T10:02:00Z",
            },
        ]


class YouTubeCommentStorageTest(unittest.TestCase):
    def test_select_comments_for_analysis_prefers_high_value_comments(self):
        collector = StubYouTubeCollector("7-ELEVEN")
        comments = [
            {"comment_id": "c1", "author": "u1", "content": "還好", "like_count": 0, "published_at": "2026-05-18T10:00:00Z"},
            {"comment_id": "c2", "author": "u2", "content": "7-11 這次食安真的很扯，活蟲事件太誇張了", "like_count": 2, "published_at": "2026-05-18T10:01:00Z"},
            {"comment_id": "c3", "author": "u3", "content": "我也遇到同樣問題，商品衛生完全不行，應該下架", "like_count": 8, "published_at": "2026-05-18T10:02:00Z"},
        ]

        selected = collector._select_comments_for_analysis(comments)

        self.assertEqual([item["comment_id"] for item in selected], ["c3", "c2"])

    def test_thread_items_deduplicate_by_platform_item_id(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            thread_id = db.save_thread(
                url="https://www.youtube.com/watch?v=video-1",
                source_name="YouTube",
                channel="youtube",
                title="測試影片",
                keyword="7-ELEVEN",
            )
            db.save_thread_item(
                thread_id,
                "第一則留言",
                item_type="comment",
                author="user1",
                sequence=1,
                platform_item_id="c1",
            )
            db.save_thread_item(
                thread_id,
                "第一則留言",
                item_type="comment",
                author="user1",
                sequence=1,
                platform_item_id="c1",
            )

            conn = db.adapter.get_connection()
            try:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) AS cnt FROM thread_items WHERE platform_item_id = ?", ("c1",))
                row = c.fetchone()
                count = row["cnt"]
            finally:
                conn.close()

            self.assertEqual(count, 1)
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_item_analyses_roundtrip(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            run_id = db.create_run("7-ELEVEN")
            thread_id = db.save_thread(
                url="https://www.youtube.com/watch?v=video-1",
                source_name="YouTube",
                channel="youtube",
                title="測試影片",
                keyword="7-ELEVEN",
                platform_id="video-1",
            )
            db.save_thread_item(
                thread_id,
                "這也太扯了吧",
                item_type="comment",
                author="user1",
                sequence=1,
                platform_item_id="c1",
            )
            item_id = db.get_thread_item_id_by_platform_item_id("c1")
            self.assertIsNotNone(item_id)

            db.save_item_analysis(
                item_id,
                run_id,
                {
                    "sentiment": "負面",
                    "score": 4,
                    "theme": "食安抱怨",
                    "reason": "留言高互動且明確指向食安",
                    "voice_source": "YouTube留言",
                    "analyzed_with": "留言",
                    "model_used": "test",
                },
                analyzed_content="這也太扯了吧",
            )

            rows = db.get_run_item_analyses(run_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["theme"], "食安抱怨")
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_youtube_collector_saves_comment_items_once(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            db = SentimentDB(db_path=path)
            collector = StubYouTubeCollector("7-ELEVEN", db=db)
            collector.fetch_latest_posts(limit=5, fresh_mode=False)
            collector.fetch_latest_posts(limit=5, fresh_mode=False)

            conn = db.adapter.get_connection()
            try:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) AS cnt FROM thread_items WHERE item_type = 'comment'")
                row = c.fetchone()
                comment_count = row["cnt"]
                c.execute("SELECT content FROM thread_items WHERE item_type = 'main' LIMIT 1")
                main_row = c.fetchone()
                main_content = main_row["content"]
            finally:
                conn.close()

            self.assertEqual(comment_count, 2)
            self.assertIn("---留言精選---", main_content)
            self.assertNotIn("---留言---", main_content)
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
