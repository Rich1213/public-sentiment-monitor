"""
DcardCollector — Dcard 渠道採集器 v7（SSR 搜尋 + 雲端 WAF 繞過）

策略：全站搜尋 SSR
  GET /search/posts?query={term}
  Next.js SSR 頁面，<article> 直接渲染在 HTML，BeautifulSoup 解析。
  多搜尋詞結果合併去重（by URL）。

━━━ 雲端部署 WAF 繞過（二擇一）━━━━━━━━━━━━━━━━━━━━━━━━━━━

Dcard 的 Cloudflare WAF 會在 IP 信譽層封鎖資料中心 IP（AWS/GCP/Azure）。
curl_cffi TLS 指紋偽裝只能繞過指紋偵測，對 IP 封鎖無效。
需在 .env 設定以下其中一個：

【方案 A — Residential Proxy（推薦）】
  DCARD_PROXY_URL=http://user:pass@proxy-host:port
  支援任何 SOCKS5/HTTP residential proxy（IPRoyal、Smartproxy、Bright Data 等）
  費用：~$3–10/GB，每日監控用量極低

【方案 B — ScraperAPI（零設定）】
  SCRAPERAPI_KEY=your_key_here
  https://www.scraperapi.com — 免費 5,000 次/月，付費 $49/月起
  會自動走 residential IP 並解 JS challenge

兩個都沒設定 → 直連（本機/台灣住宅 IP 可正常運作，雲端會 403）

渠道識別：channel = "dcard"
"""

import os
import re
import time
import random
import urllib.parse
from typing import List, Dict, Optional
from datetime import datetime

from bs4 import BeautifulSoup
from src.utils.db_manager import SentimentDB
from src.config.brands import get_search_query, is_brand_relevant

try:
    from curl_cffi import requests as cf_requests
    USE_CURL_CFFI = True
except ImportError:
    import requests as cf_requests
    USE_CURL_CFFI = False
    print("⚠️  [Dcard] curl_cffi 未安裝，將使用標準 requests。"
          "建議執行：pip install curl_cffi")

# ── WAF 繞過模式（讀 .env）──────────────────────────────────────
_PROXY_URL      = os.getenv("DCARD_PROXY_URL", "").strip()
_SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY", "").strip()

if _SCRAPERAPI_KEY:
    _BYPASS_MODE = "scraperapi"
    print(f"  [Dcard] 使用 ScraperAPI 模式（雲端 WAF 繞過）")
elif _PROXY_URL:
    _BYPASS_MODE = "proxy"
    print(f"  [Dcard] 使用 Residential Proxy 模式（{_PROXY_URL.split('@')[-1]}）")
else:
    _BYPASS_MODE = "direct"

DCARD_SEARCH_URL    = "https://www.dcard.tw/search/posts"
SCRAPERAPI_ENDPOINT = "https://api.scraperapi.com/"

REQUEST_DELAY = (2.0, 4.0)

# 允許的 Dcard 版別（消費者情緒有分析價值的版）
# 格式：Dcard API 回傳的 forumName 或 SSR 解析的版名（含 "Forum" 後綴兩種都收）
ALLOWED_FORUMS = {
    # 超商專版：最直接的消費者討論
    "超商", "超商 forum",
    # 食評：商品心得、食物體驗
    "美食", "美食 forum",
    # 網路購物：取貨、退貨、客服糾紛
    "網路購物", "網路購物 forum",
    # 閒聊：日常消費抱怨、品牌話題
    "閒聊", "閒聊 forum",
    # 有趣：有時超商話題在這裡爆紅
    "有趣", "有趣 forum",
    # 心情：消費者投訴情緒文
    "心情", "心情 forum",
}

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":           "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":  "zh-TW,zh;q=0.9,en-US;q=0.8",
    "Accept-Encoding":  "gzip, deflate, br",
    "sec-ch-ua":        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest":   "document",
    "sec-fetch-mode":   "navigate",
    "sec-fetch-site":   "none",
    "sec-fetch-user":   "?1",
    "upgrade-insecure-requests": "1",
}


