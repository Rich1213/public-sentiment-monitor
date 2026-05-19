import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from zoneinfo import ZoneInfo

from src.utils.db_manager import SentimentDB


def resolve_snapshot_date(
    now: Optional[datetime] = None,
    timezone_name: str = "Asia/Taipei",
    offset_days: int = -1,
) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    target_tz = ZoneInfo(timezone_name)
    local_now = current.astimezone(target_tz)
    return (local_now.date() + timedelta(days=offset_days)).isoformat()


def run_daily_snapshot_capture(
    db_path: Optional[str] = None,
    timezone_name: str = "Asia/Taipei",
    offset_days: int = -1,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    snapshot_date = resolve_snapshot_date(now=now, timezone_name=timezone_name, offset_days=offset_days)
    keywords_env = os.getenv("MONITOR_KEYWORDS", "").strip()
    keywords = [item.strip() for item in keywords_env.split(",") if item.strip()] or None

    db = SentimentDB(db_path=db_path)
    written = db.save_daily_snapshots(snapshot_date=snapshot_date, keywords=keywords)
    return {
        "snapshot_date": snapshot_date,
        "written": written,
        "keywords": keywords or [],
        "timezone": timezone_name,
    }


def main() -> int:
    timezone_name = os.getenv("SNAPSHOT_TIMEZONE", "Asia/Taipei")
    offset_days = int(os.getenv("SNAPSHOT_DATE_OFFSET_DAYS", "-1"))
    result = run_daily_snapshot_capture(
        timezone_name=timezone_name,
        offset_days=offset_days,
    )
    print(
        f"[daily_snapshot_job] snapshot_date={result['snapshot_date']} "
        f"written={result['written']} timezone={result['timezone']}"
    )
    if result["keywords"]:
        print(f"[daily_snapshot_job] keywords={','.join(result['keywords'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
