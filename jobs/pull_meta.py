import argparse
import json

from services.meta_sync import run_backfill_sync, run_daily_sync


def main():
    parser = argparse.ArgumentParser(description="Run Meta Ads sync jobs.")
    parser.add_argument("mode", nargs="?", default="daily", choices=["daily", "backfill"])
    parser.add_argument("--days", type=int, default=None, help="Backfill days override.")
    args = parser.parse_args()

    if args.mode == "daily":
        result = run_daily_sync()
    else:
        result = run_backfill_sync(days=args.days)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
