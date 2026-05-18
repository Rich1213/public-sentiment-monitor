"""
backfill_legacy_scores.py — 將 analyses 舊版 0.x 分數回填成 1–5。

用法：
  python scripts/backfill_legacy_scores.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.db_manager import SentimentDB


def main():
    db = SentimentDB()
    updated = db.backfill_legacy_scores()
    print(f"已回填 {updated} 筆舊分數。")


if __name__ == "__main__":
    main()
