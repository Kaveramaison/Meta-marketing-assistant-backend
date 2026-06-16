import os
import json
import requests
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

result = (
    supabase.table("meta_accounts")
    .select("id, client_id, ad_account_id, ad_account_name, access_token")
    .limit(1)
    .execute()
)

if not result.data:
    raise Exception("No Meta account found in meta_accounts")

meta_account = result.data[0]

client_id = meta_account["client_id"]
access_token = meta_account["access_token"]
raw_ad_account_id = meta_account["ad_account_id"]
ad_account_id = f"act_{raw_ad_account_id}"
ad_account_name = meta_account.get("ad_account_name")

print("Using ad account:", ad_account_id)
print("Ad account name:", ad_account_name)

url = f"https://graph.facebook.com/v20.0/{ad_account_id}/insights"

params = {
    "access_token": access_token,
    "level": "ad",
    "time_range": json.dumps({
        "since": "2026-06-01",
        "until": "2026-06-01"
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
    "limit": 100
}

response = requests.get(url, params=params)
data = response.json()

if "error" in data:
    print("Meta API Error:")
    print(json.dumps(data, indent=2))
    raise Exception("Meta API failed")

rows = data.get("data", [])

def get_results(actions):
    if not actions:
        return 0

    for action in actions:
        if action.get("action_type") in [
            "lead",
            "onsite_conversion.lead_grouped",
            "offsite_complete_registration_add_meta_leads"
        ]:
            return int(float(action.get("value", 0)))

    return 0

insert_rows = []

for row in rows:
    insert_rows.append({
        "perf_date": row.get("date_start"),
        "client_id": client_id,
        "platform": "meta",
        "account_id": raw_ad_account_id,
        "account_name": ad_account_name,
        "campaign_id": row.get("campaign_id"),
        "campaign_name": row.get("campaign_name"),
        "adset_id": row.get("adset_id"),
        "adset_name": row.get("adset_name"),
        "ad_id": row.get("ad_id"),
        "ad_name": row.get("ad_name"),
        "country": row.get("country"),
        "spend": float(row.get("spend", 0)),
        "impressions": int(row.get("impressions", 0)),
        "clicks": int(row.get("clicks", 0)),
        "reach": int(row.get("reach", 0)),
        "results": get_results(row.get("actions", []))
    })

print("Rows fetched:", len(rows))
print("Rows prepared for Supabase:", len(insert_rows))

if insert_rows:
    db_result = (
        supabase.table("marketing_performance")
        .insert(insert_rows)
        .execute()
    )

    print("Inserted rows:", len(db_result.data))

total_spend = sum(r["spend"] for r in insert_rows)
total_clicks = sum(r["clicks"] for r in insert_rows)
total_impressions = sum(r["impressions"] for r in insert_rows)
total_results = sum(r["results"] for r in insert_rows)

print("\nTOTALS")
print("Spend:", total_spend)
print("Clicks:", total_clicks)
print("Impressions:", total_impressions)
print("Results:", total_results)
