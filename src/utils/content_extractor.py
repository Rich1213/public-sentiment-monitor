"""
ContentExtractor — 自適應全文提取模組

提取策略（依序降級）：
  Tier 1: trafilatura + requests   (最佳，90%+ 新聞網站)
  Tier 2: BeautifulSoup html.parser (純 Python，無二進位依賴)
  PTT   : requests 專屬路徑

注意：不使用 vendor 目錄，請在本機執行：
  pip install -r requirements.txt
  playwright install chromium
"""

import re
import requests
from urllib.parse import urlparse

MAX_CONTENT = 1500
MIN_VALID   = 150
TIMEOUT     = 12

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ContentExtractor:

    def extract(self, url: str) -> str:
        domain = urlparse(url).netloc.lower()

        if "ptt.cc" in domain:
            return self._extract_ptt(url)

        return self._extract_news(url)

    # ── HTML 下載（共用）─────────────────────────────────
    def _download_html(self, url: str) -> str:
        try:
            resp = requests.get(
                url, headers=HEADERS, timeout=TIMEOUT,
                verify=False, allow_redirects=True
            )
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            print(f"  [Extractor] 下載失敗：{type(e).__name__}")
            return ""

    # ── Tier 1: trafilatura ──────────────────────────────
    def _tier1_trafilatura(self, html: str, url: str) -> str:
        try:
            import trafilatura
            text = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            ) or ""
            return text.strip()
        except ImportError:
            print(f"  [Tier1] trafilatura 未安裝，跳過")
            return ""
        except Exception as e:
            print(f"  [Tier1/trafilatura] {type(e).__name__}: {str(e)[:60]}")
            return ""

    # ── Tier 2: BeautifulSoup (純 Python，無 lxml 依賴) ──
    def _tier2_beautifulsoup(self, html: str) -> str:
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")   # 不用 lxml

            # 移除干擾元素
            for tag in soup(["script", "style", "nav", "header",
                              "footer", "aside", "form", "iframe",
                              "figure", ".ad", ".advertisement"]):
                tag.decompose()

            # 優先嘗試語意標籤
            for selector in ["article", "main", "[role='main']",
                              ".article-content", ".post-content",
                              ".story", ".content", "#content"]:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(separator="\n")
                    if len(text.strip()) >= MIN_VALID:
                        return self._clean(text)

            # 退回：收集所有 <p> 標籤
            paras = [p.get_text().strip()
                     for p in soup.find_all("p")
                     if len(p.get_text().strip()) > 30]
            return self._clean("\n".join(paras))

        except ImportError:
            print(f"  [Tier2] beautifulsoup4 未安裝")
            return ""
        except Exception as e:
            print(f"  [Tier2/bs4] {type(e).__name__}: {str(e)[:60]}")
            return ""

    # ── 主流程 ───────────────────────────────────────────
    def _extract_news(self, url: str) -> str:
        html = self._download_html(url)
        if not html:
            print(f"  [Extractor] ❌ 無法下載頁面")
            return ""

        # Tier 1
        text = self._tier1_trafilatura(html, url)
        if len(text) >= MIN_VALID:
            print(f"  [Extractor] ✅ Tier1/trafilatura ({len(text)} 字)")
            return self._truncate(text)

        # Tier 2
        print(f"  [Extractor] Tier1 不足，嘗試 Tier2/bs4...")
        text = self._tier2_beautifulsoup(html)
        if len(text) >= MIN_VALID:
            print(f"  [Extractor] ✅ Tier2/bs4 ({len(text)} 字)")
            return self._truncate(text)

        print(f"  [Extractor] ❌ 提取失敗，退回標題分析")
        return ""

    # ── PTT 專屬 ─────────────────────────────────────────
    def _extract_ptt(self, url: str) -> str:
        try:
            from bs4 import BeautifulSoup
            resp = requests.get(
                url, headers=HEADERS, timeout=TIMEOUT,
                cookies={"over18": "1"}
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            content_div = soup.find("div", id="main-content")
            if not content_div:
                return ""
            for tag in content_div.select(".push, #article-polling"):
                tag.decompose()
            text = self._clean(content_div.get_text(separator="\n"))
            print(f"  [Extractor] ✅ PTT ({len(text)} 字)")
            return self._truncate(text)
        except Exception as e:
            print(f"  [Extractor/PTT] {e}")
            return ""

    # ── 工具 ─────────────────────────────────────────────
    def _clean(self, text: str) -> str:
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _truncate(self, text: str) -> str:
        if len(text) > MAX_CONTENT:
            return text[:MAX_CONTENT] + "⋯⋯（內容截斷）"
        return text
