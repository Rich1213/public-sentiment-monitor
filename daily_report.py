"""
daily_report.py — 輿情日報產出入口 v2（四層框架）

從資料庫讀取當天監控結果，產出：
  1. Telegram 日報摘要（四層架構文字格式）
  2. Word 完整版日報（.docx）

用法：
  python daily_report.py                    # 今天的日報
  python daily_report.py --date 2026-05-15  # 指定日期
  python daily_report.py --no-telegram      # 只產出 Word
  python daily_report.py --no-docx          # 只發 Telegram
"""

import os
import sys
import json
import argparse
import subprocess
import dataclasses
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── 設定 ─────────────────────────────────────────────────────────
PRIMARY_BRAND   = os.getenv("PRIMARY_BRAND", "7-ELEVEN")
ALERT_THRESHOLD = float(os.getenv("ALERT_THRESHOLD", "0.7"))
REPORTS_DIR     = Path("reports")
SCRIPT_DIR      = Path(__file__).parent
DOCX_GENERATOR  = SCRIPT_DIR / "src" / "reporters" / "generate_docx.js"

CHANNEL_LABEL = {
    "google_news": "Google News",
    "ptt":         "PTT",
    "dcard":       "Dcard",
}

STATUS_ICON = {"ok": "✅", "warn": "⚠️", "down": "❌"}


# ── Telegram 格式化 ───────────────────────────────────────────────

def _health_icon(positive: int, neutral: int, negative: int, total: int) -> str:
    if total == 0:
        return "⚫"
    if negative > total / 2:
        return "🔴"
    if positive > total / 2:
        return "🟢"
    return "🟡"


def format_telegram(report) -> str:
    """
    將 DailyReport 轉為 Telegram Markdown 格式（四層框架）。

    結構：
      ─ 標頭
      ─ Layer 1：資料品質儀表板
      ─ Layer 2：三維情緒矩陣（media / forum / social）
      ─ Layer 3：競品動態摘要
      ─ Layer 4：PR 策略方向
      ─ 頁尾
    """
    lines = []
    p = report.primary

    # ── 標頭 ─────────────────────────────────────────────────────
    lines += [
        "╔══════════════════════════════╗",
        "  📊 *台灣便利超商輿情日報*",
        f"  日期：{report.date}",
        "╚══════════════════════════════╝",
        "",
    ]

    if not p:
        lines += [
            f"⚠️ 主目標品牌 *{PRIMARY_BRAND}* 本日無監測資料",
            "請先執行 `python main.py`",
            "",
        ]
        # 頁尾直接輸出
        lines += [
            "──────────────────────────────",
            f"🤖 自動產出｜{report.generated_at}",
            "_台灣便利超商輿情監控系統_",
        ]
        return "\n".join(lines)

    dq  = p.data_quality
    s3d = p.sentiment_3dim

    # ── Layer 1：資料品質儀表板 ───────────────────────────────────
    reliability_pct = int((dq.reliability_score if dq else 0) * 100)
    lines += [
        f"🎯 *主目標：{p.keyword}*",
        "──────────────────────────────",
        f"*【Layer 1】資料品質儀表板*",
        f"可信度評分：{'█' * (reliability_pct // 10)}{'░' * (10 - reliability_pct // 10)} {reliability_pct}%",
        "",
    ]

    if dq:
        for ch in dq.channels:
            icon = STATUS_ICON.get(ch.status, "⚠️")
            count_str = f"{ch.count} 篇" if ch.count > 0 else "無資料"
            ptt_extra = ""
            if ch.channel == "ptt" and ch.count > 0:
                ptt_extra = f"  推/噓={ch.push_ratio:.0%}/{ch.boo_ratio:.0%}"
            lines.append(f"  {icon} `{ch.label}`（{ch.layer}層）：{count_str}{ptt_extra}")

        if dq.warnings:
            lines.append("")
            for w in dq.warnings:
                lines.append(f"  {w}")
    lines.append("")

    # ── Layer 2：三維情緒矩陣 ────────────────────────────────────
    lines.append("*【Layer 2】三維情緒矩陣*")

    layer_configs = [
        (s3d.media  if s3d else None, "📰", "媒體層", "media"),
        (s3d.forum  if s3d else None, "💬", "論壇層 PTT", "forum"),
        (s3d.social if s3d else None, "📱", "社群層 Dcard", "social"),
    ]

    for layer_obj, emoji, label, layer_key in layer_configs:
        if layer_obj and layer_obj.count > 0:
            h = _health_icon(layer_obj.positive, layer_obj.neutral,
                             layer_obj.negative, layer_obj.count)
            lines.append(f"  {emoji} *{label}* {h}　{layer_obj.count} 篇｜強度 {layer_obj.avg_score}")
            lines.append(
                f"     ✅{layer_obj.positive} ⚪{layer_obj.neutral} 🚨{layer_obj.negative}"
            )
            # PTT 論壇層加推/噓比例
            if layer_key == "forum" and layer_obj.ptt_push_total + layer_obj.ptt_boo_total > 0:
                total_r = layer_obj.ptt_push_total + layer_obj.ptt_boo_total
                lines.append(
                    f"     推：{layer_obj.ptt_push_total}（{layer_obj.ptt_push_total/total_r:.0%}）"
                    f"　噓：{layer_obj.ptt_boo_total}（{layer_obj.ptt_boo_total/total_r:.0%}）"
                )
            # 熱門議題（前 2）
            if layer_obj.top_themes:
                themes_str = "、".join(layer_obj.top_themes[:2])
                lines.append(f"     議題：{themes_str}")
            # 層內警報文章
            if layer_obj.alert_articles:
                lines.append(f"     🚨 負面警報 {len(layer_obj.alert_articles)} 篇")
                for a in layer_obj.alert_articles[:2]:
                    lines.append(f"       · [{a.title[:40]}]({a.url})　強度 {a.score}")
        else:
            missing_msg = "（PTT 本日無資料，論壇情緒缺失）" if layer_key == "forum" \
                else "（Dcard 本日無資料，社群情緒缺失）" if layer_key == "social" \
                else "（新聞採集無資料）"
            lines.append(f"  {emoji} *{label}* ❌ {missing_msg}")

    if s3d:
        lines.append(f"  📊 三維加權情緒強度：*{s3d.overall_avg}*")
    lines.append("")

    # 整體高強度負面文章（跨層）
    if p.alert_articles:
        lines.append(f"⚠️ *高強度負面警報（共 {p.alert_count} 篇）*")
        for a in p.alert_articles[:3]:
            ch = CHANNEL_LABEL.get(a.channel, a.channel)
            lines += [
                f"  🚨 [{a.title[:50]}]({a.url})",
                f"     `{ch}` 強度 {a.score}｜{a.theme}",
            ]
        if len(p.alert_articles) > 3:
            lines.append(f"  ⋯⋯（另有 {p.alert_count - 3} 篇，詳見 Word 報告）")
        lines.append("")

    # ── Layer 3：競品動態摘要 ─────────────────────────────────────
    if report.competitors:
        lines += ["*【Layer 3】競品動態摘要*", "──────────────────────────────"]
        for c in report.competitors:
            h = _health_icon(c.positive, c.neutral, c.negative, c.total)
            alert_txt = f"　🚨 {c.alert_count} 篇警報" if c.alert_count > 0 else ""
            themes_txt = "、".join(c.top_themes[:2]) if c.top_themes else "—"
            # 三層分布
            layer_dist = f"媒{c.media_count}/壇{c.forum_count}/社{c.social_count}"
            lines.append(
                f"  {h} *{c.keyword}*：{c.total} 篇（{layer_dist}）"
                f"｜✅{c.positive} ⚪{c.neutral} 🚨{c.negative}{alert_txt}"
            )
            lines.append(f"     議題：{themes_txt}")
        lines.append("")

    # ── Layer 4：PR 策略方向 ──────────────────────────────────────
    track_label = "危機應對軌 🛡️" if p.pr_track == "A" else "品牌進攻軌 🚀"
    lines += [
        f"*【Layer 4】PR 策略：Track {p.pr_track}（{track_label}）*",
        "完整策略建議詳見附件 Word 報告。",
        "",
    ]

    # ── 頁尾 ──────────────────────────────────────────────────────
    lines += [
        "──────────────────────────────",
        f"🤖 自動產出｜{report.generated_at}",
        "_台灣便利超商輿情監控系統_",
    ]

    return "\n".join(lines)


