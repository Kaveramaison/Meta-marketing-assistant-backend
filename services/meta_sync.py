import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from core.config import settings
from core.supabase_client import get_supabase

PERFORMANCE_TABLE = "marketing_performance_daily"
BATCH_SIZE = 500

LEAD_ACTION_TYPES = {
    "lead",
    "onsite_conversion.lead_grouped",
    "offsite_complete_registration_add_meta_leads",
    "leadgen.other",
}
LINK_CLICK_ACTION_TYPES = {"link_click", "inline_link_click"}
PURCHASE_ACTION_TYPES = {
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "onsite_conversion.purchase",
}


def to_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def round_or_none(value, digits=4):
    if value is None:
        return None
    return round(value, digits)


def action_total(actions, action_types: set[str]) -> int:
    return sum(to_int(a.get("value")) for a in (actions or []) if a.get("action_type") in action_types)


def chunk_list(items, size=BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def app_today() -> date:
    return datetime.now(ZoneInfo(settings.default_timezone)).date()


def graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_api_version}/{path.lstrip('/')}"


def normalize_ad_account_id(ad_account_id: str) -> str:
    return ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"


def supabase():
    return get_supabase()


def get_accounts(backfill: bool):
    result = (
        supabase()
        .table("meta_accounts")
        .select("id, client_id, ad_account_id, ad_account_name, access_token, backfill_done, is_active")
        .eq("is_active", True)
        .eq("backfill_done", not backfill)
        .execute()
    )
    return result.data or []


def get_scheduled_accounts():
    result = (
        supabase()
        .table("meta_accounts")
        .select(
            "id, client_id, ad_account_id, ad_account_name, access_token, backfill_done, "
            "is_active, sync_frequency_hours, last_synced_at"
        )
        .eq("is_active", True)
        .eq("backfill_done", True)
        .execute()
    )
    return result.data or []


def parse_supabase_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def account_is_due(account, now: datetime) -> tuple[bool, str]:
    last_synced_at = parse_supabase_datetime(account.get("last_synced_at"))
    if last_synced_at is None:
        return True, "never_synced"

    frequency_hours = max(to_int(account.get("sync_frequency_hours")) or 24, 1)
    next_sync_at = last_synced_at + timedelta(hours=frequency_hours)
    if now >= next_sync_at:
        return True, "frequency_elapsed"

    return False, f"next_sync_at={next_sync_at.isoformat()}"


def create_sync_run(account, sync_type: str, start_date: date, end_date: date):
    payload = {
        "client_id": account.get("client_id"),
        "platform": "meta",
        "account_id": account.get("ad_account_id"),
        "sync_type": sync_type,
        "status": "running",
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "started_at": datetime.utcnow().isoformat(),
        "metadata": {"timezone": settings.default_timezone},
    }
    result = supabase().table("sync_runs").insert(payload).execute()
    rows = result.data or []
    return rows[0]["id"] if rows else None


def finish_sync_run(sync_run_id, status: str, counters: dict, error_message: str | None = None):
    if not sync_run_id:
        return
    payload = {
        "status": status,
        "finished_at": datetime.utcnow().isoformat(),
        "rows_fetched": counters.get("rows_fetched", 0),
        "rows_inserted": counters.get("rows_inserted", 0),
        "rows_updated": counters.get("rows_updated", 0),
        "error_message": error_message,
        "metadata": counters.get("metadata", {}),
    }
    supabase().table("sync_runs").update(payload).eq("id", sync_run_id).execute()


def fetch_meta_insights(meta_account, target_date: date):
    url = graph_url(f"{normalize_ad_account_id(meta_account['ad_account_id'])}/insights")
    params = {
        "access_token": meta_account["access_token"],
        "level": "ad",
        "time_range": json.dumps({"since": target_date.isoformat(), "until": target_date.isoformat()}),
        "breakdowns": "country",
        "fields": ",".join([
            "date_start", "campaign_id", "campaign_name", "adset_id", "adset_name",
            "ad_id", "ad_name", "impressions", "clicks", "reach", "frequency",
            "spend", "ctr", "cpc", "actions",
        ]),
        "limit": 500,
    }
    rows = []
    while url:
        response = requests.get(url, params=params, timeout=60)
        data = response.json()
        if response.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Meta API failed: {json.dumps(data)}")
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = None
    return rows


