"""
PTTCollector — PTT 渠道採集器 v4（版板調整）

版板選擇原則：只保留有真實消費者情緒討論的版。
  CVS          — 超商專版：商品心得、討論、投訴，推/噓是直接情緒指標
  Gossiping    — 八卦版：病毒式話題擴散地，品牌危機往往由此引爆（雲端環境可正常連線）
  Consumer     — 消費者版：投訴、維權、服務問題
  SocialHealth — 心情/社會觀察：真實消費體驗吐槽、品牌重大事件討論（如併購新聞）
  WomenTalk    — 女性討論區：服務政策直接評論（自助結帳、價格情緒等）

移除原因（全站搜尋實測後確認）：
  Lifeismoney — 優惠情報轉貼，100% 正面，無情緒分析價值
  e-coupon    — 禮券交易，與品牌情緒無關
  Food        — 「全家人牛排」「全家福元宵」語意模糊，大量假陽性
  Hate        — 「全家」常被誤用為罵人語（死全家），大量假陽性
  brand       — 「全家聖誕禮物」等「全家＝whole family」誤判
  part-time   — 超商取貨/寄貨物流操作，零情緒價值
  creditcard  — 超商只是信用卡回饋的消費地點，非品牌討論

渠道識別：channel = "ptt"
"""

import time
import random
import re
import requests
import urllib3
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from datetime import datetime

from src.utils.content_extractor import ContentExtractor
from src.utils.db_manager import SentimentDB
from src.config.brands import (
    get_search_terms,
    is_direct_brand_match,
    is_generic_crisis_match,
    is_relevant_with_two_stage_attribution,
)

# 關閉 InsecureRequestWarning（PTT SSL 較舊）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PTT_BASE   = "https://www.ptt.cc"
MAX_PUSH_ITEMS = 30
MAX_CONTENT    = 1500
MAX_RETRIES    = 3

