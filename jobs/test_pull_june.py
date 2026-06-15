import os
import requests
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def pull_meta_data(ad_account_id, access_token):
    url = f"https://graph.facebook.com/v21.0/{ad_account_id}/insights"

    params = {
        "access_token": access_token,
        "level": "campaign",
        "time_range": '{"since":"2026-06-01","until":"2026-06-10"}',
        "time_increment": 1,
        "fields": "date_start,date_stop,account_id,account_name,campaign_id,campaign_name,spend,impressions,clicks,reach,actions"
    }

    response = requests.get(url, params=params)
    print(response.status_code)
    print(response.text)

def run():
    accounts = supabase.table("meta_accounts").select("*").eq("is_active", True).execute().data

    for acc in accounts:
        pull_meta_data(acc["ad_account_id"], acc["access_token"])

if __name__ == "__main__":
    run()