def normalize_performance_rows(meta_account, rows):
    output = []
    for row in rows:
        spend = to_float(row.get("spend"))
        impressions = to_int(row.get("impressions"))
        clicks = to_int(row.get("clicks"))
        reach = to_int(row.get("reach"))
        results = action_total(row.get("actions"), LEAD_ACTION_TYPES)
        link_clicks = action_total(row.get("actions"), LINK_CLICK_ACTION_TYPES) or clicks
        purchases = action_total(row.get("actions"), PURCHASE_ACTION_TYPES)
        ctr = to_float(row.get("ctr")) if row.get("ctr") is not None else ((clicks / impressions) * 100 if impressions else None)
        cpc = to_float(row.get("cpc")) if row.get("cpc") is not None else (spend / clicks if clicks else None)
        frequency = to_float(row.get("frequency")) if row.get("frequency") is not None else (impressions / reach if reach else None)
        output.append({
            "perf_date": row.get("date_start"),
            "client_id": meta_account["client_id"],
            "platform": "meta",
            "account_id": meta_account["ad_account_id"].replace("act_", ""),
            "account_name": meta_account.get("ad_account_name"),
            "campaign_id": row.get("campaign_id"),
            "campaign_name": row.get("campaign_name"),
            "adset_id": row.get("adset_id"),
            "adset_name": row.get("adset_name"),
            "ad_id": row.get("ad_id"),
            "ad_name": row.get("ad_name"),
            "country": row.get("country"),
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
            "reach": reach,
            "frequency": round_or_none(frequency),
            "link_clicks": link_clicks,
            "ctr": round_or_none(ctr),
            "cpc": round_or_none(cpc),
            "results": results,
            "cpl": round_or_none(spend / results) if results > 0 else None,
            "purchases": purchases,
            "revenue": 0,
        })
    return output


def upsert_rows(table_name: str, rows: list[dict], on_conflict: str) -> int:
    if not rows:
        return 0
    total = 0
    for batch in chunk_list(rows):
        result = supabase().table(table_name).upsert(batch, on_conflict=on_conflict).execute()
        total += len(result.data or [])
    return total


def upsert_dimensions(rows: list[dict]) -> dict:
    campaigns, ad_sets, ads = {}, {}, {}
    for row in rows:
        if row.get("campaign_id"):
            campaigns[(row["client_id"], row["platform"], row["account_id"], row["campaign_id"])] = {
                "client_id": row["client_id"], "platform": row["platform"], "account_id": row["account_id"],
                "campaign_id": row["campaign_id"], "campaign_name": row.get("campaign_name"),
            }
        if row.get("campaign_id") and row.get("adset_id"):
            ad_sets[(row["client_id"], row["platform"], row["account_id"], row["adset_id"])] = {
                "client_id": row["client_id"], "platform": row["platform"], "account_id": row["account_id"],
                "campaign_id": row["campaign_id"], "adset_id": row["adset_id"], "adset_name": row.get("adset_name"),
            }
        if row.get("campaign_id") and row.get("adset_id") and row.get("ad_id"):
            ads[(row["client_id"], row["platform"], row["account_id"], row["ad_id"])] = {
                "client_id": row["client_id"], "platform": row["platform"], "account_id": row["account_id"],
                "campaign_id": row["campaign_id"], "adset_id": row["adset_id"], "ad_id": row["ad_id"],
                "ad_name": row.get("ad_name"), "creative_id": row.get("creative_id"),
            }
    return {
        "campaigns": upsert_rows("campaigns", list(campaigns.values()), "client_id,platform,account_id,campaign_id"),
        "ad_sets": upsert_rows("ad_sets", list(ad_sets.values()), "client_id,platform,account_id,adset_id"),
        "ads": upsert_rows("ads", list(ads.values()), "client_id,platform,account_id,ad_id"),
    }


