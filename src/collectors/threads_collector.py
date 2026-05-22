"""
ThreadsCollector — Threads 搜尋採集器 v1（ScraperAPI 單一路徑）

策略：
  1. 以 Threads 搜尋頁為主，每個搜尋詞抓前幾篇高訊號結果
  2. 官方帳優先，但保留一般使用者真實反饋
  3. 單篇貼文只做必要補抓，不做 reply thread 深爬

渠道識別：channel = "threads"
"""

import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from src.config.brands import get_brand_config, get_search_terms, is_brand_relevant
from src.utils.db_manager import SentimentDB

SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"
THREADS_SEARCH_URL = "https://www.threads.com/search"
DEFAULT_FETCH_LIMIT = 8


class ThreadsCollector:
    CHANNEL = "threads"
    SOURCE_NAME = "Threads"

    def __init__(self, keyword: str, db: Optional[SentimentDB] = None):
        self.keyword = keyword
        self.db = db
        self._scraperapi_key = os.getenv("SCRAPERAPI_KEY", "").strip()
        self._bypass_mode = "scraperapi" if self._scraperapi_key else "disabled"
        self.search_terms = get_search_terms(keyword, "threads")
        self.brand_config = get_brand_config(keyword)
        try:
            self._search_cache_minutes = max(0, int(os.getenv("THREADS_SEARCH_CACHE_MINUTES", "45")))
        except ValueError:
            self._search_cache_minutes = 45

        if self._scraperapi_key:
            print("  [Threads] 使用 ScraperAPI 模式")
        else:
            print("  [Threads] 未設定 SCRAPERAPI_KEY，Threads 採集停用")

    def _search_cache_key(self) -> str:
        return f"threads_search:v1:{self.keyword}"

    def _scraperapi_get(self, target_url: str, render: bool = True, timeout: int = 90):
        return requests.get(
            SCRAPERAPI_ENDPOINT,
            params={
                "api_key": self._scraperapi_key,
                "url": target_url,
                "render": "true" if render else "false",
            },
            timeout=timeout,
        )

    def _build_search_url(self, term: str) -> str:
        query = urllib.parse.quote(term)
        return f"{THREADS_SEARCH_URL}?q={query}&serp_type=default"

    def _official_handles(self) -> List[str]:
        handles = [h.lower() for h in self.brand_config.get("official_accounts", [])]
        if handles:
            return handles

        guessed = []
        for token in self.brand_config.get("validation_keywords", []):
            cleaned = re.sub(r"[^a-z0-9_]", "", token.lower())
            if cleaned and len(cleaned) >= 4:
                guessed.append(cleaned)
        return guessed

    def _is_official_account(self, username: str, full_name: str) -> bool:
        uname = (username or "").lower()
        fname = (full_name or "").lower()
        official_handles = self._official_handles()
        if uname in official_handles:
            return True
        return any(token in uname or token in fname for token in official_handles)

    def _extract_fragments(self, fragment_blob: str) -> str:
        matches = re.findall(r'"plaintext":"((?:[^"\\]|\\.)*)"', fragment_blob)
        texts = []
        for raw in matches:
            try:
                texts.append(json.loads(f'"{raw}"'))
            except Exception:
                texts.append(raw.replace('\\"', '"'))
        return " ".join(t.strip() for t in texts if t.strip())

    def _parse_search_results(self, html: str, limit: int) -> List[Dict]:
        pattern = re.compile(
            r'"username":"(?P<username>[^"]+)"'
            r'.+?"full_name":"(?P<full_name>[^"]*)"'
            r'.+?"code":"(?P<code>[^"]+)"'
            r'.+?"taken_at":(?P<taken_at>\d+)'
            r'.+?"text_fragments":\{"fragments":\[(?P<fragments>.*?)\]\}'
            r'.+?"direct_reply_count":(?P<reply>\d+)'
            r'.+?"repost_count":(?P<repost>\d+)'
            r'.+?"quote_count":(?P<quote>\d+)'
            r'.+?"reshare_count":(?P<reshare>\d+)',
            re.DOTALL,
        )

        seen_links = set()
        rows: List[Dict] = []
        for match in pattern.finditer(html):
            username = match.group("username")
            full_name = match.group("full_name")
            code = match.group("code")
            content = self._extract_fragments(match.group("fragments"))
            if not content:
                continue
            if not is_brand_relevant(self.keyword, content, content):
                continue

            link = f"https://www.threads.com/@{username}/post/{code}"
            if link in seen_links:
                continue
            seen_links.add(link)

            taken_at = datetime.fromtimestamp(int(match.group("taken_at")), tz=timezone.utc).isoformat(timespec="seconds")
            rows.append(
                {
                    "username": username,
                    "full_name": full_name,
                    "link": link,
                    "content": content,
                    "title": content[:80],
                    "published": taken_at,
                    "reply_count": int(match.group("reply")),
                    "repost_count": int(match.group("repost")),
                    "quote_count": int(match.group("quote")),
                    "reshare_count": int(match.group("reshare")),
                    "is_official": self._is_official_account(username, full_name),
                }
            )

        rows.sort(
            key=lambda row: (
                0 if row["is_official"] else 1,
                -(
                    row["reply_count"]
                    + row["repost_count"] * 2
                    + row["quote_count"] * 2
                    + row["reshare_count"]
                ),
                row["published"],
            )
        )
        return rows[:limit]

    def _fetch_search_results(self, term: str, limit: int) -> List[Dict]:
        if not self._scraperapi_key:
            return []
        try:
            resp = self._scraperapi_get(self._build_search_url(term), render=True)
            if resp.status_code != 200:
                print(f"  [Threads/search] 「{term}」→ {resp.status_code}")
                return []
            return self._parse_search_results(resp.text, limit=limit)
        except Exception as e:
            print(f"  [Threads/search] 「{term}」失敗：{e}")
            return []

    def _fetch_post_detail(self, row: Dict) -> Dict:
        if not self._scraperapi_key:
            return row
        try:
            resp = self._scraperapi_get(row["link"], render=False, timeout=60)
            if resp.status_code != 200:
                return row

            title_match = re.search(r'<meta property="og:title" content="([^"]+)"', resp.text)
            desc_match = re.search(r'<meta name="description" content="([^"]+)"', resp.text)

            if title_match:
                row["title"] = title_match.group(1).strip() or row["title"]
            if desc_match:
                row["content"] = desc_match.group(1).strip() or row["content"]
            return row
        except Exception:
            return row

    def fetch_latest_posts(self, limit: int = DEFAULT_FETCH_LIMIT, fresh_mode: bool = False) -> List[Dict]:
        terms_str = " / ".join(self.search_terms)
        print(f"  Fetching Threads posts for keyword: {self.keyword}（搜尋詞：{terms_str}）...")
        if not self._scraperapi_key:
            print("  [Threads] 未設定 SCRAPERAPI_KEY，跳過採集")
            return []

        cached_rows: List[Dict] = []
        cache_key = self._search_cache_key()
        if not fresh_mode and self.db and self._search_cache_minutes > 0:
            cached = self.db.get_collector_cache(cache_key)
            if cached and isinstance(cached.get("results"), list):
                cached_rows = cached["results"]
                print(
                    f"  [Threads] 命中搜尋快取（{self._search_cache_minutes} 分鐘內）"
                    f"：沿用 {len(cached_rows)} 篇候選貼文"
                )

        rows = cached_rows
        if not rows:
            merged: List[Dict] = []
            seen_links = set()
            per_term_limit = max(limit, 5)
            for term in self.search_terms:
                for row in self._fetch_search_results(term, limit=per_term_limit):
                    if row["link"] in seen_links:
                        continue
                    seen_links.add(row["link"])
                    merged.append(row)
            merged.sort(
                key=lambda row: (
                    0 if row["is_official"] else 1,
                    -(
                        row["reply_count"]
                        + row["repost_count"] * 2
                        + row["quote_count"] * 2
                        + row["reshare_count"]
                    ),
                    row["published"],
                )
            )
            rows = merged[: max(limit * 2, limit)]
            if self.db and self._search_cache_minutes > 0 and rows:
                self.db.set_collector_cache(cache_key, {"results": rows}, ttl_minutes=self._search_cache_minutes)

        articles: List[Dict] = []
        for row in rows[:limit]:
            should_enrich = row.get("is_official") and (fresh_mode or not cached_rows)
            detailed = self._fetch_post_detail(dict(row)) if should_enrich else dict(row)
            title = (detailed.get("title") or detailed.get("content") or "")[:120]
            content = detailed.get("content") or ""
            articles.append(
                {
                    "title": title,
                    "link": detailed["link"],
                    "source": self.SOURCE_NAME,
                    "published": detailed.get("published") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "content": content,
                    "channel": self.CHANNEL,
                    "keyword": self.keyword,
                    "author": detailed.get("username"),
                    "comment_count": detailed.get("reply_count", 0),
                    "board": "official" if detailed.get("is_official") else "public_feedback",
                    "threads_metrics": {
                        "reply_count": detailed.get("reply_count", 0),
                        "repost_count": detailed.get("repost_count", 0),
                        "quote_count": detailed.get("quote_count", 0),
                        "reshare_count": detailed.get("reshare_count", 0),
                    },
                }
            )

        print(f"  → Threads: {len(articles)} 篇完成")
        return articles
