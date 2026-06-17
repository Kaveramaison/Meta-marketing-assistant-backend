import argparse
import json

from services.meta_sync import run_backfill_sync, run_daily_sync, run_scheduled_sync


def main():
    parser = argparse.ArgumentParser(description="Run Meta Ads sync jobs.")
    parser.add_argument("mode", nargs="?", default="daily", choices=["daily", "backfill", "scheduled"])
    parser.add_argument("--days", type=int, default=None, help="Backfill days override.")
    args = parser.parse_args()

    if args.mode == "daily":
        result = run_daily_sync()
    elif args.mode == "backfill":
        result = run_backfill_sync(days=args.days)
    else:
        result = run_scheduled_sync()

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
