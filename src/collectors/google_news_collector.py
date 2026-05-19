"""
GoogleNewsCollector — 品牌敘事訊號採集器 v3

【定位重新設計】
  Google News ≠ 民意資料來源
  Google News  = 品牌正在推什麼敘事的偵測層

  用途：
    1. 偵測品牌本週主動推播的公關訊號（促銷活動、危機應對、企業公告）
    2. 產出「敘事關鍵詞清單」，提供 PTT/Dcard 採集器做交叉比對
       → 「7-ELEVEN 在推冰品促銷」→ PTT 有沒有人抱怨品質？

  不做的事：
    - 不再提取新聞全文（公關稿全文對輿情分析無意義）
    - 不再用 newspaper3k（高失敗率、高延遲）
    - 不再因「無全文」而跳過文章

【資料結構】
  content = RSS title + summary（足夠做敘事分類）
  narrative_type = 自動分類敘事類型（促銷 / 公告 / 危機 / 中立）

渠道識別：channel = "google_news"
"""

import os
import feedparser
import urllib.parse
import re
from typing import List, Dict, Optional

from src.utils.db_manager import SentimentDB
from src.config.brands import get_search_query, is_relevant_with_two_stage_attribution


# ── 敘事類型關鍵詞分類 ───────────────────────────────────────────
NARRATIVE_RULES = [
    ("危機曝光",  ["事故", "投訴", "抱怨", "食安", "問題", "爭議", "違規", "罰款", "下架",
                    "道歉", "危機", "醜聞", "負評", "客訴", "糾紛", "違法", "受傷"]),
    ("促銷活動",  ["優惠", "折扣", "買一送一", "限時", "特價", "活動", "贈品", "集點",
                    "兌換", "開賣", "上市", "限定", "免費", "抽獎", "好康", "送"]),
    ("企業公告",  ["股東", "財報", "股價", "收購", "合作", "簽約", "擴店", "展店",
                    "任命", "人事", "策略", "轉型", "永續", "ESG", "獲獎"]),
    ("品牌行銷",  ["代言", "聯名", "限量", "新品", "推出", "首賣", "體驗",
                    "門市", "快閃", "巡迴", "音樂節", "藝人"]),
]

def classify_narrative(title: str, summary: str = "") -> str:
    """從標題和摘要自動分類敘事類型。"""
    combined = (title + " " + summary).lower()
    for narrative_type, keywords in NARRATIVE_RULES:
        if any(kw in combined for kw in keywords):
            return narrative_type
    return "中立報導"


def clean_summary(raw: str) -> str:
    """清理 RSS summary 中的 HTML 標籤和多餘空白。"""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