class DcardCollector:
    CHANNEL     = "dcard"
    SOURCE_NAME = "Dcard"

    def __init__(self, keyword: str, db: Optional[SentimentDB] = None):
        self.keyword      = keyword
        self.db           = db
        self.search_query = get_search_query(keyword, "dcard")
        self.search_terms = [t.strip() for t in self.search_query.split(" OR ") if t.strip()]
        self.session      = self._init_session()

    def _init_session(self):
        if USE_CURL_CFFI:
            session = cf_requests.Session(impersonate="chrome124")
        else:
            import requests as req
            session = req.Session()
        session.headers.update(BROWSER_HEADERS)

        # Residential proxy：設定到 session
        if _BYPASS_MODE == "proxy":
            session.proxies = {
                "http":  _PROXY_URL,
                "https": _PROXY_URL,
            }

        # ScraperAPI 模式不需要暖機（直接走 API endpoint）
        if _BYPASS_MODE != "scraperapi":
            try:
                session.get("https://www.dcard.tw/", timeout=15)
                time.sleep(random.uniform(1.0, 2.0))
            except Exception:
                pass
        return session

    def _get(self, url: str, params: dict = None, headers: dict = None,
             timeout: int = 20, force_direct: bool = False):
        """
        統一 GET 請求入口，依 _BYPASS_MODE 選擇路由：
          - scraperapi：透過 ScraperAPI endpoint 轉發（HTML 頁用）
          - proxy：session 已設定 proxy，直接發送
          - direct：直連
        force_direct=True 時強制直連（JSON API endpoint 適用）
        """
        if _BYPASS_MODE == "scraperapi" and not force_direct:
            # ScraperAPI 把目標 URL 當參數傳入
            target = url
            if params:
                target += ("&" if "?" in target else "?") + urllib.parse.urlencode(params)
            api_params = {
                "api_key": _SCRAPERAPI_KEY,
                "url":     target,
                "render":  "false",   # Dcard SSR 不需要 JS 執行
            }
            return self.session.get(
                SCRAPERAPI_ENDPOINT, params=api_params,
                headers=headers, timeout=60   # ScraperAPI 需要較長 timeout
            )
        else:
            return self.session.get(
                url, params=params, headers=headers, timeout=timeout
            )

    def _delay(self):
        time.sleep(random.uniform(*REQUEST_DELAY))

    def _scraperapi_get(self, target_url: str, timeout: int = 60):
        """
        透過 ScraperAPI 發送 GET 請求，使用裸 requests（不帶 curl_cffi browser headers）。
        ScraperAPI 本身的 API endpoint 不需要 browser fingerprint，
        curl_cffi session 的 TLS 指紋反而會導致 ScraperAPI 回傳 403。
        """
        import requests as _req
        return _req.get(
            SCRAPERAPI_ENDPOINT,
            params={"api_key": _SCRAPERAPI_KEY, "url": target_url, "render": "false"},
            timeout=timeout,
        )

    # ════════════════════════════════════════════════════════════
    # 策略一：全站搜尋 SSR
    # ════════════════════════════════════════════════════════════

    def _parse_article(self, art) -> Optional[Dict]:
        """
        解析 SSR <article>：
          URL          → <a href="/f/{forum}/p/{post_id}">
          title        → <h2>/<h3>
          forum        → <a href="/f/{forum}">（有文字）
          date         → <time datetime="ISO8601">
          excerpt      → h2 後第一段非空文字
          like_count   → button[0] 純數字
          comment_count→ button[1] 純數字
        """
        post_link = art.find("a", href=re.compile(r"/p/\d+$"))
        if not post_link:
            return None
        href = post_link["href"]
        # 過濾掉 SSR 頁面中誤抓的 Google News 連結（不是 /f/.../p/數字 格式的都排除）
        if not re.match(r"^/f/[^/]+/p/\d+$", href):
            return None
        post_url = "https://www.dcard.tw" + href
        post_id  = href.split("/p/")[-1]

        forum = "Dcard"
        for a in art.find_all("a", href=re.compile(r"^/f/[^/]+$")):
            text = a.get_text(strip=True)
            if text and text not in ("查看全文",):
                forum = text
                break

        heading = art.find(["h1", "h2", "h3"])
        title   = heading.get_text(strip=True) if heading else ""
        if not title:
            return None

        date_str = ""
        time_el  = art.find("time", attrs={"datetime": True})
        if time_el:
            date_str = time_el["datetime"]

        excerpt = ""
        if heading:
            for sib in heading.next_siblings:
                if hasattr(sib, "get_text"):
                    text = sib.get_text(strip=True)
                    if text:
                        excerpt = text
                        break

        def _btn_num(btn) -> int:
            t = btn.get_text(strip=True)
            return int(t) if t.isdigit() else 0

        buttons       = art.find_all("button")
        like_count    = _btn_num(buttons[0]) if len(buttons) >= 1 else 0
        comment_count = _btn_num(buttons[1]) if len(buttons) >= 2 else 0

        return {
            "id": post_id, "title": title, "excerpt": excerpt,
            "forum": forum, "post_url": post_url, "date_str": date_str,
            "like_count": like_count, "comment_count": comment_count,
        }

    def _fetch_search_term(self, term: str, limit: int) -> List[Dict]:
        """搜尋單一關鍵詞的 SSR 頁面。limit 參數保留相容性，實際不截斷（全部解析）。"""
        try:
            self._delay()
            resp = self._get(DCARD_SEARCH_URL, params={"query": term})
            if resp.status_code != 200:
                print(f"  [Dcard/search] 「{term}」→ {resp.status_code}")
                return []

            soup     = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all("article")
            if not articles:
                print(f"  [Dcard/search] 「{term}」→ 無 <article>（WAF 封鎖？）")
                return []

            results = []
            for art in articles:          # ← 不再截斷，全部解析
                parsed = self._parse_article(art)
                if not parsed:
                    continue
                # 全站搜尋不限版別：高互動的工作板/其他版討論也要納入
                if is_brand_relevant(self.keyword, parsed["title"], parsed["excerpt"]):
                    results.append(parsed)
            return results
        except Exception as e:
            print(f"  [Dcard/search] 「{term}」失敗：{e}")
            return []

    # ════════════════════════════════════════════════════════════
    # 日期解析
    # ════════════════════════════════════════════════════════════

    def _parse_date(self, date_str: str) -> str:
        """支援 ISO 8601（2026-03-25T06:45:40.954Z）。"""
        try:
            return datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    def _fetch_post_content(self, post_id: str) -> Dict:
        """
        透過 Dcard API v2 取得文章全文。
        直連會被 Cloudflare 403，固定走 ScraperAPI（若無 key 則回傳空）。
        回傳：{ content, like_count, comment_count, forum, title, date_str }
        """
        if not _SCRAPERAPI_KEY:
            return {}
        api_url = f"https://www.dcard.tw/service/api/v2/posts/{post_id}"
        try:
            self._delay()
            resp = self._scraperapi_get(api_url)
            if resp.status_code != 200:
                return {}
            d = resp.json()
            return {
                "content":       (d.get("content") or "").strip(),
                "like_count":    d.get("likeCount", 0),
                "comment_count": d.get("commentCount", 0),
                "forum":         d.get("forumName", ""),
                "title":         d.get("title", ""),
                "date_str":      d.get("createdAt", ""),
            }
        except Exception as e:
            print(f"  [Dcard/API] post {post_id} 失敗：{e}")
            return {}

    def _is_recent(self, date_str: str, max_days: int = 30) -> bool:
        """
        Dcard SSR 搜尋結果可能包含 2017–2022 的舊文。
        只保留 max_days 天內的文章（依 ISO 8601 timestamp 判斷）。
        date_str 為空時寬鬆放行（假設是近期文章）。
        """
        if not date_str:
            return True
        try:
            from datetime import timezone, timedelta
            pub = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
            cutoff = datetime.utcnow() - timedelta(days=max_days)
            return pub >= cutoff
        except Exception:
            return True

    # ════════════════════════════════════════════════════════════
    # 策略二：版板置頂貼文 API（公開端點，免登入）
    # ════════════════════════════════════════════════════════════

    # 固定採集的版板（超商專版 + 美食版）
    FORUM_API_BOARDS = ["cvs", "food"]

    def _fetch_pinned_posts(self, forum_id: str) -> List[Dict]:
        """
        取得版板置頂貼文（pinnedPosts）。
        端點：GET /service/api/v2/forums/{forum_id}/pinnedPosts
        ✅ 公開端點，無需登入，ScraperAPI render=false 可正常回傳。
        置頂貼文通常是版板最具代表性、討論最熱烈的文章（如：員工勞資、服務投訴等）。
        """
        api_url = f"https://www.dcard.tw/service/api/v2/forums/{forum_id}/pinnedPosts"
        try:
            self._delay()
            if _SCRAPERAPI_KEY:
                resp = self._scraperapi_get(api_url)
            else:
                resp = self.session.get(api_url, timeout=20)

            if resp.status_code != 200:
                print(f"  [Dcard/pinned/{forum_id}] {resp.status_code}")
                return []

            posts_json = resp.json()
            if not isinstance(posts_json, list):
                return []

            results = []
            for p in posts_json:
                post_id  = str(p.get("id", ""))
                title    = (p.get("title") or "").strip()
                forum    = p.get("forumName") or forum_id
                date_str = p.get("createdAt", "")
                like_cnt = p.get("likeCount", 0)
                cmt_cnt  = p.get("commentCount", 0)
                excerpt  = (p.get("excerpt") or "").strip()
                if not post_id or not title:
                    continue
                post_url = f"https://www.dcard.tw/f/{forum_id}/p/{post_id}"
                results.append({
                    "id":            post_id,
                    "title":         title,
                    "excerpt":       excerpt,
                    "forum":         forum,
                    "post_url":      post_url,
                    "date_str":      date_str,
                    "like_count":    like_cnt,
                    "comment_count": cmt_cnt,
                    "is_pinned":     True,
                })
            print(f"  [Dcard/pinned/{forum_id}] 取得 {len(results)} 篇置頂")
            return results
        except Exception as e:
            print(f"  [Dcard/pinned/{forum_id}] 失敗：{e}")
            return []

    def _fetch_forum_posts(self, forum_id: str, limit: int = 20) -> List[Dict]:
        """
        透過 Dcard API v2 取得指定版板的最新貼文列表。
        端點：GET /service/api/v2/forums/{forum_id}/posts?limit=N
        回傳 metadata（含 post_id），不含全文（全文由 _fetch_post_content 補齊）。
        需要 ScraperAPI 繞過 WAF；無 key 時直連（本機可用，雲端會 403）。
        """
        api_url = f"https://www.dcard.tw/service/api/v2/forums/{forum_id}/posts"
        try:
            self._delay()
            if _SCRAPERAPI_KEY:
                resp = self.session.get(
                    SCRAPERAPI_ENDPOINT,
                    params={
                        "api_key": _SCRAPERAPI_KEY,
                        "url":     api_url + f"?limit={limit}",
                        "render":  "false",
                    },
                    timeout=60,
                )
            else:
                resp = self.session.get(api_url, params={"limit": limit}, timeout=20)

            if resp.status_code != 200:
                print(f"  [Dcard/forum/{forum_id}] API {resp.status_code}")
                return []

            posts_json = resp.json()
            if not isinstance(posts_json, list):
                print(f"  [Dcard/forum/{forum_id}] 非預期回應格式")
                return []

            results = []
            for p in posts_json:
                post_id  = str(p.get("id", ""))
                title    = (p.get("title") or "").strip()
                forum    = p.get("forumName") or forum_id
                date_str = p.get("createdAt", "")
                like_cnt = p.get("likeCount", 0)
                cmt_cnt  = p.get("commentCount", 0)
                if not post_id or not title:
                    continue
                post_url = f"https://www.dcard.tw/f/{forum_id}/p/{post_id}"
                results.append({
                    "id":            post_id,
                    "title":         title,
                    "excerpt":       (p.get("excerpt") or "").strip(),
                    "forum":         forum,
                    "post_url":      post_url,
                    "date_str":      date_str,
                    "like_count":    like_cnt,
                    "comment_count": cmt_cnt,
                })
            print(f"  [Dcard/forum/{forum_id}] 取得 {len(results)} 篇")
            return results
        except Exception as e:
            print(f"  [Dcard/forum/{forum_id}] 失敗：{e}")
            return []

    # ════════════════════════════════════════════════════════════
    # 主入口
    # ════════════════════════════════════════════════════════════

    def fetch_latest_posts(self, limit: int = 15, fresh_mode: bool = False) -> List[Dict]:
        terms_str = " / ".join(self.search_terms)
        print(f"  Fetching Dcard posts for keyword: {self.keyword}（搜尋詞：{terms_str}）...")

        seen_urls: set = set()
        raw_posts: List[Dict] = []

        # ── 策略一：版板置頂貼文（CVS + food，公開 API）──────────
        # pinnedPosts 端點免登入，ScraperAPI 可直接存取。
        # 置頂文是版板內最具代表性的討論，如員工勞資、品牌比較等。
        from src.config.brands import get_brand_config
        brand_cfg   = get_brand_config(self.keyword)
        exclude_kws = brand_cfg.get("exclude_keywords", [])

        for forum_id in self.FORUM_API_BOARDS:
            pinned = self._fetch_pinned_posts(forum_id)
            added  = 0
            for p in pinned:
                if p["post_url"] in seen_urls:
                    continue
                combined = (p["title"] + " " + p["excerpt"]).lower()
                if any(ex.lower() in combined for ex in exclude_kws):
                    continue
                # 置頂貼文不過期過濾（可能是幾個月前釘選的長期討論）
                seen_urls.add(p["post_url"])
                raw_posts.append(p)
                added += 1
            if added:
                print(f"  [Dcard/pinned/{forum_id}] 新增 {added} 篇置頂")

        # ── 策略二：全站搜尋 SSR（每個搜尋詞，不限版別）────────
        # 注意：版板列表 API（/service/api/v2/forums/{id}/posts）需要登入 cookie，
        # ScraperAPI 無法繞過（回傳 403）。全站搜尋 SSR 是唯一可用的公開路徑。
        # 版白名單已移除 → 任何版（工作板、美食板等）的高互動貼文都會納入。
        for term in self.search_terms:
            posts = self._fetch_search_term(term, limit * 2)
            added = 0
            skipped_old = 0
            for p in posts:
                if p["post_url"] not in seen_urls:
                    if not self._is_recent(p["date_str"], max_days=90):
                        skipped_old += 1
                        continue
                    seen_urls.add(p["post_url"])
                    raw_posts.append(p)
                    added += 1
            msg = f"  [Dcard/search/'{term}'] 新增 {added} 篇"
            if skipped_old:
                msg += f"（過濾舊文 {skipped_old} 篇）"
            print(msg)

        if not raw_posts:
            print("  → Dcard: 0 篇完成（WAF 封鎖或無結果）\n")
            return []

        print(f"  [Dcard] 合計 {len(raw_posts)} 篇（去重後）")

        articles = []
        for post in raw_posts:
            if len(articles) >= limit:
                break

            link    = post["post_url"]
            post_id = post["id"]

            if not fresh_mode and self.db and self.db.is_duplicate(link):
                print(f"  [Dcard] 跳過重複：{post['title'][:30]}")
                continue

            # ── 用 Dcard API 取全文（ScraperAPI 代理）──────────────
            api_data    = self._fetch_post_content(post_id)
            title       = api_data.get("title") or post["title"]
            forum       = api_data.get("forum") or post["forum"]
            raw_content = api_data.get("content") or post["excerpt"] or ""
            date_str    = api_data.get("date_str") or post["date_str"]
            like_cnt    = api_data.get("like_count") or post["like_count"]
            comment_cnt = api_data.get("comment_count") or post["comment_count"]
            published   = self._parse_date(date_str)

            # 過濾純圖片貼文：去除 URL 行後，純文字少於 50 字視為無情緒分析價值
            text_lines = [
                l for l in raw_content.split("\n")
                if l.strip() and not l.strip().startswith("http")
            ]
            text_content = " ".join(text_lines).strip()
            if len(text_content) < 50:
                print(f"  [Dcard] ⏭ 略過（純圖/無文字）：{title[:40]}")
                continue
            content = raw_content   # 保留原始內容給 AI（含圖 URL 不影響）

            print(f"  [Dcard] ✅ 儲存：{title[:45]}（文字 {len(text_content)} chars）")

            article = {
                "title":         title,
                "link":          link,
                "source":        f"Dcard/{forum}",
                "published":     published,
                "content":       content,
                "channel":       self.CHANNEL,
                "keyword":       self.keyword,
                "push_count":    like_cnt,
                "boo_count":     0,
                "neutral_count": 0,
                "comment_count": comment_cnt,
                "push_items":    [],
            }

            if self.db:
                thread_id = self.db.save_thread(
                    url           = link,
                    source_name   = self.SOURCE_NAME,
                    channel       = self.CHANNEL,
                    title         = title,
                    board         = forum,
                    keyword       = self.keyword,
                    published_at  = published,
                    push_count    = like_cnt,
                    boo_count     = 0,
                    neutral_count = 0,
                    comment_count = comment_cnt,
                )
                if content:
                    self.db.save_thread_item(thread_id, content, item_type="main")

            articles.append(article)

        print(f"  → Dcard: {len(articles)} 篇完成\n")
        return articles
