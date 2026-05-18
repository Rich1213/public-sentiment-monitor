"""
dashboard_narrator.py — Dashboard 用短版論述生成器

策略：
  1. 優先用 LLM 按固定框架生成 1-2 句摘要
  2. 若 LLM 失敗，退回規則式摘要
"""

from collections import Counter
from typing import Dict, List, Optional


CHANNEL_LABELS = {
    "google_news": "Google News",
    "ptt": "PTT",
    "dcard": "Dcard",
    "youtube": "YouTube",
}


class DashboardNarrator:
    SYSTEM_PROMPT = (
        "你是品牌公關總監的 dashboard 摘要撰寫助手。"
        "只根據提供的事實輸出 1 句繁體中文，不可腦補未證實歸因。"
    )

    def __init__(self, router=None):
        self._router = router

    def summarize(self, keyword: str, articles: List[Dict], analyses: List[Dict]) -> str:
        try:
            if self._router is None:
                from src.ai.router import ModelRouter
                self._router = ModelRouter()
            prompt = self._build_prompt(keyword, articles, analyses)
            return self._router.complete_with_fallback(
                task="pr",
                system=self.SYSTEM_PROMPT,
                user=prompt,
                max_tokens=220,
            ).strip()
        except Exception:
            return self._fallback_summary(keyword, articles, analyses)

    def _build_prompt(self, keyword: str, articles: List[Dict], analyses: List[Dict]) -> str:
        top_items = self._pick_focus_items(articles, analyses)
        item_lines = []
        for item in top_items[:5]:
            item_lines.append(
                f"- 渠道：{CHANNEL_LABELS.get(item.get('channel'), item.get('channel', '未知'))}"
                f"｜分數：{item.get('score', 0)}"
                f"｜主題：{item.get('theme', '—')}"
                f"｜標題：{item.get('title', '—')}"
            )

        return (
            f"主品牌：{keyword}\n"
            "請用以下框架輸出 1 句繁體中文，不要列點：\n"
            "今日最需要關注的事件是＿＿，主要出現在＿＿，已反映出＿＿風險，建議立即＿＿。\n\n"
            "規則：\n"
            "1. 只講一個主事件，不要拼接不相干事件。\n"
            "2. 只列 1-2 個主要渠道。\n"
            "3. 若資料顯示是食安/活蟲/異物，就優先聚焦該事件。\n"
            "4. 不可說未證實的品牌責任，只能說需要釐清是否涉及。\n\n"
            "資料：\n" + "\n".join(item_lines)
        )

    def _pick_focus_items(self, articles: List[Dict], analyses: List[Dict]) -> List[Dict]:
        merged = []
        for article, analysis in zip(articles, analyses):
            merged.append({
                "title": article.get("title", ""),
                "channel": article.get("channel", ""),
                "score": int(round(analysis.get("score", 0) or 0)),
                "theme": analysis.get("theme", ""),
                "sentiment": analysis.get("sentiment", ""),
            })
        merged.sort(key=lambda x: (x["score"], x["sentiment"] == "負面"), reverse=True)
        return merged

    def _fallback_summary(self, keyword: str, articles: List[Dict], analyses: List[Dict]) -> str:
        focus_items = [item for item in self._pick_focus_items(articles, analyses) if item["sentiment"] == "負面"]
        if not focus_items:
            return f"今日尚未觀察到 {keyword} 的明確高風險事件，建議持續追蹤主要渠道變化。"

        top_bucket = [item for item in focus_items if item["score"] >= 4]
        if not top_bucket:
            top_score = focus_items[0]["score"]
            top_bucket = [item for item in focus_items if item["score"] >= top_score]

        channel_counts = Counter(CHANNEL_LABELS.get(item["channel"], item["channel"]) for item in top_bucket)
        channels = [label for label, _ in channel_counts.most_common(2)]

        top_themes = []
        for item in top_bucket:
            theme = item.get("theme") or item.get("title")
            if theme and theme not in top_themes:
                top_themes.append(theme)
            if len(top_themes) >= 2:
                break

        event_text = "、".join(top_themes[:2]) if top_themes else "負面事件擴散"
        channel_text = "、".join(channels[:2]) if channels else "主要社群渠道"
        risk_text = "食安與品管風險擴散" if any("蟲" in t or "食安" in t or "異物" in t for t in top_themes) else "品牌聲譽風險升高"

        return (
            f"今日最需要關注的事件是{event_text}，主要出現在 {channel_text}，"
            f"已反映出{risk_text}，需要立即釐清是否直接涉及 {keyword} 門市、商品或供應鏈。"
        )
