"""
YouTubeCollector — YouTube 影片留言採集器

【採集策略】
  符合以下任一條件即納入分析：
  ① 觀看數 >= VIEWS_THRESHOLD（預設 10,000）— 已擴散的輿論
  ② 發布時間在 RECENT_HOURS 小時內（預設 48h）— 剛爆發的新事件

【不做關鍵字枚舉】
  危機判斷完全交給 AI 情感分析，不預設任何危機詞彙

【Quota 消耗估算（每日 10,000 免費）】
  搜尋：100 / 次
  影片詳情：1 / 次（批次查詢）
  留言：1 / 頁（50 則 / 頁）
  5 個品牌 × 1 次搜尋 = 500 quota / 天，綽綽有餘

渠道識別：channel = "youtube"
"""

import os
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

from src.utils.db_manager import SentimentDB
from src.config.brands import get_search_query

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
VIEWS_THRESHOLD  = int(os.getenv("YOUTUBE_VIEWS_THRESHOLD", "10000"))
RECENT_HOURS     = int(os.getenv("YOUTUBE_RECENT_HOURS", "48"))
COMMENTS_PER_VIDEO = int(os.getenv("YOUTUBE_COMMENTS_PER_VIDEO", "30"))


class YouTubeCollector:
    CHANNEL     = "youtube"
    SOURCE_NAME = "YouTube"

    def __init__(self, keyword: str, db: Optional[SentimentDB] = None):
        self.keyword      = keyword
        self.db           = db
        self.api_key      = os.getenv("YOUTUBE_API_KEY", "")
        self.search_query = get_search_query(keyword, "youtube")

        if not self.api_key:
            raise ValueError("YOUTUBE_API_KEY 環境變數未設定")

    # ── 搜尋影片 ─────────────────────────────────────────────────

    def _search_videos(self, max_results: int = 20) -> List[Dict]:
        """搜尋相關影片，回傳基本資訊列表。"""
        url = f"{YOUTUBE_API_BASE}/search"
        params = {
            "key":        self.api_key,
            "q":          self.search_query,
            "part":       "snippet",
            "type":       "video",
            "regionCode": "TW",
            "relevanceLanguage": "zh-Hant",
            "order":      "relevance",
            "maxResults": max_results,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [
                {
                    "video_id":    it["id"]["videoId"],
                    "title":       it["snippet"]["title"],
                    "channel":     it["snippet"]["channelTitle"],
                    "published":   it["snippet"]["publishedAt"],
                    "description": it["snippet"].get("description", "")[:200],
                }
                for it in items
            ]
        except Exception as e:
            print(f"  [YouTube] 搜尋失敗：{e}")
            return []

    # ── 批次查詢影片詳情（觀看數）───────────────────────────────

    def _get_video_stats(self, video_ids: List[str]) -> Dict[str, int]:
        """批次查詢影片觀看數，回傳 {video_id: view_count}。"""
        if not video_ids:
            return {}
        url = f"{YOUTUBE_API_BASE}/videos"
        params = {
            "key":  self.api_key,
            "id":   ",".join(video_ids),
            "part": "statistics",
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            result = {}
            for it in resp.json().get("items", []):
                vid = it["id"]
                stats = it.get("statistics", {})
                result[vid] = int(stats.get("viewCount", 0))
            return result
        except Exception as e:
            print(f"  [YouTube] 影片詳情查詢失敗：{e}")
            return {}

    # ── 抓留言 ───────────────────────────────────────────────────

    def _get_comments(self, video_id: str, max_comments: int = COMMENTS_PER_VIDEO) -> List[str]:
        """取得影片頂層留言（按相關性排序）。"""
        url = f"{YOUTUBE_API_BASE}/commentThreads"
        params = {
            "key":        self.api_key,
            "videoId":    video_id,
            "part":       "snippet",
            "order":      "relevance",
            "maxResults": min(max_comments, 100),
            "textFormat": "plainText",
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 403:
                # 影片關閉留言功能
                return []
            resp.raise_for_status()
            comments = []
            for it in resp.json().get("items", []):
                text = it["snippet"]["topLevelComment"]["snippet"].get("textDisplay", "")
                likes = it["snippet"]["topLevelComment"]["snippet"].get("likeCount", 0)
                if text.strip():
                    comments.append(f"[{likes}讚] {text[:200]}")
            return comments
        except Exception as e:
            print(f"  [YouTube] 留言抓取失敗 ({video_id})：{e}")
            return []

    # ── 主入口 ───────────────────────────────────────────────────

    def fetch_latest_posts(self, limit: int = 10, fresh_mode: bool = False) -> List[Dict]:
        """
        採集 YouTube 影片留言。

        納入條件（OR）：
          ① 觀看數 >= VIEWS_THRESHOLD
          ② 發布時間在 RECENT_HOURS 小時內

        content = 影片標題 + 描述 + 熱門留言（最多 COMMENTS_PER_VIDEO 則）
        """
        print(f"  Fetching YouTube for keyword: {self.keyword}...")

        if not self.api_key:
            print("  [YouTube] YOUTUBE_API_KEY 未設定，跳過")
            return []

        # 1. 搜尋影片
        raw_videos = self._search_videos(max_results=min(limit * 3, 30))
        if not raw_videos:
            print("  [YouTube] 搜尋無結果")
            return []

        # 2. 批次查詢觀看數
        video_ids = [v["video_id"] for v in raw_videos]
        stats_map = self._get_video_stats(video_ids)

        # 3. 過濾：觀看數 OR 新發布
        now_utc    = datetime.now(timezone.utc)
        cutoff     = now_utc - timedelta(hours=RECENT_HOURS)
        qualified  = []

        for v in raw_videos:
            vid        = v["video_id"]
            view_count = stats_map.get(vid, 0)
            try:
                pub_dt = datetime.fromisoformat(v["published"].replace("Z", "+00:00"))
            except Exception:
                pub_dt = now_utc - timedelta(days=365)  # 解析失敗視為舊文

            is_popular = view_count >= VIEWS_THRESHOLD
            is_recent  = pub_dt >= cutoff

            if is_popular or is_recent:
                reason = []
                if is_popular: reason.append(f"觀看數 {view_count:,}")
                if is_recent:  reason.append(f"新發布 {pub_dt.strftime('%m/%d %H:%M')}")
                v["view_count"] = view_count
                v["reason"]     = "、".join(reason)
                qualified.append(v)

        print(f"  [YouTube] 搜尋到 {len(raw_videos)} 支，符合條件 {len(qualified)} 支")

        # 4. 逐支抓留言
        articles = []
        skipped_dup = 0

        for v in qualified[:limit]:
            vid   = v["video_id"]
            url   = f"https://www.youtube.com/watch?v={vid}"
            title = v["title"]

            # 去重
            if not fresh_mode and self.db and self.db.is_duplicate(url):
                skipped_dup += 1
                continue

            comments = self._get_comments(vid)
            comment_block = "\n".join(comments[:COMMENTS_PER_VIDEO]) if comments else ""

            content_parts = [title]
            if v["description"]:
                content_parts.append(v["description"])
            if comment_block:
                content_parts.append("---留言---")
                content_parts.append(comment_block)
            content = "\n".join(content_parts)

            print(f"  [YouTube] [{v['reason']}] {title[:50]}... ({len(comments)} 則留言)")

            article = {
                "title":         title,
                "link":          url,
                "source":        v["channel"],
                "published":     v["published"],
                "content":       content,
                "channel":       self.CHANNEL,
                "keyword":       self.keyword,
                "push_count":    v.get("view_count", 0),
                "boo_count":     0,
                "neutral_count": 0,
                "comment_count": len(comments),
                "push_items":    [],
            }

            if self.db:
                thread_id = self.db.save_thread(
                    url=url,
                    source_name=self.SOURCE_NAME,
                    channel=self.CHANNEL,
                    title=title,
                    board=v["channel"],
                    keyword=self.keyword,
                    published_at=v["published"],
                    push_count=v.get("view_count", 0),
                    comment_count=len(comments),
                )
                if content:
                    self.db.save_thread_item(thread_id, content, item_type="main")

            articles.append(article)

        if skipped_dup > 0:
            print(f"  [YouTube] 跳過已採集：{skipped_dup} 支（用 --fresh 強制重採）")
        print(f"  → YouTube: {len(articles)} 支完成\n")
        return articles