# ── Word 文件產出 ─────────────────────────────────────────────────

def generate_docx(report, output_path: Path) -> bool:
    """將 report 序列化為 JSON 後呼叫 Node.js 產生 .docx。"""
    def to_dict(obj):
        if dataclasses.is_dataclass(obj):
            return {k: to_dict(v) for k, v in dataclasses.asdict(obj).items()}
        if isinstance(obj, list):
            return [to_dict(i) for i in obj]
        return obj

    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(to_dict(report), f, ensure_ascii=False, indent=2)

    result = subprocess.run(
        ["node", str(DOCX_GENERATOR), str(json_path), str(output_path)],
        capture_output=True, text=True
    )

    json_path.unlink(missing_ok=True)

    if result.returncode == 0:
        print(f"  ✅ Word 日報：{output_path}")
        return True
    else:
        print(f"  ❌ Word 產出失敗：{result.stderr}")
        return False


# ── 主流程 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="台灣便利超商輿情日報產出器")
    parser.add_argument("--date", default=None, help="指定日期（YYYY-MM-DD），預設今天")
    parser.add_argument("--no-telegram", action="store_true", help="不發送 Telegram")
    parser.add_argument("--no-docx", action="store_true", help="不產出 Word 文件")
    args = parser.parse_args()

    date = args.date or datetime.now().strftime("%Y-%m-%d")

    print(f"\n  📋 輿情日報產出｜日期：{date}｜主目標：{PRIMARY_BRAND}")
    print("  " + "─" * 50)

    # 聚合資料
    from src.utils.db_manager import SentimentDB
    from src.reporters.daily_reporter import DailyReporter

    db = SentimentDB()
    reporter = DailyReporter(
        db_path=db.db_path,
        primary_brand=PRIMARY_BRAND,
        alert_threshold=ALERT_THRESHOLD,
    )
    report = reporter.build(date=date)

    if not report.primary and not report.competitors:
        print(f"  ⚠️  {date} 無任何監測資料，請先執行 python main.py")
        sys.exit(0)

    # Telegram 日報
    if not args.no_telegram:
        from src.notifiers.telegram_notifier import TelegramNotifier
        try:
            notifier = TelegramNotifier()
            telegram_text = format_telegram(report)
            ok = notifier.send_daily_report(telegram_text)
            if ok:
                print("  ✅ Telegram 日報發送成功")
        except Exception as e:
            print(f"  ⚠️  Telegram 未設定或發送失敗：{e}")

    # Word 日報
    if not args.no_docx:
        REPORTS_DIR.mkdir(exist_ok=True)
        docx_path = REPORTS_DIR / f"輿情日報_{date}.docx"
        generate_docx(report, docx_path)

    print(f"\n  ✅ 日報產出完成｜{datetime.now().strftime('%H:%M:%S')}\n")


if __name__ == "__main__":
    main()
