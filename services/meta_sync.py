import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from core.config import settings
from core.supabase_client import get_supabase

PERFORMANCE_TABLE = "marketing_performance_daily"
BREAKDOWN_TABLE = "marketing_performance_breakdowns"
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
COUNTRY_BREAKDOWN = "country"
BREAKDOWN_CONFIGS = {
    "placement": "publisher_platform,platform_position",
    "demographic": "age,gender",
    "device": "impression_device",
    "hourly": "hourly_stats_aggregated_by_advertiser_time_zone",
    "geo_region": "region",
    "geo_city": "region,city",
    "geo_dma": "dma",
}
DEFAULT_BREAKDOWNS = ["placement", "device", "demographic", "hourly"]
GEO_GRAIN_BREAKDOWNS = {
    "country": [],
    "region": ["geo_region"],
    "city": ["geo_city"],
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


def action_value_total(actions, action_types: set[str]) -> float:
    return sum(to_float(a.get("value")) for a in (actions or []) if a.get("action_type") in action_types)


def chunk_list(items, size=BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def app_today() -> date:
    return datetime.now(ZoneInfo(settings.default_timezone)).date()


def graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_api_version}/{path.lstrip('/')}"


def normalize_ad_account_id(ad_account_id: str) -> str:
    return ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"


def normalize_account_id(ad_account_id: str) -> str:
    return ad_account_id.replace("act_", "")


def supabase():
    return get_supabase()


def get_accounts(backfill: bool):
    result = (
        supabase()
        .table("meta_accounts")
        .select(
            "id, client_id, ad_account_id, ad_account_name, access_token, backfill_done, "
            "is_active, geo_grain, enabled_breakdowns"
        )
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
            "is_active, sync_frequency_hours, last_synced_at, geo_grain, enabled_breakdowns"
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


def insight_fields() -> str:
    return ",".join([
        "date_start", "campaign_id", "campaign_name", "adset_id", "adset_name",
        "ad_id", "ad_name", "impressions", "clicks", "reach", "frequency",
        "spend", "ctr", "cpc", "actions", "action_values",
    ])


def fetch_meta_insights(meta_account, target_date: date, breakdowns: str = COUNTRY_BREAKDOWN):
    url = graph_url(f"{normalize_ad_account_id(meta_account['ad_account_id'])}/insights")
    params = {
        "access_token": meta_account["access_token"],
        "level": "ad",
        "time_range": json.dumps({"since": target_date.isoformat(), "until": target_date.isoformat()}),
        "breakdowns": breakdowns,
        "fields": insight_fields(),
        "limit": 500,
    }
    return fetch_meta_insights_with_params(url, params)


def fetch_meta_insights_with_params(url: str, params: dict):
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


def enabled_breakdowns_for_account(meta_account) -> list[str]:
    enabled = meta_account.get("enabled_breakdowns")
    if isinstance(enabled, str):
        try:
            enabled = json.loads(enabled)
        except json.JSONDecodeError:
            enabled = None
    if not isinstance(enabled, list):
        enabled = DEFAULT_BREAKDOWNS

    geo_grain = (meta_account.get("geo_grain") or "country").lower()
    requested = [*enabled, *GEO_GRAIN_BREAKDOWNS.get(geo_grain, [])]
    return [
        breakdown_type
        for breakdown_type in dict.fromkeys(requested)
        if breakdown_type in BREAKDOWN_CONFIGS
    ]


def fetch_meta_edge(meta_account, edge: str, fields: list[str], ids: set[str] | None = None):
    url = graph_url(f"{normalize_ad_account_id(meta_account['ad_account_id'])}/{edge}")
    params = {
        "access_token": meta_account["access_token"],
        "fields": ",".join(fields),
        "limit": 500,
    }
    if ids:
        params["filtering"] = json.dumps([{"field": "id", "operator": "IN", "value": sorted(ids)}])

    rows = []
    while url:
        response = requests.get(url, params=params, timeout=60)
        data = response.json()
        if response.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Meta API failed for {edge}: {json.dumps(data)}")
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = None
    return rows


def nested_value(payload: dict, *path):
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_asset_text(asset_feed_spec: dict, key: str, value_key: str = "text") -> str | None:
    items = asset_feed_spec.get(key) or []
    if not items:
        return None
    first = items[0] if isinstance(items[0], dict) else {}
    return first.get(value_key)


def creative_from_ad(ad: dict) -> dict | None:
    creative = ad.get("creative") or {}
    creative_id = creative.get("id")
    if not creative_id:
        return None

    story_spec = creative.get("object_story_spec") or {}
    link_data = story_spec.get("link_data") or {}
    video_data = story_spec.get("video_data") or {}
    asset_feed_spec = creative.get("asset_feed_spec") or {}
    call_to_action = (
        creative.get("call_to_action_type")
        or nested_value(link_data, "call_to_action", "type")
        or nested_value(video_data, "call_to_action", "type")
        or first_asset_text(asset_feed_spec, "call_to_action_types", "type")
    )

    return {
        "creative_id": creative_id,
        "name": creative.get("name"),
        "title": creative.get("title") or link_data.get("name") or video_data.get("title") or first_asset_text(asset_feed_spec, "titles"),
        "body": creative.get("body") or link_data.get("message") or video_data.get("message") or first_asset_text(asset_feed_spec, "bodies"),
        "call_to_action": call_to_action,
        "image_url": creative.get("image_url") or link_data.get("image_url"),
        "video_id": creative.get("video_id") or video_data.get("video_id"),
        "thumbnail_url": creative.get("thumbnail_url"),
        "asset_type": creative.get("object_type"),
        "raw_payload": creative,
    }


def fetch_ads_metadata(meta_account, ad_ids: set[str]):
    if not ad_ids:
        return []

    base_fields = [
        "id", "name", "campaign_id", "adset_id", "status", "effective_status",
        "updated_time",
    ]
    creative_fields = "creative{id,name,title,body,call_to_action_type,image_url,thumbnail_url,video_id,object_type,object_story_spec}"

    try:
        return fetch_meta_edge(meta_account, "ads", [
            *base_fields,
            creative_fields.replace("}", ",asset_feed_spec}"),
        ], ad_ids)
    except Exception:
        return fetch_meta_edge(meta_account, "ads", [*base_fields, creative_fields], ad_ids)


def fetch_dimension_metadata(meta_account, rows):
    campaign_ids = {row.get("campaign_id") for row in rows if row.get("campaign_id")}
    adset_ids = {row.get("adset_id") for row in rows if row.get("adset_id")}
    ad_ids = {row.get("ad_id") for row in rows if row.get("ad_id")}

    campaigns = fetch_meta_edge(meta_account, "campaigns", [
        "id", "name", "objective", "status", "effective_status", "buying_type",
        "daily_budget", "lifetime_budget", "start_time", "stop_time", "created_time", "updated_time",
    ], campaign_ids) if campaign_ids else []

    ad_sets = fetch_meta_edge(meta_account, "adsets", [
        "id", "name", "campaign_id", "status", "effective_status", "optimization_goal",
        "billing_event", "bid_strategy", "daily_budget", "lifetime_budget", "targeting",
        "start_time", "end_time", "created_time", "updated_time",
    ], adset_ids) if adset_ids else []

    ads = fetch_ads_metadata(meta_account, ad_ids)

    return {
        "campaigns": campaigns,
        "ad_sets": ad_sets,
        "ads": ads,
        "creatives": [creative for ad in ads if (creative := creative_from_ad(ad))],
    }


def placement_value(row: dict) -> str:
    publisher = row.get("publisher_platform")
    position = row.get("platform_position")
    if publisher and position:
        return f"{publisher}:{position}"
    return publisher or position or "all"


def metric_payload(meta_account, row: dict, ad_creatives: dict[str, str] | None = None) -> dict:
    ad_creatives = ad_creatives or {}
    spend = to_float(row.get("spend"))
    impressions = to_int(row.get("impressions"))
    clicks = to_int(row.get("clicks"))
    reach = to_int(row.get("reach"))
    results = action_total(row.get("actions"), LEAD_ACTION_TYPES)
    link_clicks = action_total(row.get("actions"), LINK_CLICK_ACTION_TYPES) or clicks
    purchases = action_total(row.get("actions"), PURCHASE_ACTION_TYPES)
    revenue = action_value_total(row.get("action_values"), PURCHASE_ACTION_TYPES)
    ctr = to_float(row.get("ctr")) if row.get("ctr") is not None else ((clicks / impressions) * 100 if impressions else None)
    cpc = to_float(row.get("cpc")) if row.get("cpc") is not None else (spend / clicks if clicks else None)
    frequency = to_float(row.get("frequency")) if row.get("frequency") is not None else (impressions / reach if reach else None)
    ad_id = row.get("ad_id")

    return {
        "perf_date": row.get("date_start"),
        "client_id": meta_account["client_id"],
        "platform": "meta",
        "account_id": normalize_account_id(meta_account["ad_account_id"]),
        "account_name": meta_account.get("ad_account_name"),
        "campaign_id": row.get("campaign_id") or "all",
        "campaign_name": row.get("campaign_name"),
        "adset_id": row.get("adset_id") or "all",
        "adset_name": row.get("adset_name"),
        "ad_id": ad_id or "all",
        "ad_name": row.get("ad_name"),
        "creative_id": ad_creatives.get(ad_id),
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
        "revenue": round_or_none(revenue, 2),
    }


def normalize_performance_rows(meta_account, rows, ad_creatives: dict[str, str] | None = None):
    output = []
    for row in rows:
        metrics = metric_payload(meta_account, row, ad_creatives)
        output.append({
            **metrics,
            "campaign_id": row.get("campaign_id"),
            "adset_id": row.get("adset_id"),
            "ad_id": row.get("ad_id"),
            "country": row.get("country"),
            "placement": placement_value(row),
        })
    return output


def normalize_breakdown_rows(meta_account, rows, breakdown_type: str, ad_creatives: dict[str, str] | None = None):
    output = []
    for row in rows:
        publisher_platform = row.get("publisher_platform") or "all"
        platform_position = row.get("platform_position") or "all"
        output.append({
            **metric_payload(meta_account, row, ad_creatives),
            "breakdown_type": breakdown_type,
            "country": row.get("country") or "all",
            "region": row.get("region") or "all",
            "city": row.get("city") or "all",
            "dma": row.get("dma") or "all",
            "publisher_platform": publisher_platform,
            "platform_position": platform_position,
            "placement": (
                f"{publisher_platform}:{platform_position}"
                if publisher_platform != "all" or platform_position != "all"
                else "all"
            ),
            "age": row.get("age") or "all",
            "gender": row.get("gender") or "all",
            "impression_device": row.get("impression_device") or "all",
            "hourly_window": row.get("hourly_stats_aggregated_by_advertiser_time_zone") or "all",
            "raw_payload": row,
        })
    return output


def sync_breakdown_rows(meta_account, target_date: date, ad_creatives: dict[str, str]) -> tuple[dict, list[dict]]:
    results = {}
    errors = []
    for breakdown_type in enabled_breakdowns_for_account(meta_account):
        breakdowns = BREAKDOWN_CONFIGS[breakdown_type]
        try:
            raw_rows = fetch_meta_insights(meta_account, target_date, breakdowns)
            rows = normalize_breakdown_rows(meta_account, raw_rows, breakdown_type, ad_creatives)
            saved = upsert_rows(
                BREAKDOWN_TABLE,
                rows,
                (
                    "perf_date,client_id,platform,account_id,campaign_id,adset_id,ad_id,"
                    "breakdown_type,country,region,city,dma,publisher_platform,platform_position,"
                    "age,gender,impression_device,hourly_window"
                ),
            )
            results[breakdown_type] = {"rows_fetched": len(raw_rows), "rows_saved": saved}
        except Exception as exc:
            errors.append({"breakdown_type": breakdown_type, "error": str(exc)})
            results[breakdown_type] = {"rows_fetched": 0, "rows_saved": 0, "error": str(exc)}
    return results, errors


def upsert_rows(table_name: str, rows: list[dict], on_conflict: str) -> int:
    if not rows:
        return 0
    total = 0
    for batch in chunk_list(rows):
        result = supabase().table(table_name).upsert(batch, on_conflict=on_conflict).execute()
        total += len(result.data or [])
    return total


def upsert_dimensions(rows: list[dict], metadata: dict | None = None) -> dict:
    campaigns, ad_sets, ads = {}, {}, {}
    creatives = {}
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

    account_id = normalize_account_id(metadata.get("account_id")) if metadata else None
    client_id = metadata.get("client_id") if metadata else None

    for campaign in (metadata or {}).get("campaigns", []):
        campaigns[(client_id, "meta", account_id, campaign["id"])] = {
            "client_id": client_id, "platform": "meta", "account_id": account_id,
            "campaign_id": campaign["id"], "campaign_name": campaign.get("name"),
            "objective": campaign.get("objective"), "status": campaign.get("status"),
            "effective_status": campaign.get("effective_status"), "buying_type": campaign.get("buying_type"),
            "daily_budget": campaign.get("daily_budget"), "lifetime_budget": campaign.get("lifetime_budget"),
            "start_time": campaign.get("start_time"), "stop_time": campaign.get("stop_time"),
            "created_time": campaign.get("created_time"), "updated_time": campaign.get("updated_time"),
            "raw_payload": campaign,
        }

    for ad_set in (metadata or {}).get("ad_sets", []):
        ad_sets[(client_id, "meta", account_id, ad_set["id"])] = {
            "client_id": client_id, "platform": "meta", "account_id": account_id,
            "campaign_id": ad_set.get("campaign_id"), "adset_id": ad_set["id"], "adset_name": ad_set.get("name"),
            "status": ad_set.get("status"), "effective_status": ad_set.get("effective_status"),
            "optimization_goal": ad_set.get("optimization_goal"), "billing_event": ad_set.get("billing_event"),
            "bid_strategy": ad_set.get("bid_strategy"), "daily_budget": ad_set.get("daily_budget"),
            "lifetime_budget": ad_set.get("lifetime_budget"), "targeting": ad_set.get("targeting"),
            "start_time": ad_set.get("start_time"), "end_time": ad_set.get("end_time"),
            "created_time": ad_set.get("created_time"), "updated_time": ad_set.get("updated_time"),
            "raw_payload": ad_set,
        }

    for ad in (metadata or {}).get("ads", []):
        creative = creative_from_ad(ad)
        ads[(client_id, "meta", account_id, ad["id"])] = {
            "client_id": client_id, "platform": "meta", "account_id": account_id,
            "campaign_id": ad.get("campaign_id"), "adset_id": ad.get("adset_id"), "ad_id": ad["id"],
            "ad_name": ad.get("name"), "creative_id": creative.get("creative_id") if creative else None,
            "status": ad.get("status"), "effective_status": ad.get("effective_status"),
            "updated_time": ad.get("updated_time"),
            "raw_payload": ad,
        }

    for creative in (metadata or {}).get("creatives", []):
        creatives[(client_id, "meta", account_id, creative["creative_id"])] = {
            "client_id": client_id, "platform": "meta", "account_id": account_id, **creative
        }

    return {
        "campaigns": upsert_rows("campaigns", list(campaigns.values()), "client_id,platform,account_id,campaign_id"),
        "ad_sets": upsert_rows("ad_sets", list(ad_sets.values()), "client_id,platform,account_id,adset_id"),
        "ads": upsert_rows("ads", list(ads.values()), "client_id,platform,account_id,ad_id"),
        "creatives": upsert_rows("creatives", list(creatives.values()), "client_id,platform,account_id,creative_id"),
    }


def sync_account_for_date(account, target_date: date) -> dict:
    raw_rows = fetch_meta_insights(account, target_date)
    metadata = fetch_dimension_metadata(account, raw_rows)
    metadata["client_id"] = account["client_id"]
    metadata["account_id"] = account["ad_account_id"]
    ad_creatives = {
        ad["id"]: creative["creative_id"]
        for ad in metadata["ads"]
        if (creative := creative_from_ad(ad))
    }
    rows = normalize_performance_rows(account, raw_rows, ad_creatives)
    performance_count = upsert_rows(
        PERFORMANCE_TABLE,
        rows,
        "perf_date,client_id,platform,account_id,campaign_id,adset_id,ad_id,country",
    )
    breakdowns, breakdown_errors = sync_breakdown_rows(account, target_date, ad_creatives)
    return {
        "date": target_date.isoformat(),
        "rows_fetched": len(raw_rows),
        "performance_rows": performance_count,
        "breakdowns": breakdowns,
        "breakdown_errors": breakdown_errors,
        "dimensions": upsert_dimensions(rows, metadata),
    }


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