# 目標版板：只保留有真實消費者情緒的版
#
# CVS          — 超商專版：商品心得、投訴，推/噓是直接情緒指標
# Gossiping    — 八卦版：品牌危機擴散地（併購、勞資、食安）
# Consumer     — 消費者版：投訴、維權、服務問題
# SocialHealth — 心情/社會觀察：真實消費體驗吐槽、品牌重大事件討論
# WomenTalk    — 女性討論區：服務政策評論（自助結帳、價格情緒）
#
# 已確認移除：
#   Lifeismoney — 優惠情報轉貼，100% 正面，無情緒分析價值
#   e-coupon    — 禮券交易，與品牌情緒無關
#   Food        — 「全家人牛排」「全家福元宵」大量誤判
#   Hate        — 「全家」常被誤用為罵人語，大量假陽性
TARGET_BOARDS = ["CVS", "Gossiping", "Consumer", "SocialHealth", "WomenTalk"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


class PTTCollector:
    CHANNEL     = "ptt"
    SOURCE_NAME = "PTT"

    def __init__(self, keyword: str, db: Optional[SentimentDB] = None):
        self.keyword      = keyword
        self.db           = db
        self.extractor    = ContentExtractor()
        self.search_terms = get_search_terms(keyword, "ptt")
        self.session      = self._init_session()

    # ── Session 初始化 ───────────────────────────────────────────
    def _init_session(self) -> requests.Session:
        """建立帶有 over18 Cookie 的 Session，先訪問主頁暖機。"""
        session = requests.Session()
        session.headers.update(HEADERS)
        session.cookies.set("over18", "1", domain="www.ptt.cc")

        for attempt in range(MAX_RETRIES):
            try:
                session.get(PTT_BASE + "/bbs/index.html", timeout=10, verify=False)
                break
            except Exception:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
        return session

    # ── 重試 GET（404 立即回傳 None）────────────────────────────
    def _get_with_retry(self, url: str, timeout: int = 12) -> Optional[requests.Response]:
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(url, timeout=timeout, verify=False)
                if resp.status_code == 404:
                    print(f"  [PTT] 404 Not Found，跳過：{url}")
                    return None
                resp.raise_for_status()
                return resp
            except requests.exceptions.HTTPError:
                return None
            except requests.exceptions.ConnectionError as e:
                wait = 2 ** attempt
                if attempt < MAX_RETRIES - 1:
                    print(f"  [PTT] 連線失敗（attempt {attempt+1}），{wait}s 後重試：{e}")
                    time.sleep(wait)
                    self.session = self._init_session()
                else:
                    print(f"  [PTT] 連線持續失敗，放棄：{e}")
                    return None
            except Exception as e:
                print(f"  [PTT] 請求失敗：{e}")
                return None
        return None

    # ── 單版板 × 單搜尋詞 ────────────────────────────────────────
    def _search_board_term(self, board: str, term: str, limit: int) -> List[Dict]:
        """對單一版板用單一關鍵詞搜尋（PTT 不支援 OR 語法）。"""
        url = (
            f"{PTT_BASE}/bbs/{board}/search"
            f"?q={requests.utils.quote(term)}"
        )
        resp = self._get_with_retry(url)
        if not resp:
            return []

        try:
            soup    = BeautifulSoup(resp.text, "html.parser")
            results = []
            for item in soup.select(".r-ent")[:limit]:
                title_el  = item.select_one(".title a")
                if not title_el:
                    continue
                meta_date = item.select_one(".meta .date")
                title     = title_el.text.strip()

                if not (
                    is_direct_brand_match(self.keyword, title)
                    or is_generic_crisis_match(title)
                ):
                    continue

                results.append({
                    "title":    title,
                    "link":     PTT_BASE + title_el["href"],
                    "date_str": meta_date.text.strip() if meta_date else "",
                    "board":    board,
                })
            return results
        except Exception as e:
            print(f"  [PTT/{board}] 解析失敗：{e}")
            return []

    # ── 全版板 × 全搜尋詞（合併去重）────────────────────────────
    def _search_all_boards(self, limit: int) -> List[Dict]:
        """遍歷目標版板 × 每個搜尋詞，合併結果並依 URL 去重。"""
        seen_urls = set()
        merged    = []

        for board in TARGET_BOARDS:
            board_added = 0
            for term in self.search_terms:
                posts = self._search_board_term(board, term, limit)
                for post in posts:
                    if post["link"] not in seen_urls:
                        seen_urls.add(post["link"])
                        merged.append(post)
                        board_added += 1
                # 搜尋詞之間短延遲
                time.sleep(random.uniform(0.5, 1.2))

            print(f"  [PTT/{board}] 新增 {board_added} 篇（搜尋詞：{self.search_terms}）")
            # 版板間較長延遲
            time.sleep(random.uniform(1.0, 2.5))

        print(f"  [PTT] 全版板合計 {len(merged)} 篇（去重後）")
        return merged

    # ── 日期解析 ─────────────────────────────────────────────────
    def _parse_date(self, date_str: str) -> str:
        """
        PTT 首頁只顯示 M/DD（無年份）。
        推斷規則：
          - 解析月份 > 當前月份 → 去年的文章
          - 否則預設今年
        例：現在 2026/05，解析到 12/08 → 2025-12-08（而非 2026-12-08）
        """
        try:
            now = datetime.now()
            month, day = date_str.strip().split("/")
            m, d = int(month), int(day)
            year = now.year if m <= now.month else now.year - 1
            return f"{year}-{m:02d}-{d:02d} 00:00"
        except Exception:
            return datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── 從 URL 取得版板名 ────────────────────────────────────────
    def _get_board_from_url(self, url: str) -> str:
        try:
            return url.split("/bbs/")[1].split("/")[0]
        except Exception:
            return "PTT"

    # ── 單篇全文 + 推/噓解析 ─────────────────────────────────────
    def _fetch_post_detail(self, url: str) -> Dict:
        resp = self._get_with_retry(url)
        if not resp:
            return {"content": "", "push_items": [],
                    "push_count": 0, "boo_count": 0, "neutral_count": 0}
        try:
            soup     = BeautifulSoup(resp.text, "html.parser")
            main_div = soup.find("div", id="main-content")
            if not main_div:
                return {"content": "", "push_items": [],
                        "push_count": 0, "boo_count": 0, "neutral_count": 0}

            # ── 推/噓文解析 ──────────────────────────────────────
            push_items    = []
            push_count    = 0
            boo_count     = 0
            neutral_count = 0
            seq           = 0

            for push_tag in main_div.select(".push")[:MAX_PUSH_ITEMS]:
                tag_cls  = push_tag.select_one(".push-tag")
                uid_el   = push_tag.select_one(".push-userid")
                cont_el  = push_tag.select_one(".push-content")

                tag_text  = (tag_cls.text.strip()     if tag_cls  else "→")
                uid_text  = (uid_el.text.strip()      if uid_el   else "")
                cont_text = (cont_el.text.strip(" :") if cont_el  else "")

                if tag_text == "推":
                    item_type  = "push";    push_count    += 1
                elif tag_text == "噓":
                    item_type  = "boo";     boo_count     += 1
                else:
                    item_type  = "neutral"; neutral_count += 1

                if cont_text:
                    push_items.append({
                        "item_type": item_type,
                        "author":    uid_text,
                        "content":   cont_text,
                        "sequence":  seq,
                    })
                    seq += 1

            # ── 主文 ──────────────────────────────────────────────
            for tag in main_div.select(".push, #article-polling"):
                tag.decompose()

            raw_text = main_div.get_text(separator="\n")
            lines    = raw_text.splitlines()
            content_lines = []
            header_done   = False
            for line in lines:
                stripped = line.strip()
                if not header_done:
                    if stripped.startswith(("作者", "看板", "標題", "時間",
                                            "Author", "Board", "Title", "Date")):
                        continue
                    else:
                        header_done = True
                content_lines.append(line)

            content = re.sub(r'\n{3,}', '\n\n', "\n".join(content_lines)).strip()
            if len(content) > MAX_CONTENT:
                content = content[:MAX_CONTENT] + "⋯⋯（內容截斷）"

            return {
                "content":       content,
                "push_items":    push_items,
                "push_count":    push_count,
                "boo_count":     boo_count,
                "neutral_count": neutral_count,
            }
        except Exception as e:
            print(f"  [PTT] 解析文章失敗：{e}")
            return {"content": "", "push_items": [],
                    "push_count": 0, "boo_count": 0, "neutral_count": 0}

    # ── 主入口 ───────────────────────────────────────────────────
    def fetch_latest_posts(self, limit: int = 15, fresh_mode: bool = False) -> List[Dict]:
        terms_str = " / ".join(self.search_terms)
        print(f"  Fetching PTT posts for keyword: {self.keyword}（搜尋詞：{terms_str}）...")
        raw_posts = self._search_all_boards(limit * 2)
        existing_links = (
            self.db.get_existing_threads([post["link"] for post in raw_posts])
            if (not fresh_mode and self.db and raw_posts) else set()
        )

        articles = []
        skipped_dup = 0
        skipped_samples = []
        for post in raw_posts:
            if len(articles) >= limit:
                break

            if post["link"] in existing_links:
                skipped_dup += 1
                if len(skipped_samples) < 5:
                    skipped_samples.append(post["title"][:30])
                continue

            print(f"  [PTT] 提取：{post['title'][:50]}...")
            detail    = self._fetch_post_detail(post["link"])
            matched, reason = is_relevant_with_two_stage_attribution(
                self.keyword,
                post["title"],
                detail["content"],
            )
            if not matched:
                continue
            board     = self._get_board_from_url(post["link"])
            published = self._parse_date(post["date_str"])

            # comment_count 在 PTT 語境下 = 推文(push) + 噓文(boo) + →文(neutral) 總數
            # 即「所有互動回應」，與 Dcard 的「留言數」語意略有不同，但皆表示「討論熱度」
            interaction_count = (
                detail["push_count"] + detail["boo_count"] + detail["neutral_count"]
            )
            article = {
                "title":         post["title"],
                "link":          post["link"],
                "source":        f"PTT/{board}",
                "published":     published,
                "content":       detail["content"],
                "channel":       self.CHANNEL,
                "keyword":       self.keyword,
                "push_count":    detail["push_count"],
                "boo_count":     detail["boo_count"],
                "neutral_count": detail["neutral_count"],
                "comment_count": interaction_count,   # PTT: 互動總數（非純留言）
                "push_items":    detail["push_items"],
            }

            print(f"  [PTT] 歸因：{reason}")

            if self.db:
                thread_id = self.db.save_thread(
                    url          = post["link"],
                    source_name  = self.SOURCE_NAME,
                    channel      = self.CHANNEL,
                    title        = post["title"],
                    board        = board,
                    keyword      = self.keyword,
                    published_at = published,
                    push_count   = detail["push_count"],
                    boo_count    = detail["boo_count"],
                    neutral_count= detail["neutral_count"],
                    comment_count= article["comment_count"],
                )
                if detail["content"]:
                    self.db.save_thread_item(thread_id, detail["content"], item_type="main")
                self.db.save_thread_items_bulk(thread_id, detail["push_items"])

            articles.append(article)

            # 文章間短暫延遲
            time.sleep(random.uniform(0.5, 1.2))

        if skipped_dup > 0:
            sample_text = "；".join(skipped_samples)
            print(f"  [PTT] 跳過重複 {skipped_dup} 篇" + (f"（例：{sample_text}）" if sample_text else ""))

        print(f"  → PTT: {len(articles)} 篇完成\n")
        return articles
