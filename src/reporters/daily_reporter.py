"""
daily_reporter.py — 輿情日報資料聚合器 v2（四層框架）

四層輸出架構：
  Layer 1 — 資料品質儀表板  (DataQualityLayer)
    · 各渠道採集健康狀態（✅/⚠️/❌）
    · 可信度評分（媒體/論壇/社群三維覆蓋率）
    · 缺層警示（PTT/Dcard 掛掉時明確告警）

  Layer 2 — 三維情緒矩陣  (ThreeDimSentiment)
    · media  層：Google News（品牌敘事訊號，PR 稿傾向，正面偏高正常）
    · forum  層：PTT（真實民意，含推/噓比例）
    · social 層：Dcard（18–35 族群，消費情緒）

  Layer 3 — 競品橫向情報  (BrandSummary)
    · 四品牌並排比較，各層情緒對照

  Layer 4 — 品牌 PR 策略  (pr_track / pr_report)
    · Track A：危機應對  |  Track B：品牌進攻

渠道→層級對應：
  google_news, news  →  media
  ptt                →  forum
  dcard              →  social
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
from collections import Counter

from src.config.brands import CHANNEL_LAYER, CHANNEL_DISPLAY
from src.utils.score_utils import normalize_score, ALERT_THRESHOLD as DEFAULT_ALERT_THRESHOLD


# ─────────────────────────────────────────────────────────────────
# Layer 0 — 基礎文章摘要
# ─────────────────────────────────────────────────────────────────

@dataclass
class ArticleSummary:
    """單篇文章摘要（用於日報的重點文章區塊）"""
    title: str
    url: str
    source: str
    channel: str
    sentiment: str
    score: float
    theme: str
    reason: str
    published: str


# ─────────────────────────────────────────────────────────────────
# Layer 1 — 資料品質儀表板
# ─────────────────────────────────────────────────────────────────

@dataclass
class ChannelHealth:
    """單一渠道的採集健康狀態"""
    channel: str        # e.g. "ptt"
    label: str          # e.g. "PTT"
    layer: str          # "media" | "forum" | "social"
    count: int          # 今日採集篇數
    positive: int
    neutral: int
    negative: int
    avg_score: float
    ptt_push_count: int   # PTT 推文總數（非 PTT 渠道填 0）
    ptt_boo_count: int    # PTT 噓文總數
    push_ratio: float     # 推/(推+噓)，PTT 獨有情感信號
    boo_ratio: float      # 噓/(推+噓)
    status: str           # "ok" | "warn" | "down"
    status_reason: str    # 說明


@dataclass
class DataQualityLayer:
    """Layer 1：資料品質儀表板"""
    channels: List[ChannelHealth]
    has_media: bool
    has_forum: bool
    has_social: bool
    total: int
    reliability_score: float   # 0.0–1.0，三層覆蓋率加權
    warnings: List[str]        # 缺層或低量警告訊息


# ─────────────────────────────────────────────────────────────────
# Layer 2 — 三維情緒矩陣
# ─────────────────────────────────────────────────────────────────

@dataclass
class LayerSentiment:
    """單一資料層（media/forum/social）的情緒彙整"""
    layer: str            # "media" | "forum" | "social"
    label: str            # 顯示名稱，e.g. "媒體層（公關稿/新聞）"
    channels: List[str]   # 貢獻渠道列表
    count: int
    positive: int
    neutral: int
    negative: int
    avg_score: float
    top_themes: List[str]
    alert_articles: List[ArticleSummary]
    # PTT 論壇層獨有情感信號
    ptt_push_total: int = 0
    ptt_boo_total: int = 0
    ptt_neutral_total: int = 0
    ptt_push_ratio: float = 0.0   # 推/(推+噓)


@dataclass
class ThreeDimSentiment:
    """Layer 2：三維情緒矩陣（media / forum / social 分層）"""
    media: Optional[LayerSentiment]    # 媒體層
    forum: Optional[LayerSentiment]    # 論壇層
    social: Optional[LayerSentiment]   # 社群層
    overall_avg: float                 # 三層加權平均（有資料層）


# ─────────────────────────────────────────────────────────────────
# Layer 3 — 競品情報 & Layer 4 — 主目標完整報告
# ─────────────────────────────────────────────────────────────────

@dataclass
class BrandSummary:
    """競品品牌的輿情彙整摘要（Layer 3 競品情報用）"""
    keyword: str
    total: int
    positive: int
    neutral: int
    negative: int
    avg_score: float
    top_themes: List[str]
    alert_count: int
    # 各層分布（可能為 None 表示無資料）
    media_count: int = 0
    forum_count: int = 0
    social_count: int = 0


@dataclass
class BrandFullReport:
    """主目標品牌的完整四層分析報告"""
    keyword: str
    # 聚合統計（整體）
    total: int
    positive: int
    neutral: int
    negative: int
    avg_score: float
    top_themes: List[str]           # 前 5 大議題
    alert_articles: List[ArticleSummary]
    key_articles: List[ArticleSummary]
    pr_track: str                   # "A" | "B"
    pr_report: str
    alert_count: int
    sources: Dict[str, int]         # {channel: count}
    # 四層框架新增欄位
    data_quality: Optional[DataQualityLayer]
    sentiment_3dim: Optional[ThreeDimSentiment]


@dataclass
class DailyReport:
    """完整輿情日報資料包"""
    date: str
    generated_at: str
    primary: Optional[BrandFullReport]
    competitors: List[BrandSummary]
    all_keywords: List[str]


# ─────────────────────────────────────────────────────────────────
# 常數
# ─────────────────────────────────────────────────────────────────

LAYER_DISPLAY = {
    "media":  "媒體層（公關稿/新聞）",
    "forum":  "論壇層（PTT 真實民意）",
    "social": "社群層（Dcard 年輕族群）",
}

# 各層可信度權重（三層全有 = 1.0）
LAYER_WEIGHTS = {"media": 0.25, "forum": 0.40, "social": 0.35}

# 各渠道「低量」警示門檻
LOW_COUNT_THRESHOLD = 3


# ─────────────────────────────────────────────────────────────────
# 聚合器
# ─────────────────────────────────────────────────────────────────

class DailyReporter:
    ALERT_THRESHOLD = DEFAULT_ALERT_THRESHOLD

    def __init__(self, db_path: str = "sentiment_monitor.db",
                 primary_brand: str = "7-ELEVEN",
                 alert_threshold: float = None):
        self.db_path = db_path
        self.primary_brand = primary_brand
        self.alert_threshold = alert_threshold or self.ALERT_THRESHOLD

    # ── DB 查詢 ───────────────────────────────────────────────────

    def _get_run_ids(self, keyword: str, date: str) -> List[int]:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id FROM monitoring_runs WHERE keyword = ? AND started_at LIKE ?",
                (keyword, f"{date}%")
            )
            return [r[0] for r in c.fetchall()]

    def _get_analyses(self, run_ids: List[int]) -> List[Dict]:
        """
        取得分析結果，額外帶入 PTT push/boo/neutral 計數（來自 threads 表）。
        """
        if not run_ids:
            return []
        placeholders = ",".join("?" * len(run_ids))
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(f"""
                SELECT
                    a.sentiment, a.score, a.theme, a.reason,
                    t.title, t.url, t.channel, t.published_at,
                    t.push_count, t.boo_count, t.neutral_count,
                    s.name AS source_name
                FROM analyses a
                JOIN threads t ON a.thread_id = t.id
                LEFT JOIN sources s ON t.source_id = s.id
                WHERE a.run_id IN ({placeholders})
                ORDER BY a.score DESC
            """, run_ids)
            rows = [dict(r) for r in c.fetchall()]
            for row in rows:
                row["raw_score"] = row.get("score")
                row["score"] = normalize_score(row.get("score"))
            return rows

    def _get_latest_pr_report(self, run_ids: List[int]) -> Optional[Dict]:
        if not run_ids:
            return None
        placeholders = ",".join("?" * len(run_ids))
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(f"""
                SELECT track, report FROM pr_reports
                WHERE run_id IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
            """, run_ids)
            row = c.fetchone()
            return dict(row) if row else None

    def _get_active_keywords(self) -> List[str]:
        """取得系統設定的所有啟用品牌（不限日期）。"""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT keyword FROM keywords WHERE is_active = 1")
            return [r[0] for r in c.fetchall()]

    def _get_keywords_with_runs(self, date: str) -> List[str]:
        """
        取得指定日期實際有監控執行記錄的品牌清單。
        避免把「今天沒跑」的品牌列入日報的 all_keywords，
        造成「有在監測但沒動態」與「今天沒執行」的混淆。
        """
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT DISTINCT keyword FROM monitoring_runs WHERE started_at LIKE ?",
                (f"{date}%",)
            )
            return [r[0] for r in c.fetchall()]

    # ── Layer 1：資料品質儀表板 ───────────────────────────────────

    def _build_channel_health(self, channel: str, articles: List[Dict]) -> ChannelHealth:
        layer = CHANNEL_LAYER.get(channel, "media")
        label = CHANNEL_DISPLAY.get(channel, channel)
        count = len(articles)

        sentiments = Counter(a["sentiment"] for a in articles)
        scores = [a["score"] or 0 for a in articles]
        avg_score = round(sum(scores) / max(count, 1), 2)

        # PTT 獨有：從 threads 欄位取推/噓計數
        push_total = sum(int(a.get("push_count") or 0) for a in articles)
        boo_total  = sum(int(a.get("boo_count")  or 0) for a in articles)
        total_reactions = push_total + boo_total
        push_ratio = round(push_total / max(total_reactions, 1), 2) if channel == "ptt" else 0.0
        boo_ratio  = round(boo_total  / max(total_reactions, 1), 2) if channel == "ptt" else 0.0

        # 健康狀態判定
        if count == 0:
            status, reason = "down", "本日無採集資料"
        elif count < LOW_COUNT_THRESHOLD:
            status, reason = "warn", f"採集量偏低（{count} 篇），資料代表性不足"
        else:
            status, reason = "ok", f"正常（{count} 篇）"

        return ChannelHealth(
            channel=channel, label=label, layer=layer,
            count=count,
            positive=sentiments.get("正面", 0),
            neutral=sentiments.get("中立", 0),
            negative=sentiments.get("負面", 0),
            avg_score=avg_score,
            ptt_push_count=push_total if channel == "ptt" else 0,
            ptt_boo_count=boo_total  if channel == "ptt" else 0,
            push_ratio=push_ratio,
            boo_ratio=boo_ratio,
            status=status,
            status_reason=reason,
        )

    def _compute_data_quality(self, analyses: List[Dict]) -> DataQualityLayer:
        # 依渠道分組
        by_channel: Dict[str, List[Dict]] = {}
        for a in analyses:
            ch = a["channel"]
            by_channel.setdefault(ch, []).append(a)

        all_channels = ["google_news", "ptt", "dcard"]
        channel_healths = [
            self._build_channel_health(ch, by_channel.get(ch, []))
            for ch in all_channels
        ]

        has_media  = any(h.count > 0 for h in channel_healths if h.layer == "media")
        has_forum  = any(h.count > 0 for h in channel_healths if h.layer == "forum")
        has_social = any(h.count > 0 for h in channel_healths if h.layer == "social")

        # 可信度評分：按層加權
        score = sum(
            LAYER_WEIGHTS[layer]
            for layer, present in [("media", has_media), ("forum", has_forum), ("social", has_social)]
            if present
        )

        warnings = []
        if not has_media:
            warnings.append("⚠️ 媒體層（Google News）本日無資料，品牌敘事訊號缺失")
        if not has_forum:
            warnings.append("⚠️ 論壇層（PTT）本日無資料，真實民意情緒無從判斷")
        if not has_social:
            warnings.append("⚠️ 社群層（Dcard）本日無資料，年輕族群情緒缺失")
        # 低量警示
        for h in channel_healths:
            if h.status == "warn":
                warnings.append(f"⚠️ {h.label}：{h.status_reason}")

        return DataQualityLayer(
            channels=channel_healths,
            has_media=has_media,
            has_forum=has_forum,
            has_social=has_social,
            total=len(analyses),
            reliability_score=round(score, 2),
            warnings=warnings,
        )

    # ── Layer 2：三維情緒矩陣 ─────────────────────────────────────

    def _build_layer_sentiment(self, layer: str, articles: List[Dict]) -> Optional[LayerSentiment]:
        if not articles:
            return None

        counts    = Counter(a["sentiment"] for a in articles)
        scores    = [a["score"] or 0 for a in articles]
        avg_score = round(sum(scores) / max(len(articles), 1), 2)
        themes    = [a["theme"] for a in articles if a.get("theme")]
        top_themes = [t for t, _ in Counter(themes).most_common(5)]

        alert_articles = [
            ArticleSummary(
                title=a["title"], url=a["url"],
                source=a.get("source_name", ""),
                channel=a["channel"], sentiment=a["sentiment"],
                score=round(a["score"] or 0, 2),
                theme=a.get("theme", ""), reason=a.get("reason", ""),
                published=a.get("published_at", ""),
            )
            for a in articles
            if a["sentiment"] == "負面" and (a["score"] or 0) >= self.alert_threshold
        ]

        channels_present = list(dict.fromkeys(a["channel"] for a in articles))

        # PTT 論壇層：加總推/噓
        ptt_push = sum(int(a.get("push_count") or 0) for a in articles if a["channel"] == "ptt")
        ptt_boo  = sum(int(a.get("boo_count")  or 0) for a in articles if a["channel"] == "ptt")
        ptt_neu  = sum(int(a.get("neutral_count") or 0) for a in articles if a["channel"] == "ptt")
        total_reactions = ptt_push + ptt_boo
        push_ratio = round(ptt_push / max(total_reactions, 1), 2) if total_reactions > 0 else 0.0

        return LayerSentiment(
            layer=layer,
            label=LAYER_DISPLAY.get(layer, layer),
            channels=channels_present,
            count=len(articles),
            positive=counts.get("正面", 0),
            neutral=counts.get("中立", 0),
            negative=counts.get("負面", 0),
            avg_score=avg_score,
            top_themes=top_themes,
            alert_articles=alert_articles,
            ptt_push_total=ptt_push,
            ptt_boo_total=ptt_boo,
            ptt_neutral_total=ptt_neu,
            ptt_push_ratio=push_ratio,
        )

    def _compute_3dim_sentiment(self, analyses: List[Dict]) -> ThreeDimSentiment:
        by_layer: Dict[str, List[Dict]] = {}
        for a in analyses:
            layer = CHANNEL_LAYER.get(a["channel"], "media")
            by_layer.setdefault(layer, []).append(a)

        media_layer  = self._build_layer_sentiment("media",  by_layer.get("media",  []))
        forum_layer  = self._build_layer_sentiment("forum",  by_layer.get("forum",  []))
        social_layer = self._build_layer_sentiment("social", by_layer.get("social", []))

        # 三層加權平均（只計有資料層）
        weighted_sum = 0.0
        weight_total = 0.0
        for layer_obj, w in [(media_layer, LAYER_WEIGHTS["media"]),
                              (forum_layer, LAYER_WEIGHTS["forum"]),
                              (social_layer, LAYER_WEIGHTS["social"])]:
            if layer_obj and layer_obj.count > 0:
                weighted_sum += layer_obj.avg_score * w
                weight_total += w
        overall_avg = round(weighted_sum / max(weight_total, 0.001), 2)

        return ThreeDimSentiment(
            media=media_layer,
            forum=forum_layer,
            social=social_layer,
            overall_avg=overall_avg,
        )

    # ── 競品摘要（Layer 3）───────────────────────────────────────

    def _compute_brand_summary(self, keyword: str, analyses: List[Dict]) -> BrandSummary:
        counts    = Counter(a["sentiment"] for a in analyses)
        avg_score = round(sum(a["score"] or 0 for a in analyses) / max(len(analyses), 1), 2)
        themes    = [a["theme"] for a in analyses if a.get("theme")]
        top_themes = [t for t, _ in Counter(themes).most_common(3)]
        alert_count = sum(
            1 for a in analyses
            if a["sentiment"] == "負面" and (a["score"] or 0) >= self.alert_threshold
        )
        media_count  = sum(1 for a in analyses if CHANNEL_LAYER.get(a["channel"]) == "media")
        forum_count  = sum(1 for a in analyses if CHANNEL_LAYER.get(a["channel"]) == "forum")
        social_count = sum(1 for a in analyses if CHANNEL_LAYER.get(a["channel"]) == "social")

        return BrandSummary(
            keyword=keyword,
            total=len(analyses),
            positive=counts.get("正面", 0),
            neutral=counts.get("中立", 0),
            negative=counts.get("負面", 0),
            avg_score=avg_score,
            top_themes=top_themes,
            alert_count=alert_count,
            media_count=media_count,
            forum_count=forum_count,
            social_count=social_count,
        )

    # ── 主目標完整報告（Layers 1–4）──────────────────────────────

    def _compute_brand_full(self, keyword: str, analyses: List[Dict],
                            pr_data: Optional[Dict]) -> BrandFullReport:
        counts    = Counter(a["sentiment"] for a in analyses)
        avg_score = round(sum(a["score"] or 0 for a in analyses) / max(len(analyses), 1), 2)

        themes    = [a["theme"] for a in analyses if a.get("theme")]
        top_themes = [t for t, _ in Counter(themes).most_common(5)]

        alert_articles = [
            ArticleSummary(
                title=a["title"], url=a["url"], source=a.get("source_name", ""),
                channel=a["channel"], sentiment=a["sentiment"],
                score=round(a["score"] or 0, 2), theme=a.get("theme", ""),
                reason=a.get("reason", ""), published=a.get("published_at", ""),
            )
            for a in analyses
            if a["sentiment"] == "負面" and (a["score"] or 0) >= self.alert_threshold
        ]

        # 代表性文章：各渠道取最高分一篇
        seen_channels = set()
        key_articles = []
        for a in sorted(analyses, key=lambda x: x["score"] or 0, reverse=True):
            if a["channel"] not in seen_channels:
                seen_channels.add(a["channel"])
                key_articles.append(ArticleSummary(
                    title=a["title"], url=a["url"], source=a.get("source_name", ""),
                    channel=a["channel"], sentiment=a["sentiment"],
                    score=round(a["score"] or 0, 2), theme=a.get("theme", ""),
                    reason=a.get("reason", ""), published=a.get("published_at", ""),
                ))

        sources = dict(Counter(a["channel"] for a in analyses))
        pr_track  = (pr_data or {}).get("track", "A")
        pr_report = (pr_data or {}).get("report", "（本次未生成 PR 策略報告）")

        # 四層框架計算
        data_quality   = self._compute_data_quality(analyses)
        sentiment_3dim = self._compute_3dim_sentiment(analyses)

        return BrandFullReport(
            keyword=keyword,
            total=len(analyses),
            positive=counts.get("正面", 0),
            neutral=counts.get("中立", 0),
            negative=counts.get("負面", 0),
            avg_score=avg_score,
            top_themes=top_themes,
            alert_articles=alert_articles,
            key_articles=key_articles,
            pr_track=pr_track,
            pr_report=pr_report,
            alert_count=len(alert_articles),
            sources=sources,
            data_quality=data_quality,
            sentiment_3dim=sentiment_3dim,
        )

    # ── 主入口 ────────────────────────────────────────────────────

    def build(self, date: str = None) -> DailyReport:
        """
        查詢指定日期（預設今天）的所有品牌監控結果，
        回傳 DailyReport 資料包（含四層框架）。
        """
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        # 今天實際有執行記錄的品牌（用於日報顯示），
        # 與 _get_active_keywords() 的「系統啟用品牌」做區分。
        all_keywords   = self._get_keywords_with_runs(date)
        competitor_kws = [k for k in all_keywords if k != self.primary_brand]

        # 主目標品牌
        primary_run_ids  = self._get_run_ids(self.primary_brand, date)
        primary_analyses = self._get_analyses(primary_run_ids)
        primary_pr       = self._get_latest_pr_report(primary_run_ids)

        primary = (
            self._compute_brand_full(self.primary_brand, primary_analyses, primary_pr)
            if primary_analyses else None
        )

        # 競品品牌
        competitors = []
        for kw in competitor_kws:
            run_ids  = self._get_run_ids(kw, date)
            analyses = self._get_analyses(run_ids)
            if analyses:
                competitors.append(self._compute_brand_summary(kw, analyses))

        return DailyReport(
            date=date,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            primary=primary,
            competitors=competitors,
            all_keywords=all_keywords,
        )
