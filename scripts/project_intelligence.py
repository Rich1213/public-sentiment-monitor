import argparse
import json

from src.jobs.intelligence_projector_job import run_intelligence_projector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-date", default=None)
    args = parser.parse_args()
    print(json.dumps(run_intelligence_projector(since_date=args.since_date), ensure_ascii=False))


if __name__ == "__main__":
    main()
