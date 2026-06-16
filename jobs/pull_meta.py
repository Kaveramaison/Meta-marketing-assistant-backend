import os
from datetime import datetime, timedelta
from collections import defaultdict
from dotenv import load_dotenv

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from supabase import create_client, Client

load_dotenv()

META_APP_ID = os.getenv("META_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

TABLE_NAME = "meta_performance"
USD_PER_AED_DIVISOR = 3.67
BATCH_SIZE = 500

if not all([META_APP_ID, META_APP_SECRET]):
    raise ValueError("Missing META_APP_ID or META_APP_SECRET")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def _to_float(val, default=0.0):
    try:
        if val in (None, "", "null"):
            return default
        return float(val)
    except Exception:
        return default


def _to_int(val, default=0):
    try:
        if val in (None, "", "null"):
            return default
        return int(float(val))
    except Exception:
        return default


def _extract_leads(row):
    actions = row.get("actions", []) or []

    for action in actions:
        if action.get("action_type") in [
            "lead",
            "onsite_conversion.lead_grouped",
            "leadgen.other"
        ]:
            return _to_int(action.get("value", 0))

    return 0


def _aed_to_usd(val):
    return round(_to_float(val) / USD_PER_AED_DIVISOR, 2)


def get_active_meta_account():
    result = (
        supabase.table("meta_accounts")
        .select("*")
        .eq("is_active", True)
        .limit(1)
        .execute()
    )

    if not result.data:
        raise ValueError("No active Meta account found in Supabase")

    account = result.data[0]

    if not account.get("access_token"):
        raise ValueError("Missing access_token in meta_accounts")

    if not account.get("ad_account_id"):
        raise ValueError("Missing ad_account_id in meta_accounts")

    return account


def normalize_row(row, client_id, meta_account_id):
    return {
        "client_id": client_id,
        "meta_account_id": meta_account_id,
        "perf_date": row.get("date_start"),
        "country": row.get("country"),
        "level": "ad",
        "campaign_id": str(row.get("campaign_id")) if row.get("campaign_id") else None,
        "campaign_name": row.get("campaign_name"),
        "adset_id": str(row.get("adset_id")) if row.get("adset_id") else None,
        "adset_name": row.get("adset_name"),
        "ad_id": str(row.get("ad_id")) if row.get("ad_id") else None,
        "ad_name": row.get("ad_name"),
        "impressions": _to_int(row.get("impressions")),
        "clicks": _to_int(row.get("clicks")),
        "reach": _to_int(row.get("reach")),
        "leads": _extract_leads(row),
        "spend_usd": _aed_to_usd(row.get("spend")),
    }


def is_valid_row(row):
    return bool(
        row.get("perf_date")
        and row.get("campaign_id")
        and row.get("campaign_name")
        and row.get("country")
    )


def row_key(row):
    return (
        row.get("client_id"),
        row.get("meta_account_id"),
        row.get("perf_date"),
        row.get("country"),
        "ad",
        row.get("campaign_id"),
        row.get("adset_id"),
        row.get("ad_id"),
    )


def fetch_data_for_date(account, date_str, client_id, meta_account_id):
    params = {
        "level": "ad",
        "time_range": {
            "since": date_str,
            "until": date_str
        },
        "breakdowns": ["country"],
        "limit": 500
    }

    insights = account.get_insights(
        fields=[
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
            "actions",
            "date_start"
        ],
        params=params
    )

    return [
        normalize_row(raw, client_id, meta_account_id)
        for raw in insights
    ]


def aggregate_rows(rows):
    agg = defaultdict(lambda: {
        "client_id": None,
        "meta_account_id": None,
        "perf_date": None,
        "country": None,
        "level": "ad",
        "campaign_id": None,
        "campaign_name": None,
        "adset_id": None,
        "adset_name": None,
        "ad_id": None,
        "ad_name": None,
        "impressions": 0,
        "clicks": 0,
        "reach": 0,
        "leads": 0,
        "spend_usd": 0.0,
    })

    skipped = 0

    for row in rows:
        if not is_valid_row(row):
            skipped += 1
            continue

        key = row_key(row)
        entry = agg[key]

        for field in [
            "client_id",
            "meta_account_id",
            "perf_date",
            "country",
            "campaign_id",
            "campaign_name",
            "adset_id",
            "adset_name",
            "ad_id",
            "ad_name",
        ]:
            entry[field] = row[field]

        entry["level"] = "ad"
        entry["impressions"] += row["impressions"]
        entry["clicks"] += row["clicks"]
        entry["reach"] += row["reach"]
        entry["leads"] += row["leads"]
        entry["spend_usd"] = round(entry["spend_usd"] + row["spend_usd"], 2)

    return list(agg.values()), skipped


def chunk_list(items, chunk_size=BATCH_SIZE):
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]


def run_meta_pull(from_date: str, to_date: str):
    meta_account = get_active_meta_account()

    client_id = meta_account["client_id"]
    meta_account_id = meta_account["id"]
    access_token = meta_account["access_token"]
    ad_account_id = meta_account["ad_account_id"]

    FacebookAdsApi.init(
        app_id=META_APP_ID,
        app_secret=META_APP_SECRET,
        access_token=access_token
    )

    account = AdAccount(ad_account_id)

    start_date = datetime.strptime(from_date, "%Y-%m-%d")
    end_date = datetime.strptime(to_date, "%Y-%m-%d")

    all_raw_rows = []
    current_date = start_date

    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        daily_rows = fetch_data_for_date(
            account=account,
            date_str=date_str,
            client_id=client_id,
            meta_account_id=meta_account_id
        )
        all_raw_rows.extend(daily_rows)
        current_date += timedelta(days=1)

    final_rows, skipped_rows = aggregate_rows(all_raw_rows)

    if not final_rows:
        return {
            "status": "no_data",
            "from_date": from_date,
            "to_date": to_date,
            "raw_rows": len(all_raw_rows),
            "clean_rows": 0,
            "skipped_rows": skipped_rows,
            "upserted": 0
        }

    total_upserted = 0

    for batch in chunk_list(final_rows):
        supabase.table(TABLE_NAME).upsert(
            batch,
            on_conflict="client_id,meta_account_id,perf_date,country,level,campaign_id,adset_id,ad_id"
        ).execute()

        total_upserted += len(batch)

    return {
        "status": "success",
        "from_date": from_date,
        "to_date": to_date,
        "raw_rows": len(all_raw_rows),
        "clean_rows": len(final_rows),
        "skipped_rows": skipped_rows,
        "upserted": total_upserted
    }