class GoogleNewsCollector:
    CHANNEL = "google_news"
    SOURCE_NAME = "Google News"

    def __init__(self, keyword: str, db: Optional[SentimentDB] = None):
        self.keyword      = keyword
        self.db           = db
        self.search_query = get_search_query(keyword, "google_news")

    def _fetch_rss_feed(self, limit: int = 30) -> List[Dict]:
        """透過 Google News RSS 獲取新聞列表。

        優先使用 ScraperAPI 代理（繞過雲端機房 IP 封鎖）；
        無 API key 時降級為直連。
        """
        encoded = urllib.parse.quote(self.search_query)
        rss_url = (
            f"https://news.google.com/rss/search"
            f"?q={encoded}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )

        scraper_key = os.getenv("SCRAPERAPI_KEY", "")

        try:
            if scraper_key:
                # ScraperAPI 代理：用 requests params 傳遞，避免雙重編碼
                import requests as _req
                print(f"  [Google News] 使用 ScraperAPI 代理採集...")
                resp = _req.get(
                    "https://api.scraperapi.com",
                    params={"api_key": scraper_key, "url": rss_url},
                    timeout=30
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)
            else:
                print(f"  [Google News] 直連採集（無 ScraperAPI）...")
                feed = feedparser.parse(rss_url)

            print(f"  [Google News] RSS 回傳 {len(feed.entries)} 筆")
            results = []
            for entry in feed.entries[:limit]:
                summary = clean_summary(
                    entry.get("summary", "") or entry.get("description", "")
                )
                results.append({
                    "title":     entry.title,
                    "link":      entry.link,
                    "published": entry.get("published", ""),
                    "summary":   summary,
                    "source":    entry.get("source", {}).get("title", "Google News"),
                })
            return results
        except Exception as e:
            print(f"  [Google News] RSS 獲取失敗：{e}")
            try:
                print("  [Google News] 改用直連 RSS fallback...")
                feed = feedparser.parse(rss_url)
                results = []
                for entry in feed.entries[:limit]:
                    summary = clean_summary(
                        entry.get("summary", "") or entry.get("description", "")
                    )
                    results.append({
                        "title":     entry.title,
                        "link":      entry.link,
                        "published": entry.get("published", ""),
                        "summary":   summary,
                        "source":    entry.get("source", {}).get("title", "Google News"),
                    })
                print(f"  [Google News] fallback RSS 回傳 {len(results)} 筆")
                return results
            except Exception as fallback_error:
                print(f"  [Google News] fallback 也失敗：{fallback_error}")
                return []

    def fetch_latest_posts(self, limit: int = 15, fresh_mode: bool = False) -> List[Dict]:
        """
        主入口：採集 Google News 品牌敘事訊號。

        content = title + RSS summary（不進網頁，不提取全文）
        narrative_type 欄位供報告層識別敘事類型

        【設計原則】
        Google News RSS 已透過品牌搜尋詞過濾，此層不再做 is_brand_relevant 二次驗證。
        原因：危機事件往往使用非預期措辭（如「超商」而非「7-ELEVEN」），
              keyword 枚舉無法涵蓋所有可能，二次過濾只會造成漏報。
              信任 Google 搜尋的品牌相關性判斷即可。
        """
        print(f"  Fetching Google News for keyword: {self.keyword}（敘事訊號模式）...")
        raw_news = self._fetch_rss_feed(limit * 2)
        title_keys = [f"gnews_title:{self.keyword}:{item['title']}" for item in raw_news]
        existing_keys = (
            self.db.get_existing_threads(title_keys)
            if (not fresh_mode and self.db and title_keys) else set()
        )

        articles = []
        narrative_counts: Dict[str, int] = {}
        skipped_dup = 0

        for item, title_key in zip(raw_news, title_keys):
            if len(articles) >= limit:
                break

            matched, reason = is_relevant_with_two_stage_attribution(
                self.keyword,
                item["title"],
                item["summary"],
            )
            if not matched:
                continue

            # 去重（Google News redirect URL 每次不同，改用標題去重）
            if title_key in existing_keys:
                skipped_dup += 1
                continue

            # 敘事類型分類
            narrative_type = classify_narrative(item["title"], item["summary"])
            narrative_counts[narrative_type] = narrative_counts.get(narrative_type, 0) + 1

            # content = 標題 + RSS 摘要
            content = item["title"]
            if item["summary"] and item["summary"] != item["title"]:
                content = item["title"] + "\n" + item["summary"]

            print(f"  [Google News] [{narrative_type}/{reason}] {item['title'][:50]}...")

            article = {
                "title":          item["title"],
                "link":           item["link"],
                "storage_key":    title_key,
                "source":         item["source"],
                "published":      item["published"],
                "content":        content,
                "channel":        self.CHANNEL,
                "keyword":        self.keyword,
                "push_count":     0,
                "boo_count":      0,
                "neutral_count":  0,
                "comment_count":  0,
                "push_items":     [],
                "narrative_type": narrative_type,
            }

            if self.db:
                thread_id = self.db.save_thread(
                    url=title_key,             # 以 title_key 做唯一識別，避免 redirect URL 漂移
                    source_name=self.SOURCE_NAME,
                    channel=self.CHANNEL,
                    title=item["title"],
                    board=narrative_type,       # board 欄位存敘事類型
                    keyword=self.keyword,
                    published_at=item["published"],
                    push_count=0,
                    boo_count=0,
                    neutral_count=0,
                    comment_count=0,
                )
                if content:
                    self.db.save_thread_item(thread_id, content, item_type="main")

            articles.append(article)

        # 敘事分布摘要
        if narrative_counts:
            dist = "、".join(f"{k}×{v}" for k, v in narrative_counts.items())
            print(f"  [Google News] 敘事分布：{dist}")
        if skipped_dup > 0:
            print(f"  [Google News] 跳過已採集：{skipped_dup} 篇（用 --fresh 強制重採）")

        print(f"  → Google News: {len(articles)} 篇完成\n")
        return articles

    def get_narrative_keywords(self, articles: List[Dict]) -> List[str]:
        """
        從採集結果提取「敘事關鍵詞」，
        供 PTT/Dcard 採集器做交叉比對搜尋（未來功能）。

        例：Google News 偵測到「冰品促銷」敘事
         → PTT 補充搜尋「7-11 冰品」看有無抱怨
        """
        keywords = []
        for a in articles:
            title = a.get("title", "")
            bracket_items = re.findall(r"「([^」]{2,10})」", title)
            keywords.extend(bracket_items)
        seen = set()
        result = []
        for kw in keywords:
            if kw not in seen and len(kw) >= 2:
                seen.add(kw)
                result.append(kw)
        return result[:5]
