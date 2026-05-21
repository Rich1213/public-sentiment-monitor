"""
src/utils/alert_engine.py — 緊急事件警報引擎

【定位】
  所有渠道共用的警報判斷與物件建構層。
  runner.py（即時通知）與 db_manager.py（Dashboard 查詢）
  都應透過此模組判斷警報，不應在各自地方重複撰寫條件。

【設計原則】
  1. is_alert_eligible()        — 單篇分析資格判斷（channel-agnostic）
  2. is_thread_alert_by_items() — 留言聚合警報判斷（策略 A：≥ N 則高風險留言）
  3. build_alert_*()            — 標準化警報 dict 建構
  4. sort_alerts()              — 統一排序邏輯
  5. ALERT_THRESHOLD            — 唯一的閾值定義點

【警報觸發兩個 Scenario】
  Scenario 1（貼文本身風險）：
    今天採集到的新文章（Google News / PTT / Dcard / YouTube 主文），
    分析後 sentiment == 負面 AND score >= ALERT_THRESHOLD。
    → 日期新鮮度由各 Collector 在採集端控管（不接受舊文章入庫）。

  Scenario 2（留言風險聚合）：
    有留言機制的渠道（YouTube / PTT 推文 / Dcard 留言），
    今天分析的留言中，score >= ALERT_THRESHOLD 的數量 >= COMMENT_ALERT_MIN_COUNT。
    → 貼文本身不必達到警報分數；由留言群體情緒判斷。
    → 若貼文已因 Scenario 1 觸發警報，不重複加入。

  新增渠道時，只需確保：
    a) Collector 控管採集日期（不讓無關的舊文章進來）
    b) 有留言機制的渠道在 runner.py 收集 item analyses，
       並呼叫 is_thread_alert_by_items() 判斷
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.utils.score_utils import ALERT_THRESHOLD  # 常數保留在 score_utils，此處 re-export

# 同一 thread 今天需要幾則高風險留言才觸發 Scenario 2 警報
COMMENT_ALERT_MIN_COUNT = 2

# ── 對外 re-export，讓其他模組只需 import alert_engine ──────────────
__all__ = [
    "ALERT_THRESHOLD",
    "COMMENT_ALERT_MIN_COUNT",
    "is_alert_eligible",
    "is_thread_alert_by_items",
    "build_alert_from_row",
    "build_alert_from_runtime",
    "build_alert_from_items",
    "sort_alerts",
]


# ─────────────────────────────────────────────────────────────────────
# 核心資格判斷
# ─────────────────────────────────────────────────────────────────────

def is_alert_eligible(sentiment: str, score: Any) -> bool:
    """
    判斷一筆分析結果是否達到警報資格。

    Channel-agnostic：所有渠道使用同一條件。
    日期新鮮度不在此判斷——由各 Collector 採集端負責。

    Args:
        sentiment: "正面" / "中立" / "負面"
        score:     1–5 整數（或可轉換的型別）

    Returns:
        True → 應觸發警報
    """
    try:
        s = int(score)
    except (TypeError, ValueError):
        s = 0
    return sentiment == "負面" and s >= ALERT_THRESHOLD


def is_thread_alert_by_items(
    item_analyses: List[Dict[str, Any]],
    min_count: int = COMMENT_ALERT_MIN_COUNT,
) -> bool:
    """
    判斷一個 thread 的留言群是否觸發 Scenario 2 警報。

    條件：item_analyses 中 score >= ALERT_THRESHOLD 的筆數 >= min_count。

    Args:
        item_analyses: 同一 thread 的留言分析結果列表
                       （runner.py 用 dict list；db_manager 用 DB row list，
                        兩者都有 sentiment / score 欄位，介面相容）
        min_count:     觸發閾值，預設 COMMENT_ALERT_MIN_COUNT

    Returns:
        True → 應觸發留言聚合警報
    """
    high_risk_count = sum(
        1 for item in item_analyses
        if is_alert_eligible(item.get("sentiment", ""), item.get("score", 0))
    )
    return high_risk_count >= min_count


# ─────────────────────────────────────────────────────────────────────
# 警報物件建構
# ─────────────────────────────────────────────────────────────────────

def build_alert_from_row(
    row: Dict[str, Any],
    active_threads: Optional[Dict[str, Any]] = None,
    snapshot_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    從 DB 查詢結果（analysis row JOIN thread）建構標準化警報 dict。

    主要用於 db_manager.get_dashboard_day_summary()。

    Args:
        row:            analysis row，包含 thread 欄位（title, url, channel 等）
        active_threads: {thread_id: thread_ctx} dict，用於計算 ongoing_days
                        未傳入時略過 ongoing_days 計算（回傳 0）
        snapshot_date:  查詢的快照日期（"YYYY-MM-DD"），用於計算 ongoing_days

    Returns:
        標準化警報 dict（與 build_alert_from_runtime 回傳形狀一致）
    """
    thread_id = row.get("thread_id")
    thread_ctx = (active_threads or {}).get(thread_id, {}) if thread_id else {}

    first_seen_at = (
        thread_ctx.get("first_seen_at")
        or row.get("first_seen_at")
        or row.get("published_at")
        or ""
    )
    recent_activity_at = (
        thread_ctx.get("recent_activity_at")
        or row.get("published_at")
        or first_seen_at
        or ""
    )

    ongoing_days = 0
    if snapshot_date and first_seen_at and str(first_seen_at)[:10] < snapshot_date:
        if thread_id in (active_threads or {}):
            ongoing_days = thread_ctx.get("ongoing_days", 0)
        else:
            try:
                d0 = datetime.fromisoformat(str(first_seen_at)[:10])
                d1 = datetime.fromisoformat(snapshot_date)
                ongoing_days = max(0, (d1 - d0).days)
            except (ValueError, TypeError):
                ongoing_days = 0

    return {
        "brand":             row.get("keyword") or row.get("brand") or "",
        "channel":           row.get("channel") or "",
        "title":             row.get("title") or "—",
        "url":               row.get("url") or "",
        "score":             int(row.get("score") or 0),
        "theme":             row.get("theme") or "—",
        "published":         row.get("published_at") or "",
        "recent_activity_at": recent_activity_at,
        "first_seen_at":     first_seen_at,
        "ongoing_days":      ongoing_days,
        "thread_id":         thread_id,
    }


