"""
worker/runner.py — 監控核心執行器

從 main.py 抽取的業務邏輯層，可被以下方式呼叫：

  1. CLI（直接執行，保留原有行為）：
       python worker/runner.py
       python worker/runner.py -k 7-ELEVEN 全家 --fresh

  2. FastAPI BackgroundTasks（api/app.py 觸發）：
       from worker.runner import run_all_brands
       run_all_brands(keywords=["7-ELEVEN"], fresh_mode=False)

  3. Railway Cron Worker（定時排程）：
       python -m worker.runner

設計原則：
  - run_monitor()    處理單一品牌的完整流程（採集→分析→通知→PR）
  - run_all_brands() 遍歷所有品牌，保護 API 速率限制
  - main()           CLI 入口（保留 argparse 介面與 banner）
"""

import os
import sys
import time
import hashlib
import argparse
import logging
from pathlib import Path
from collections import Counter
from datetime import datetime
from dotenv import load_dotenv

# 確保專案根目錄在 sys.path（直接執行 worker/runner.py 時需要）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")

from src.utils.logger import get_logger
logger = get_logger(__name__)

# ── 採集與警報設定 ───────────────────────────────────────────────
DEFAULT_KEYWORDS     = ["7-ELEVEN", "全家", "萊爾富", "OK mart", "超商食安"]
ALERT_THRESHOLD      = float(os.getenv("ALERT_THRESHOLD", "0.7"))
FETCH_LIMIT          = int(os.getenv("FETCH_LIMIT", "15"))   # 提高以降低漏網率
INTER_ARTICLE_DELAY  = float(os.getenv("INTER_ARTICLE_DELAY", "1.5"))   # NVIDIA 40 req/min 保護
INTER_BRAND_COOLDOWN = int(os.getenv("INTER_BRAND_COOLDOWN", "60"))      # 品牌間冷卻（秒）

SENTIMENT_EMOJI = {"正面": "✅", "中立": "⚪", "負面": "🚨"}


# ─────────────────────────────────────────────────────────────
# 格式化輸出工具
# ─────────────────────────────────────────────────────────────

def _print_banner():
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║     台灣便利超商輿情監控系統                             ║")
    print("║     Taiwan CVS Public Sentiment Monitor v2.0             ║")
    print("╚══════════════════════════════════════════════════════════╝")


def _print_sep(char="─", width=60):
    print(char * width)


# ─────────────────────────────────────────────────────────────
# 單一品牌監控流程
# ─────────────────────────────────────────────────────────────

