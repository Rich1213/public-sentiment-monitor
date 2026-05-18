"""
src/analyzers/pr_advisor.py — 公關策略分析師

透過 ModelRouter 路由到對應的 LLM provider。
保留原始 prompt 邏輯（Track A / Track B）。
"""
import os
from typing import List, Dict
from collections import Counter

from dotenv import load_dotenv
load_dotenv()

from src.utils.score_utils import normalize_score, ALERT_THRESHOLD


class PRAdvisor:
    def __init__(self, router=None):
        """
        Args:
            router: ModelRouter 實例（None 時自動建立）
        """
        if router is None:
            from src.ai.router import ModelRouter
            router = ModelRouter()
        self._router = router

    def _build_context(self, keyword: str, articles: List[Dict], analyses: List[Dict]) -> str:
        """將所有文章與分析結果打包成結構化 context 給 LLM。"""
        lines = [f"監測關鍵字：{keyword}\n", "=== 輿情資料摘要 ===\n"]

        for i, (art, ana) in enumerate(zip(articles, analyses), 1):
            lines.append(f"[文章 {i}]")
            lines.append(f"標題：{art['title']}")
            lines.append(f"來源：{art['source']}｜時間：{art['published']}")
            lines.append(f"情緒：{ana.get('sentiment')}（強度 {ana.get('score')}）")
            lines.append(f"主題：{ana.get('theme')}")
            lines.append(f"聲量來源：{ana.get('voice_source', '不明')}")
            lines.append(f"分析依據：{ana.get('reason')}")
            content_preview = art.get('content', '')[:300]
            if content_preview:
                lines.append(f"內文摘要：{content_preview}⋯⋯")
            lines.append("")

        return "\n".join(lines)

    def _get_track_prompt(self, context: str, track: str) -> str:
        """根據軌道產生對應的 prompt。"""
        if track == "B":
            return f"""你是一位同時具備公關策略與數位行銷專業的資深顧問。
根據以下輿情資料，輿情整體偏向正面，請啟動【品牌資產進攻軌 Track B】。

{context}

請輸出以下格式的繁體中文戰略分析，直接輸出文字，不要用 markdown：

════════════════════════════════════════
📋 數據驅動：公關與輿情戰略儀表板
════════════════════════════════════════

【輿情極性與動能判定】
（整體狀態判定、擴散動能、戰略軌道宣告）

【議題解構與商業價值評估】
▪️ 討論核心：（正面評價的核心點是什麼）
▪️ 訊號類型：（UGC口碑 / 媒體背書 / KOL推薦 / 其他）
▪️ 潛在商業價值：（SEO潛力、B2B信任、媒體轉化等具體評估）

【數據驅動行動清單 (Actionable Strategy)】
🎯 戰略最高指導原則：（一句話定義核心策略方向）

① 內容資產化 (Content Asset)：
   （具體建議如何把這波討論轉化為官方內容資產）

② SEO 與流量承接 (SEO & Funnel)：
   （觀測到哪些熱詞，建議如何優化哪些頁面）

③ 社群互動與聲量放大 (Engagement)：
   （建議哪種角色、在哪個平台、以什麼方式介入）

④ 品牌資產長期佈局：
   （這波正面聲量如何轉化為長期品牌資產）
════════════════════════════════════════"""
        else:
            return f"""你是一位同時具備危機公關與輿情策略的資深顧問，擅長 SCCT 框架。
根據以下輿情資料，輿情整體偏向負面或混亂，請啟動【危機應對軌 Track A】。

{context}

請輸出以下格式的繁體中文戰略分析，直接輸出文字，不要用 markdown：

════════════════════════════════════════
📋 數據驅動：公關策略與危機應對分析
════════════════════════════════════════

【輿情健康度綜合判定】
（預警等級：Level 1-4、擴散動能、是否有水軍或異常操作跡象）

【危機屬性與定調】
▪️ 核心爭點：（一句話總結輿論的核心矛盾）
▪️ 危機類型：（SCCT 三型之一：受害型 / 意外型 / 可預防型，並說明判斷依據）
▪️ 利益關係人：（主要反彈者是誰，是真實用戶、媒體、競品還是網軍）

【傳播路徑解析 (Node Analysis)】
▪️ 引爆起點 (Where/Who)：（最初從哪裡、誰點燃的）
▪️ 放大節點 (Amplifier)：（誰或什麼平台讓它擴散）
▪️ 輿論風向 (What/Why)：（大眾目前聚焦的是哪個面向）

【行動策略與論述框架 (Actionable Strategy)】
🎯 戰略最高指導原則：（對應 SCCT 類型的核心精神）

① 止血期 (0-24hr)：
   - 溝通渠道：
   - 核心訊息 (Key Message)：（草擬一句話定調）
   - 禁止動作：

② 溝通期 (24-72hr)：
   - 具體行動：
   - 論述框架：

③ 修復期（長尾）：
   - 品牌信任重建策略：

🧨 【決策雷區 (Red Lines)】
（在這個特定情境下，絕對不能說的話或做的事，列出 2-3 點）
════════════════════════════════════════"""

    def _determine_track(self, analyses: List[Dict]) -> str:
        """根據分析結果決定走哪條軌道。"""
        sentiments = [a.get('sentiment', '未知') for a in analyses]
        counts = Counter(sentiments)
        avg_score = sum(normalize_score(a.get('score', 0)) for a in analyses) / max(len(analyses), 1)

        negative = counts.get('負面', 0)
        positive = counts.get('正面', 0)
        total = len(analyses)

        if negative > total / 2 or (negative >= 2 and avg_score >= ALERT_THRESHOLD):
            return "A"
        elif positive > total / 2:
            return "B"
        else:
            return "A" if negative >= positive else "B"

    def advise(self, keyword: str, articles: List[Dict], analyses: List[Dict]) -> str:
        """主入口：判斷軌道並輸出對應的戰略分析。"""
        track = self._determine_track(analyses)
        context = self._build_context(keyword, articles, analyses)
        prompt = self._get_track_prompt(context, track)

        print(f"\n  → 軌道判定：Track {'B（品牌進攻軌）' if track == 'B' else 'A（危機應對軌）'}")
        print("  → 正在生成公關戰略分析⋯⋯\n")

        try:
            result = self._router.complete_with_fallback(
                task="pr",
                system="你是一位資深公關策略顧問，精通台灣媒體生態與危機管理。",
                user=prompt,
                max_tokens=2500,
            )
            return result
        except Exception as e:
            return f"PR 分析生成失敗：{e}"
