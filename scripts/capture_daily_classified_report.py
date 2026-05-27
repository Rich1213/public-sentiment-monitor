import os
import sys
from pathlib import Path
import argparse

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_dotenv(REPO_ROOT / ".env")

from src.jobs.daily_classified_report_job import run_daily_classified_report_capture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    scope_key = os.getenv("DAILY_REPORT_SCOPE_KEY", "7-ELEVEN")
    result = run_daily_classified_report_capture(report_date=args.date, scope_key=scope_key)
    print(
        f"[daily_classified_report] report_date={result['report_date']} "
        f"scope_key={result['scope_key']} written_sections={result['written_sections']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