def build_alert_from_runtime(
    keyword: str,
    article: Dict[str, Any],
    analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """
    從 runner.py 執行時期的 (article, analysis) 建構標準化警報 dict。

    主要用於 worker/runner.py 的即時 Telegram 通知。

    Args:
        keyword:  品牌關鍵字
        article:  Collector 回傳的文章 dict
        analysis: SentimentAnalyzer 回傳的分析 dict

    Returns:
        標準化警報 dict（與 build_alert_from_row 回傳形狀一致）
    """
    published = (
        article.get("published")
        or article.get("published_at")
        or ""
    )
    return {
        "brand":             keyword,
        "channel":           article.get("channel") or "",
        "title":             article.get("title") or "—",
        "url":               article.get("link") or article.get("url") or "",
        "score":             int(analysis.get("score") or 0),
        "theme":             analysis.get("theme") or "—",
        "published":         published,
        "recent_activity_at": published,
        "first_seen_at":     published,
        "ongoing_days":      0,
        "thread_id":         None,
    }


def build_alert_from_items(
    keyword: str,
    item_rows: List[Dict[str, Any]],
    active_threads: Optional[Dict[str, Any]] = None,
    snapshot_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    從同一 thread 的多筆 item_analysis rows 建構留言聚合警報 dict。

    主要用於 Scenario 2：貼文本身未達閾值，但留言群體情緒達標。
    警報物件指向「母貼文」，score 取高風險留言中的最高分。

    Args:
        keyword:        品牌關鍵字
        item_rows:      同一 thread 的 item_analysis 列表，每筆需含
                        thread_id / title / url / channel（來自 JOIN threads）
                        sentiment / score / theme / published_at
        active_threads: {thread_id: ctx} dict，用於 ongoing_days 計算
        snapshot_date:  快照日期（"YYYY-MM-DD"）

    Returns:
        標準化警報 dict，額外含 trigger="comments" 與 comment_alert_count
    """
    high_risk = [
        r for r in item_rows
        if is_alert_eligible(r.get("sentiment", ""), r.get("score", 0))
    ]
    # 取分數最高的留言做代表
    best = max(high_risk, key=lambda r: int(r.get("score") or 0))

    thread_id = best.get("thread_id")
    thread_ctx = (active_threads or {}).get(thread_id, {}) if thread_id else {}

    # 最新留言時間 → recent_activity_at
    comment_dates = [r.get("published_at", "") for r in item_rows if r.get("published_at")]
    recent_activity_at = max(comment_dates, default="") if comment_dates else ""

    first_seen_at = thread_ctx.get("first_seen_at") or best.get("published_at") or ""

    # 最常見主題（取高風險留言）
    theme_counts: Dict[str, int] = {}
    for r in high_risk:
        t = r.get("theme") or ""
        if t:
            theme_counts[t] = theme_counts.get(t, 0) + 1
    top_theme = max(theme_counts, key=lambda k: theme_counts[k]) if theme_counts else "—"

    ongoing_days = 0
    if snapshot_date and first_seen_at and str(first_seen_at)[:10] < snapshot_date:
        if thread_id in (active_threads or {}):
            ongoing_days = thread_ctx.get("ongoing_days", 0)
        else:
            try:
                d0 = datetime.fromisoformat(str(first_seen_at)[:10])
                d1 = datetime.fromisoformat(snapshot_date)
                ongoing_days = max(0, (d1 - d0).days)
            except (ValueError, TypeError):
                ongoing_days = 0

    return {
        "brand":               keyword,
        "channel":             best.get("channel") or "",
        "title":               best.get("title") or "—",
        "url":                 best.get("url") or "",
        "score":               int(best.get("score") or 0),
        "theme":               top_theme,
        "published":           "",          # 母貼文 published_at 不在 item rows 裡
        "recent_activity_at":  recent_activity_at,
        "first_seen_at":       first_seen_at,
        "ongoing_days":        ongoing_days,
        "thread_id":           thread_id,
        "trigger":             "comments",  # 標記由留言聚合觸發
        "comment_alert_count": len(high_risk),
    }


# ─────────────────────────────────────────────────────────────────────
# 排序
# ─────────────────────────────────────────────────────────────────────

def sort_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    警報排序：分數高優先，同分則最近活動時間優先。

    統一排序邏輯，避免 runner.py 與 db_manager.py 各自定義。
    """
    return sorted(
        alerts,
        key=lambda a: (a.get("score", 0), a.get("recent_activity_at") or ""),
        reverse=True,
    )
