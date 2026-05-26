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
import html
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
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
        try:
            self._max_post_age_days = max(0, int(os.getenv("THREADS_MAX_POST_AGE_DAYS", "45")))
        except ValueError:
            self._max_post_age_days = 45
        try:
            self._search_max_workers = max(1, int(os.getenv("THREADS_SEARCH_MAX_WORKERS", "2")))
        except ValueError:
            self._search_max_workers = 2

        if self._scraperapi_key:
            print("  [Threads] 使用 ScraperAPI 模式")
        else:
            print("  [Threads] 未設定 SCRAPERAPI_KEY，Threads 採集停用")

    def _search_cache_key(self) -> str:
        return f"threads_search:v1:{self.keyword}"

    def _ordered_search_terms(self) -> List[str]:
        ordered: List[str] = []
        seen = set()
        for term in self._official_handles() + self.search_terms:
            cleaned = (term or "").strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(cleaned)
        return ordered

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

    def _threads_context_keywords(self) -> List[str]:
        return [kw.lower() for kw in self.brand_config.get("threads_context_keywords", []) if kw]

    def _threads_store_context_keywords(self) -> List[str]:
        generic_store_markers = [
            "超商", "便利商店", "門市", "店員", "交貨便", "ibon",
            "city cafe", "openpoint", "open point", "咖啡", "鮮食",
            "御飯糰", "茶葉蛋", "霜淇淋", "思樂冰", "取貨", "寄杯",
            "會員", "冷麵", "飯糰", "調酒",
        ]
        return list(dict.fromkeys(
            kw for kw in (self._threads_context_keywords() + generic_store_markers)
            if kw not in {"台灣", "臺灣"}
        ))

    def _threads_focus_keywords(self) -> List[str]:
        return [
            "新品", "新口味", "口味", "聯名", "開箱", "回購", "好吃", "難吃",
            "咖啡", "飲料", "調酒", "霜淇淋", "冰淇淋", "飯糰", "便當",
            "麵包", "甜點", "沙拉", "御飯糰", "茶葉蛋", "思樂冰", "熱狗",
            "三明治", "冷麵", "涼麵", "鮮食", "服務", "店員", "態度",
            "排隊", "門市", "取貨", "結帳", "寄杯",
        ]

    def _has_cjk_text(self, text: str) -> bool:
        return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", text or ""))

    def _is_official_giveaway_post(self, content: str) -> bool:
        text = content or ""
        if any(marker in text for marker in ["抽獎", "免費獲得", "中獎", "贈送", "贈品"]):
            return True
        if "活動時間" in text and "指定規則" in text:
            return True
        if "追蹤" in text and "留言" in text and ("獲得" in text or "機會" in text):
            return True
        return False

    def _is_official_account(self, username: str, full_name: str) -> bool:
        uname = (username or "").lower()
        fname = (full_name or "").lower()
        official_handles = self._official_handles()
        if uname in official_handles:
            return True
        return any(token in uname or token in fname for token in official_handles)

    def _is_threads_relevant(self, username: str, full_name: str, content: str) -> bool:
        combined = " ".join([username or "", full_name or "", content or ""]).lower()
        if not self._has_cjk_text(content):
            return False

        if self._is_official_account(username, full_name):
            if self._is_official_giveaway_post(content):
                return False
            return True

        if not is_brand_relevant(self.keyword, content, content):
            return False

        overseas_markers = [
            "singapore", "malaysia", "japan", "tokyo", "osaka", "philippines",
            "manila", "hong kong", "thailand", "kuala lumpur", "rm", "sgd", "¥", "usd",
        ]
        taiwan_markers = self._threads_context_keywords()
        store_markers = self._threads_store_context_keywords()
        focus_markers = self._threads_focus_keywords()
        has_taiwan_context = any(marker in combined for marker in taiwan_markers)
        has_overseas_context = any(marker in combined for marker in overseas_markers)
        has_store_context = any(marker in combined for marker in store_markers)
        has_focus_signal = any(marker in combined for marker in focus_markers)

        if has_overseas_context and not has_taiwan_context:
            return False

        generic_english_brand_markers = ["7-eleven", "7-11", "seven eleven", "711"]
        has_generic_english_brand = any(marker in combined for marker in generic_english_brand_markers)

        if has_generic_english_brand and not has_taiwan_context:
            return False

        if not has_store_context:
            return False

        if not has_focus_signal:
            return False

        return True

    def _extract_fragments(self, fragment_blob: str) -> str:
        matches = re.findall(r'"plaintext":"((?:[^"\\]|\\.)*)"', fragment_blob)
        texts = []
        for raw in matches:
            try:
                texts.append(json.loads(f'"{raw}"'))
            except Exception:
                texts.append(raw.replace('\\"', '"'))
        return " ".join(t.strip() for t in texts if t.strip())

    def _extract_fragments_from_object(self, payload) -> str:
        if isinstance(payload, dict):
            fragments = payload.get("fragments")
            if isinstance(fragments, list):
                texts = []
                for fragment in fragments:
                    if not isinstance(fragment, dict):
                        continue
                    plain = fragment.get("plaintext")
                    if plain:
                        texts.append(str(plain).strip())
                return " ".join(text for text in texts if text)
            for value in payload.values():
                text = self._extract_fragments_from_object(value)
                if text:
                    return text
            return ""
        if isinstance(payload, list):
            texts = [self._extract_fragments_from_object(item) for item in payload]
            return " ".join(text for text in texts if text)
        return ""

    def _iter_post_objects(self, payload):
        if isinstance(payload, dict):
            has_post_shape = (
                isinstance(payload.get("username"), str)
                and isinstance(payload.get("code"), str)
                and payload.get("taken_at") is not None
            )
            if has_post_shape:
                yield payload
            for value in payload.values():
                yield from self._iter_post_objects(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from self._iter_post_objects(item)

    def _parse_search_results_from_json(self, html_text: str, limit: int) -> List[Dict]:
        rows: List[Dict] = []
        seen_links = set()
        script_pattern = re.compile(
            r"<script[^>]*type=[\"']application/json[\"'][^>]*>(?P<body>.*?)</script>",
            re.DOTALL | re.IGNORECASE,
        )

        for match in script_pattern.finditer(html_text):
            body = html.unescape(match.group("body")).strip()
            if not body:
                continue
            try:
                payload = json.loads(body)
            except Exception:
                continue

            for post in self._iter_post_objects(payload):
                username = post.get("username") or ""
                full_name = post.get("full_name") or ""
                code = post.get("code") or ""
                content = self._extract_fragments_from_object(post.get("text_post_app_info") or {})
                if not content:
                    continue

                try:
                    taken_at_dt = datetime.fromtimestamp(int(post.get("taken_at")), tz=timezone.utc)
                except Exception:
                    continue
                if not self._is_post_recent_enough(taken_at_dt):
                    continue
                if not self._is_threads_relevant(username, full_name, content):
                    continue

                link = f"https://www.threads.com/@{username}/post/{code}"
                if link in seen_links:
                    continue
                seen_links.add(link)

                row = {
                    "username": username,
                    "full_name": full_name,
                    "link": link,
                    "content": content,
                    "title": content[:80],
                    "published": taken_at_dt.isoformat(timespec="seconds"),
                    "reply_count": int(post.get("direct_reply_count") or 0),
                    "repost_count": int(post.get("repost_count") or 0),
                    "quote_count": int(post.get("quote_count") or 0),
                    "reshare_count": int(post.get("reshare_count") or 0),
                    "is_official": self._is_official_account(username, full_name),
                }
                rows.append(row)

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

    def _has_result_markers(self, html: str) -> bool:
        return all(
            marker in html
            for marker in ('"username":"', '"taken_at":', '"plaintext":"', '"direct_reply_count":')
        )

    def _is_generic_threads_meta_title(self, title: str) -> bool:
        normalized = html.unescape((title or "")).strip().lower()
        if not normalized:
            return True
        return normalized.endswith(" on threads")

    def _is_post_recent_enough(self, taken_at: datetime) -> bool:
        if self._max_post_age_days <= 0:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._max_post_age_days)
        return taken_at >= cutoff

    def _parse_search_results(self, html: str, limit: int) -> List[Dict]:
        json_rows = self._parse_search_results_from_json(html, limit=limit)
        if json_rows:
            return json_rows

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
            taken_at_dt = datetime.fromtimestamp(int(match.group("taken_at")), tz=timezone.utc)
            if not self._is_post_recent_enough(taken_at_dt):
                continue
            if not self._is_threads_relevant(username, full_name, content):
                continue

            link = f"https://www.threads.com/@{username}/post/{code}"
            if link in seen_links:
                continue
            seen_links.add(link)

            taken_at = taken_at_dt.isoformat(timespec="seconds")
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
            rows = self._parse_search_results(resp.text, limit=limit)
            if rows:
                print(f"  [Threads/search] 「{term}」→ 解析 {len(rows)} 筆")
            elif self._has_result_markers(resp.text):
                print(f"  [Threads/search] 「{term}」→ 有頁面資料，但品牌過濾後為 0 筆")
            else:
                print(f"  [Threads/search] 「{term}」→ 空殼頁或無可解析結果")
            return rows
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
                meta_title = html.unescape(title_match.group(1)).strip()
                if meta_title and not self._is_generic_threads_meta_title(meta_title):
                    row["title"] = meta_title
            if desc_match:
                meta_desc = html.unescape(desc_match.group(1)).strip()
                row["content"] = meta_desc or row["content"]
            return row
        except Exception:
            return row

    def fetch_latest_posts(self, limit: int = DEFAULT_FETCH_LIMIT, fresh_mode: bool = False) -> List[Dict]:
        ordered_terms = self._ordered_search_terms()
        terms_str = " / ".join(ordered_terms)
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
            target_candidates = max(limit * 2, limit)
            processed_terms = 0
            for batch_start in range(0, len(ordered_terms), self._search_max_workers):
                batch_terms = ordered_terms[batch_start: batch_start + self._search_max_workers]
                batch_results: Dict[str, List[Dict]] = {term: [] for term in batch_terms}
                with ThreadPoolExecutor(max_workers=self._search_max_workers) as executor:
                    future_to_term = {
                        executor.submit(self._fetch_search_results, term, per_term_limit): term
                        for term in batch_terms
                    }
                    for future in as_completed(future_to_term):
                        term = future_to_term[future]
                        try:
                            batch_results[term] = future.result()
                        except Exception as e:
                            print(f"  [Threads/search] 「{term}」失敗：{e}")
                            batch_results[term] = []

                for term in batch_terms:
                    for row in batch_results[term]:
                        if row["link"] in seen_links:
                            continue
                        seen_links.add(row["link"])
                        merged.append(row)

                processed_terms += len(batch_terms)
                # Threads 搜尋 render=true 很慢；至少跑兩個 term 後，候選夠用就先停。
                if len(merged) >= target_candidates and processed_terms >= 2:
                    print(f"  [Threads] 候選已達 {len(merged)} 篇，提前停止後續搜尋詞")
                    break
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
            rows = merged[:target_candidates]
            if self.db and self._search_cache_minutes > 0 and rows:
                self.db.set_collector_cache(cache_key, {"results": rows}, ttl_minutes=self._search_cache_minutes)

        articles: List[Dict] = []
        for row in rows[:limit]:
            should_enrich = row.get("is_official") and (fresh_mode or not cached_rows)
            detailed = self._fetch_post_detail(dict(row)) if should_enrich else dict(row)
            title = (detailed.get("title") or detailed.get("content") or "")[:120]
            content = detailed.get("content") or ""
            article = {
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
            if self.db:
                thread_id = self.db.save_thread(
                    url=article["link"],
                    source_name=self.SOURCE_NAME,
                    channel=self.CHANNEL,
                    title=article["title"],
                    board=article["board"],
                    keyword=self.keyword,
                    published_at=article["published"],
                    comment_count=article["comment_count"],
                )
                if content:
                    self.db.save_thread_item(thread_id, content, item_type="main")
            articles.append(article)

        print(f"  → Threads: {len(articles)} 篇完成")
        return articles