def sync_account_for_date(account, target_date: date) -> dict:
    raw_rows = fetch_meta_insights(account, target_date)
    rows = normalize_performance_rows(account, raw_rows)
    performance_count = upsert_rows(PERFORMANCE_TABLE, rows, "perf_date,client_id,platform,account_id,campaign_id,adset_id,ad_id,country")
    return {"date": target_date.isoformat(), "rows_fetched": len(raw_rows), "performance_rows": performance_count, "dimensions": upsert_dimensions(rows)}


def sync_account_window(account, sync_type: str, start_date: date, end_date: date) -> dict:
    sync_run_id = create_sync_run(account, sync_type, start_date, end_date)
    counters = {"rows_fetched": 0, "rows_inserted": 0, "rows_updated": 0, "metadata": {"dates": []}}
    try:
        current = start_date
        while current <= end_date:
            result = sync_account_for_date(account, current)
            counters["rows_fetched"] += result["rows_fetched"]
            counters["rows_updated"] += result["performance_rows"]
            counters["metadata"]["dates"].append(result)
            current += timedelta(days=1)
        supabase().table("meta_accounts").update({"last_synced_at": datetime.utcnow().isoformat()}).eq("id", account["id"]).execute()
        if sync_type == "backfill":
            supabase().table("meta_accounts").update({"backfill_done": True}).eq("id", account["id"]).execute()
        finish_sync_run(sync_run_id, "success", counters)
        return {"account_id": account["ad_account_id"], "status": "success", **counters}
    except Exception as exc:
        finish_sync_run(sync_run_id, "failed", counters, str(exc))
        raise


def run_daily_sync() -> dict:
    from services.insights import generate_basic_insights
    accounts = get_accounts(backfill=False)
    today = app_today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=max(settings.daily_lookback_days - 1, 0))
    results = []
    for account in accounts:
        result = sync_account_window(account, "daily", start_date, end_date)
        result["insights"] = generate_basic_insights(account.get("client_id"))
        results.append(result)
    return {"mode": "daily", "timezone": settings.default_timezone, "date_from": start_date.isoformat(), "date_to": end_date.isoformat(), "accounts": len(accounts), "results": results}


def run_backfill_sync(days: int | None = None) -> dict:
    from services.insights import generate_basic_insights
    accounts = get_accounts(backfill=True)
    today = app_today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=max((days or settings.backfill_days) - 1, 0))
    results = []
    for account in accounts:
        result = sync_account_window(account, "backfill", start_date, end_date)
        result["insights"] = generate_basic_insights(account.get("client_id"))
        results.append(result)
    return {"mode": "backfill", "timezone": settings.default_timezone, "date_from": start_date.isoformat(), "date_to": end_date.isoformat(), "accounts": len(accounts), "results": results}


def run_scheduled_sync() -> dict:
    from services.insights import generate_basic_insights

    accounts = get_scheduled_accounts()
    now = datetime.now(timezone.utc)
    today = app_today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=max(settings.daily_lookback_days - 1, 0))

    results = []
    skipped = []

    for account in accounts:
        is_due, reason = account_is_due(account, now)
        if not is_due:
            skipped.append({
                "account_id": account.get("ad_account_id"),
                "account_name": account.get("ad_account_name"),
                "reason": reason,
                "sync_frequency_hours": account.get("sync_frequency_hours") or 24,
                "last_synced_at": account.get("last_synced_at"),
            })
            continue

        result = sync_account_window(account, "scheduled", start_date, end_date)
        result["reason"] = reason
        result["sync_frequency_hours"] = account.get("sync_frequency_hours") or 24
        result["insights"] = generate_basic_insights(account.get("client_id"))
        results.append(result)

    return {
        "mode": "scheduled",
        "timezone": settings.default_timezone,
        "checked_at": now.isoformat(),
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "accounts_checked": len(accounts),
        "accounts_synced": len(results),
        "accounts_skipped": len(skipped),
        "results": results,
        "skipped": skipped,
    }