def run_monitor(keyword: str, db, analyzer, advisor, notifier, fresh_mode: bool = False):
    """
    對單一品牌關鍵字執行完整的採集 → 分析 → 通知流程。

    Args:
        keyword:    品牌關鍵字（例：7-ELEVEN）
        db:         SentimentDB 實例
        analyzer:   SentimentAnalyzer 實例
        advisor:    PRAdvisor 實例
        notifier:   TelegramNotifier 實例（None = 不發通知）
        fresh_mode: True = 忽略去重快取，強制重新採集
    """
    from src.collectors.google_news_collector import GoogleNewsCollector
    from src.collectors.ptt_collector import PTTCollector
    from src.collectors.dcard_collector import DcardCollector

    _print_sep("═")
    print(f"  🏪 {keyword}  [{datetime.now().strftime('%Y-%m-%d %H:%M')}]")
    _print_sep("═")

    db.ensure_keyword(keyword)
    run_id = db.create_run(keyword, fresh_mode=fresh_mode)

    # ── 1. 多渠道資料採集 ─────────────────────────────────────
    print("\n  📡 多渠道採集中...")
    all_articles = []
    collector_map = [
        ("Google News", GoogleNewsCollector(keyword, db=db)),
        ("PTT",         PTTCollector(keyword, db=db)),
        ("Dcard",       DcardCollector(keyword, db=db)),
    ]

    for name, collector in collector_map:
        try:
            articles = collector.fetch_latest_posts(
                limit=FETCH_LIMIT, fresh_mode=fresh_mode
            )
            all_articles.extend(articles)
        except Exception as e:
            print(f"  ⚠️  [{name}] 採集失敗：{e}")
            logger.warning("[%s] 採集失敗：%s", name, e, exc_info=True)

    if not all_articles:
        print(f"\n  ℹ️  {keyword}：本次無新資料，結束。")
        db.close_run(run_id, articles_found=0, articles_new=0)
        return

    print(f"\n  📥 共採集 {len(all_articles)} 篇，開始 AI 情感分析...\n")

    # ── 2. 逐篇情感分析 ──────────────────────────────────────
    _print_sep()
    print("  📰 各篇輿情分析")
    _print_sep()

    analyses    = []
    alert_pairs = []   # [(article, analysis), ...]

    for i, article in enumerate(all_articles, 1):
        if i > 1:
            time.sleep(INTER_ARTICLE_DELAY)   # 控制 NVIDIA API 速率（40 req/min）
        try:
            analysis = analyzer.analyze(article["title"], article.get("content", ""))
        except Exception as e:
            logger.warning("情感分析失敗 [%s]：%s", article.get("link", "?"), e)
            analysis = {
                "sentiment": "未知", "score": 0,
                "theme": "分析失敗", "reason": str(e),
                "voice_source": "未知", "analyzed_with": "標題",
            }
        analyses.append(analysis)

        # 寫入 DB
        thread_id = hashlib.md5(article["link"].encode()).hexdigest()
        try:
            db.save_analysis(
                thread_id, run_id, analysis,
                analyzed_content=article.get("content", "")[:500],
            )
        except Exception as e:
            logger.warning("分析結果儲存失敗 [%s]：%s", article.get("link", "?"), e)

        # 格式化輸出
        sentiment  = analysis.get("sentiment", "未知")
        score      = analysis.get("score", 0)
        theme      = analysis.get("theme", "-")
        reason     = analysis.get("reason", "-")
        voice      = analysis.get("voice_source", "-")
        analyzed_w = analysis.get("analyzed_with", "-")
        emoji      = SENTIMENT_EMOJI.get(sentiment, "❓")

        print(f"\n[{i:02d}] {article['title']}")
        print(f"      📰 {article['source']}　🕐 {article['published']}　依據：{analyzed_w}")
        print(f"      {emoji} 情緒：{sentiment}（強度 {score}）　🏷 主題：{theme}")
        print(f"      🗣 聲量：{voice}")
        print(f"      💡 {reason}")
        print(f"      🔗 {article['link']}")

        # 達到警報閾值
        if sentiment == "負面" and score >= ALERT_THRESHOLD:
            alert_pairs.append((article, analysis))

    # ── 3. 輿情彙整統計 ──────────────────────────────────────
    print()
    _print_sep()
    print("  📊 輿情彙整")
    _print_sep()

    counts    = Counter(a.get("sentiment", "未知") for a in analyses)
    total     = len(analyses)
    avg_score = sum(a.get("score", 0) for a in analyses) / max(total, 1)
    themes    = list(dict.fromkeys(
        a.get("theme", "").upper() for a in analyses if a.get("theme")
    ))

    for label in ["負面", "中立", "正面"]:
        cnt = counts.get(label, 0)
        bar = "█" * cnt + "░" * (total - cnt)
        print(f"  {SENTIMENT_EMOJI.get(label)} {label}：{cnt} 篇  [{bar}]")

    print(f"\n  平均情緒強度：{avg_score:.2f}")
    print(f"  主導情緒：{SENTIMENT_EMOJI.get(counts.most_common(1)[0][0], '❓')} "
          f"{counts.most_common(1)[0][0]}")
    print(f"  主要議題：{'、'.join(themes[:5]) if themes else '—'}")

    # ── 4. Telegram 警報 ──────────────────────────────────────
    if alert_pairs:
        if notifier:
            print(f"\n  🚨 發現 {len(alert_pairs)} 篇高強度負面文章，發送 Telegram 警報...")
            for article, analysis in alert_pairs:
                try:
                    notifier.send_sentiment_alert(keyword, article, analysis)
                except Exception as e:
                    print(f"     ⚠️  通知失敗：{e}")
        else:
            print(f"\n  🚨 {keyword}：發現 {len(alert_pairs)} 篇高強度負面（Telegram 未設定，警報未發送）")
            for article, analysis in alert_pairs:
                score = analysis.get("score", 0)
                print(f"     ⚠️  [{score:.1f}] {article['title'][:50]}")
                print(f"          🔗 {article['link']}")
    else:
        print(f"\n  ✅ {keyword}：無緊急警報")

    # ── 5. PR 策略分析 ────────────────────────────────────────
    print()
    _print_sep()
    print("  🧠 公關戰略分析（AI 研判中）")
    _print_sep()

    try:
        pr_report = advisor.advise(keyword, all_articles, analyses)
        negative_count = counts.get("負面", 0)
        track = "A" if negative_count > total / 2 else "B"
        db.save_pr_report(run_id, keyword, track, pr_report)
        print(pr_report)
    except Exception as e:
        print(f"  ⚠️  PR 分析失敗：{e}")
        logger.error("PR 分析失敗 [%s]：%s", keyword, e, exc_info=True)

    db.close_run(run_id, articles_found=total, articles_new=total)


