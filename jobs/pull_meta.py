import os
import sys
import json
import requests
from datetime import date, timedelta
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLE_NAME = "marketing_performance_daily"
BATCH_SIZE = 500


def to_int(value):
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def to_float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def get_results(actions):
    if not actions:
        return 0

    for action in actions:
        if action.get("action_type") in [
            "lead",
            "onsite_conversion.lead_grouped",
            "offsite_complete_registration_add_meta_leads",
            "leadgen.other"
        ]:
            return to_int(action.get("value"))

    return 0


def chunk_list(items, size=BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_accounts_for_backfill():
    result = (
        supabase.table("meta_accounts")
        .select("id, client_id, ad_account_id, ad_account_name, access_token, backfill_done")
        .eq("backfill_done", False)
        .execute()
    )

    return result.data or []


def get_accounts_for_daily():
    result = (
        supabase.table("meta_accounts")
        .select("id, client_id, ad_account_id, ad_account_name, access_token, backfill_done")
        .eq("backfill_done", True)
        .execute()
    )

    return result.data or []


def fetch_meta_data(meta_account, target_date):
    raw_ad_account_id = meta_account["ad_account_id"]
    ad_account_id = f"act_{raw_ad_account_id}"
    access_token = meta_account["access_token"]

    print(f"Fetching {target_date} for {ad_account_id}")

    url = f"https://graph.facebook.com/v20.0/{ad_account_id}/insights"

    params = {
        "access_token": access_token,
        "level": "ad",
        "time_range": json.dumps({
            "since": target_date,
            "until": target_date
        }),
        "breakdowns": "country",
        "fields": ",".join([
            "date_start",
            "campaign_id",
            "campaign_name",
            "adset_id",
            "adset_name",
            "ad_id",
            "ad_name",
            "impressions",
            "clicks",
            "reach",
            "spend",
            "actions"
        ]),
        "limit": 500
    }

    all_rows = []

    while url:
        response = requests.get(url, params=params)
        data = response.json()

        if "error" in data:
            print("Meta API Error:")
            print(json.dumps(data, indent=2))
            raise Exception("Meta API failed")

        all_rows.extend(data.get("data", []))

        url = data.get("paging", {}).get("next")
        params = None

    return all_rows


def normalize_rows(meta_account, rows):
    output = []

    for row in rows:
        output.append({
            "perf_date": row.get("date_start"),
            "client_id": meta_account["client_id"],
            "platform": "meta",
            "account_id": meta_account["ad_account_id"],
            "account_name": meta_account.get("ad_account_name"),
            "campaign_id": row.get("campaign_id"),
            "campaign_name": row.get("campaign_name"),
            "adset_id": row.get("adset_id"),
            "adset_name": row.get("adset_name"),
            "ad_id": row.get("ad_id"),
            "ad_name": row.get("ad_name"),
            "country": row.get("country"),
            "spend": to_float(row.get("spend")),
            "impressions": to_int(row.get("impressions")),
            "clicks": to_int(row.get("clicks")),
            "reach": to_int(row.get("reach")),
            "results": get_results(row.get("actions", []))
        })

    return output


def upsert_rows(rows):
    if not rows:
        return 0

    total = 0

    for batch in chunk_list(rows):
        result = (
            supabase.table(TABLE_NAME)
            .upsert(
                batch,
                on_conflict="perf_date,client_id,platform,account_id,campaign_id,adset_id,ad_id,country"
            )
            .execute()
        )

        total += len(result.data or [])

    return total


def pull_account_for_date(meta_account, target_date):
    raw_rows = fetch_meta_data(meta_account, target_date)
    final_rows = normalize_rows(meta_account, raw_rows)

    print(f"Rows fetched: {len(raw_rows)}")
    print(f"Rows prepared: {len(final_rows)}")

    upserted = upsert_rows(final_rows)

    print(f"Rows upserted: {upserted}")

    return upserted


def mark_backfill_done(meta_account_id):
    supabase.table("meta_accounts").update({
        "backfill_done": True
    }).eq("id", meta_account_id).execute()


def run_backfill(days=90):
    accounts = get_accounts_for_backfill()

    print(f"Accounts pending backfill: {len(accounts)}")

    today = date.today()
    start_date = today - timedelta(days=days)
    end_date = today - timedelta(days=1)

    for account in accounts:
        print(f"Starting backfill for account: {account.get('ad_account_name')}")

        current = start_date
        total_upserted = 0

        while current <= end_date:
            target_date = current.isoformat()

            try:
                upserted = pull_account_for_date(account, target_date)
                total_upserted += upserted
            except Exception as e:
                print(f"Error on {target_date}: {e}")
                raise e

            current += timedelta(days=1)

        mark_backfill_done(account["id"])

        print(f"Backfill complete for account: {account.get('ad_account_name')}")
        print(f"Total rows upserted: {total_upserted}")


def run_daily():
    accounts = get_accounts_for_daily()

    print(f"Accounts ready for daily sync: {len(accounts)}")

    yesterday = date.today() - timedelta(days=1)
    target_date = yesterday.isoformat()

    total_upserted = 0

    for account in accounts:
        upserted = pull_account_for_date(account, target_date)
        total_upserted += upserted

    print(f"Daily sync complete for {target_date}")
    print(f"Total rows upserted: {total_upserted}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"

    if mode == "backfill":
        run_backfill(days=90)

    elif mode == "daily":
        run_daily()

    else:
        raise Exception("Invalid mode. Use: backfill or daily")
