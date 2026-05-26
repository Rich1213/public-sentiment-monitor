from typing import Iterable, List

from src.intelligence.monthly_snapshot_builder import MonthlySnapshotBuilder
from src.utils.db_manager import SentimentDB


DEFAULT_INTELLIGENCE_SCOPE_KEYS = [
    "7-ELEVEN",
    "全家",
    "萊爾富",
    "OK mart",
    "market",
]


def run_monthly_intelligence_snapshot_capture(
    db: SentimentDB = None,
    snapshot_month: str = None,
    scope_keys: Iterable[str] = None,
) -> dict:
    db = db or SentimentDB()
    builder = MonthlySnapshotBuilder(db)
    scope_keys = list(scope_keys or DEFAULT_INTELLIGENCE_SCOPE_KEYS)
    written = 0
    for scope_key in scope_keys:
        scope_type = "market" if scope_key == "market" else "brand"
        payload = builder.build_snapshot(snapshot_month=snapshot_month, scope_type=scope_type, scope_key=scope_key)
        db.save_intel_monthly_snapshot(payload)
        written += 1
    return {"snapshot_month": snapshot_month, "written": written, "scope_keys": scope_keys}