# ─────────────────────────────────────────────────────────────
# 多品牌批次執行（供 API + Cron 呼叫）
# ─────────────────────────────────────────────────────────────

def run_all_brands(
    keywords: list = None,
    fresh_mode: bool = False,
    print_banner: bool = False,
) -> None:
    """
    遍歷所有指定品牌，依序執行完整監控流程。

    Args:
        keywords:     品牌清單（None = 使用 env MONITOR_KEYWORDS 或預設四大品牌）
        fresh_mode:   True = 強制重新採集
        print_banner: True = 顯示 ASCII banner（CLI 模式用）
    """
    # 關鍵字優先序：參數 > env > 預設值
    if not keywords:
        env_kws = [k.strip() for k in os.getenv("MONITOR_KEYWORDS", "").split(",") if k.strip()]
        keywords = env_kws or DEFAULT_KEYWORDS

    from src.utils.db_manager import SentimentDB
    from src.analyzers.sentiment_analyzer import SentimentAnalyzer
    from src.analyzers.pr_advisor import PRAdvisor
    from src.notifiers.telegram_notifier import TelegramNotifier

    db       = SentimentDB()
    analyzer = SentimentAnalyzer()
    advisor  = PRAdvisor()

    try:
        notifier = TelegramNotifier()
    except Exception as e:
        print(f"\n  ⚠️  Telegram 未設定，將略過警報通知：{e}")
        notifier = None

    if print_banner:
        _print_banner()
        print(f"\n  監控品牌：{', '.join(keywords)}")
        print(f"  資料來源：Google News, PTT, Dcard")
        print(f"  採集上限：每渠道 {FETCH_LIMIT} 篇")
        print(f"  警報閾值：負面強度 ≥ {ALERT_THRESHOLD}")
        print(f"  重新採集：{'是（忽略去重）' if fresh_mode else '否（跳過已採集）'}")
        print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    for idx, keyword in enumerate(keywords):
        if idx > 0:
            print(f"\n  ⏳ 品牌間冷卻 {INTER_BRAND_COOLDOWN}s（保護 NVIDIA API 速率限制）...")
            time.sleep(INTER_BRAND_COOLDOWN)
        try:
            run_monitor(
                keyword=keyword,
                db=db,
                analyzer=analyzer,
                advisor=advisor,
                notifier=notifier,
                fresh_mode=fresh_mode,
            )
        except KeyboardInterrupt:
            print("\n\n  ⛔ 使用者中斷，結束監控。")
            sys.exit(0)
        except Exception as e:
            print(f"\n  ❌ {keyword} 監控發生錯誤：{e}")
            import traceback
            traceback.print_exc()

    _print_sep("═")
    print(f"  ✅ 本次監控完成｜{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _print_sep("═")
    print()


# ─────────────────────────────────────────────────────────────
# CLI 入口（保留原 main.py 行為）
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="台灣便利超商輿情監控系統",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  python worker/runner.py                      監控所有四大品牌
  python worker/runner.py -k 7-ELEVEN 全家     只監控指定品牌
  python worker/runner.py --fresh              強制重新採集（忽略去重快取）
  python -m worker.runner                      以模組方式執行（Railway Cron）
        """,
    )
    parser.add_argument(
        "--keywords", "-k",
        nargs="+",
        default=None,
        metavar="品牌",
        help="指定監控關鍵字（預設：7-ELEVEN 全家 萊爾富 OK mart）",
    )
    parser.add_argument(
        "--fresh", "-f",
        action="store_true",
        help="強制重新採集，忽略去重快取",
    )
    args = parser.parse_args()

    keywords = args.keywords or None  # None 由 run_all_brands 自行解析 env

    run_all_brands(keywords=keywords, fresh_mode=args.fresh, print_banner=True)


if __name__ == "__main__":
    main()
