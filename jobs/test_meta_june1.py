import os
import json
import requests
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 1. Get Meta account from Supabase
result = (
    supabase.table("meta_accounts")
    .select("id, client_id, ad_account_id, ad_account_name, access_token")
    .limit(1)
    .execute()
)

if not result.data:
    raise Exception("No Meta account found in meta_accounts")

meta_account = result.data[0]

access_token = meta_account["access_token"]
ad_account_id = meta_account["ad_account_id"]

print("Using ad account:", ad_account_id)
print("Ad account name:", meta_account.get("ad_account_name"))

# 2. Pull June 1 granular Meta data
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
        "country",
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

print("\nRows fetched:", len(rows))
print("\nSample rows:")
print(json.dumps(rows[:5], indent=2))

# 3. Totals
total_spend = sum(float(r.get("spend", 0)) for r in rows)
total_clicks = sum(int(r.get("clicks", 0)) for r in rows)
total_impressions = sum(int(r.get("impressions", 0)) for r in rows)

print("\nTOTALS")
print("Spend:", total_spend)
print("Clicks:", total_clicks)
print("Impressions:", total_impressions)
