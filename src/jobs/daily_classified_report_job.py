from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from src.daily_report.builder import DailyReportBuilder
from src.utils.db_manager import APP_TIMEZONE, SentimentDB


def resolve_report_date(
    now: Optional[datetime] = None,
    timezone_name: str = APP_TIMEZONE,
    offset_days: int = -1,
) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    target_tz = ZoneInfo(timezone_name)
    local_now = current.astimezone(target_tz)
    return (local_now.date() + timedelta(days=offset_days)).isoformat()


def run_daily_classified_report_capture(
    db: SentimentDB = None,
    report_date: str = None,
    scope_key: str = "7-ELEVEN",
) -> Dict[str, object]:
    db = db or SentimentDB()
    report_date = report_date or resolve_report_date()
    builder = DailyReportBuilder(db)
    payload = builder.build_report(report_date=report_date, scope_key=scope_key)
    db.save_intel_daily_report(payload["report"])
    written_sections = 0
    for section in payload["sections"]:
        db.save_intel_daily_report_section(
            {
                "report_date": report_date,
                "scope_key": scope_key,
                "section_key": section["section_key"],
                "section_label": section["section_label"],
                "signal_count": section["signal_count"],
                "pos_count": section["pos_count"],
                "neu_count": section["neu_count"],
                "neg_count": section["neg_count"],
                "high_risk_count": section["high_risk_count"],
                "summary_text": section["summary_text"],
                "top_threads_json": section["top_threads_json"],
                "evidence_quotes_json": section["evidence_quotes_json"],
                "payload_json": section["payload_json"],
            }
        )
        written_sections += 1
    return {
        "report_date": report_date,
        "scope_key": scope_key,
        "written_sections": written_sections,
    }
