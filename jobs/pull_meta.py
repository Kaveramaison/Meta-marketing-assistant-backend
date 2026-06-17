import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.meta_sync import run_backfill_sync, run_daily_sync, run_scheduled_sync
from services.meta_warehouse_sync import run_daily_metadata_sync


def main():
    parser = argparse.ArgumentParser(description="Run Meta Ads sync jobs.")
    parser.add_argument("mode", nargs="?", default="daily", choices=["daily", "backfill", "scheduled", "daily_metadata"])
    parser.add_argument("--days", type=int, default=None, help="Backfill days override.")
    args = parser.parse_args()

    if args.mode == "daily":
        result = run_daily_sync()
    elif args.mode == "backfill":
        result = run_backfill_sync(days=args.days)
    elif args.mode == "daily_metadata":
        result = run_daily_metadata_sync()
    else:
        result = run_scheduled_sync()

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
