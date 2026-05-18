"""
src/analyzers/sentiment_analyzer.py — 情感分析器

透過 ModelRouter 路由到對應的 LLM provider。
保留原始 prompt 邏輯，新增 should_analyze() 智慧篩選。
"""
import json
import re
import os
from typing import Dict

from dotenv import load_dotenv
load_dotenv()

# 負面關鍵字清單（用於 should_analyze 篩選）
NEGATIVE_KEYWORDS = [
    "食安", "食品安全", "違規", "罰款", "缺貨", "詐騙", "抗議", "投訴",
    "問題", "事故", "醜聞", "爭議", "不滿", "批評", "下架", "召回",
    "汙染", "過期", "造假", "客訴", "糾紛", "賠償", "索賠", "罷工",
    "倒閉", "裁員", "停業", "違法", "起訴", "判決", "敗訴", "懲處",
    "吐槽", "爛", "差評", "負評", "崩潰", "失職", "疏失",
]

# 品牌關鍵字（命中表示文章跟目標品牌有關）
BRAND_KEYWORDS = [
    "7-ELEVEN", "7eleven", "7-11", "統一超商", "統一",
    "全家", "FamilyMart", "family mart",
    "萊爾富", "Hi-Life", "hilife",
    "OK mart", "OK超商", "OKmart",
]


class SentimentAnalyzer:
    SYSTEM_PROMPT = (
        "你是一位專業的媒體公關與輿論分析師，擅長台灣便利超商產業的輿情判讀。"
    )

    def __init__(self, router=None):
        """
        Args:
            router: ModelRouter 實例（None 時自動建立）
        """
        if router is None:
            from src.ai.router import ModelRouter
            router = ModelRouter()
        self._router = router

    # ── 智慧篩選 ─────────────────────────────────────────────
    def should_analyze(self, article: Dict) -> bool:
        """
        判斷文章是否值得送 LLM 分析。

        評分邏輯：
        1. 互動數高（push、comment）→ 加分
        2. 命中負面關鍵字 → 加分（優先分析）
        3. 品牌命中程度 → 未命中則直接跳過

        Returns:
            True  → 送 LLM 分析
            False → 跳過（中立/無關）
        """
        title = article.get("title", "")
        content = article.get("content", "")
        text = (title + " " + content).lower()

        # 品牌命中（必要條件）
        brand_hit = any(kw.lower() in text for kw in BRAND_KEYWORDS)
        # 文章標題中的品牌（直接關聯）
        title_brand_hit = any(kw.lower() in title.lower() for kw in BRAND_KEYWORDS)

        # 若文章完全沒提到任何品牌，跳過
        if not brand_hit:
            return False

        score = 0

        # 標題直接提到品牌 → +2
        if title_brand_hit:
            score += 2

        # 負面關鍵字 → 每命中 +1（最高 +3）
        neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
        score += min(neg_hits, 3)

        # 互動數
        push = article.get("push_count") or 0
        boo = article.get("boo_count") or 0
        comments = article.get("comment_count") or 0
        interaction = push + boo + comments

        if interaction >= 100:
            score += 3
        elif interaction >= 30:
            score += 2
        elif interaction >= 10:
            score += 1

        # 閾值：score >= 1 才分析
        return score >= 1

    # ── 主分析流程 ────────────────────────────────────────────
    def analyze(self, title: str, content: str = "") -> Dict:
        """
        分析文章的情感傾向。優先使用全文內容，若無則退回標題分析。
        """
        if content and len(content) > 50:
            analysis_text = f"標題：{title}\n\n內文：{content}"
            context_label = "標題 + 全文"
        else:
            analysis_text = f"標題：{title}"
            context_label = "標題"

        user_prompt = (
            "你是一位專業的品牌公關顧問，專門評估台灣便利超商的輿情公關風險。\n\n"
            "【核心任務】評估這篇文章對品牌造成的「公關風險等級」，不是評估情緒強烈程度。\n\n"
            "【公關風險等級定義】score 為 1～5 的整數：\n"
            "  5 🔥 公關危機：食安事故、重大醜聞、媒體大規模報導、可能引發政府介入\n"
            "     範例：食物驗出異物/活蟲、大規模食物中毒、財務造假\n"
            "  4 🚨 嚴重警示：明顯傷害品牌形象、多人共鳴、有擴散潛力\n"
            "     範例：服務態度系統性問題、食品衛生疑慮、熱門負評引起廣泛討論\n"
            "  3 ⚠️ 中度關注：單一負面事件、值得追蹤但尚未擴散\n"
            "     範例：個別門市投訴、產品品質抱怨（已有多人附議）\n"
            "  2 📋 低度留意：一般消費心得、輕微不滿、商品評價\n"
            "     範例：PTT [商品] 板評價、個人口味偏好、員工心情抱怨\n"
            "  1 ✅ 無風險：正面新聞、促銷活動、企業公告、中立報導\n"
            "     範例：新品上市、聯名活動、財報良好\n\n"
            "【重要判斷原則】\n"
            "  - PTT [商品] 板的商品評價 → 最高 2 分（個人消費心得，不是品牌危機）\n"
            "  - 員工求職/離職心情文 → 最高 2 分（不影響消費者觀感）\n"
            "  - 食安/異物/衛生問題 → 至少 4 分\n"
            "  - 只有一人抱怨且無共鳴 → 最高 3 分\n"
            "  - 媒體新聞報導的負面事件 → 比同等 PTT 文章高 1 分\n\n"
            "【情緒分類】sentiment（三選一）：正面 / 中立 / 負面\n"
            "  注意：score 高不代表 sentiment 一定是負面（例如正面新聞 score=1, sentiment=正面）\n\n"
            "請只回覆 JSON，不含任何其他文字：\n"
            '{"sentiment": "...", "score": 整數1-5, "theme": "5字內主題", "reason": "一句話說明公關風險判斷依據", "voice_source": "主要聲量來源"}\n\n'
            f"{analysis_text}"
        )

        try:
            raw = self._router.complete_with_fallback(
                task="sentiment",
                system=self.SYSTEM_PROMPT,
                user=user_prompt,
            )
            provider = self._router.get("sentiment")
            result = self._extract_json(raw)
            if "theme" in result:
                result["theme"] = result["theme"].strip().upper()
            result["analyzed_with"] = context_label
            result["model_used"] = provider.name()
            # 確保必要欄位存在
            for field in ("sentiment", "score", "theme", "reason", "voice_source"):
                if field not in result:
                    result[field] = "未知" if field != "score" else 0
            return result
        except Exception as e:
            print(f"  Analysis error: {e}")
            return {
                "sentiment": "未知", "score": 0,
                "theme": "分析失敗", "reason": str(e),
                "voice_source": "未知", "analyzed_with": context_label,
                "model_used": "error",
            }

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """
        從 LLM 回應中穩健地擷取 JSON 物件。

        策略：
          1. 直接 json.loads（LLM 只回傳 JSON 時最快）
          2. 計算大括號深度定位最外層 {...}
        """
        text = raw.strip()

        # 策略一：直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 策略二：找最外層 { ... }（計算括號深度）
        start = text.find('{')
        if start == -1:
            raise ValueError(f"回應中找不到 JSON 物件：{text[:200]}")

        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])

        raise ValueError(f"JSON 括號不對稱，無法解析：{text[:200]}")
