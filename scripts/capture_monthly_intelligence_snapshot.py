import argparse
import json

from src.jobs.monthly_intelligence_snapshot_job import run_monthly_intelligence_snapshot_capture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True)
    args = parser.parse_args()
    print(json.dumps(run_monthly_intelligence_snapshot_capture(snapshot_month=args.month), ensure_ascii=False))


if __name__ == "__main__":
    main()
