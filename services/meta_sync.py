import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from core.config import settings
from core.supabase_client import get_supabase

PERFORMANCE_TABLE = "marketing_performance_daily"
BREAKDOWN_TABLE = "marketing_performance_breakdowns"
ACTION_TABLE = "meta_action_daily"
LEAD_FORMS_TABLE = "meta_lead_forms"
LEADS_TABLE = "meta_leads"
LEAD_ANSWERS_TABLE = "meta_lead_answers"
ENTITY_SNAPSHOTS_TABLE = "meta_entity_snapshots_daily"
TARGETING_SNAPSHOTS_TABLE = "meta_targeting_snapshots"
CREATIVE_ASSETS_TABLE = "meta_creative_assets"
AD_CREATIVE_USAGE_TABLE = "meta_ad_creative_usage"
PIXELS_TABLE = "meta_pixels"
EVENT_SOURCES_TABLE = "meta_event_sources"
ACCOUNT_ACTIVITIES_TABLE = "meta_account_activities"
ACCOUNT_HEALTH_TABLE = "meta_account_health_snapshots"
FORM_HEALTH_TABLE = "meta_form_health_snapshots"
BUSINESS_ASSETS_TABLE = "meta_business_assets"
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
            "is_active, geo_grain, enabled_breakdowns, performance_sync_frequency_hours, "
            "leads_sync_frequency_hours, metadata_sync_frequency_hours, breakdown_sync_frequency_hours, "
            "health_sync_frequency_hours, last_performance_synced_at, last_leads_synced_at, "
            "last_metadata_synced_at, last_breakdown_synced_at, last_health_synced_at"
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
            "is_active, sync_frequency_hours, last_synced_at, geo_grain, enabled_breakdowns, "
            "performance_sync_frequency_hours, leads_sync_frequency_hours, metadata_sync_frequency_hours, "
            "breakdown_sync_frequency_hours, health_sync_frequency_hours, last_performance_synced_at, "
            "last_leads_synced_at, last_metadata_synced_at, last_breakdown_synced_at, last_health_synced_at"
        )
        .eq("is_active", True)
        .eq("backfill_done", True)
        .execute()
    )
    return result.data or []


def get_metadata_accounts():
    result = (
        supabase()
        .table("meta_accounts")
        .select(
            "id, client_id, ad_account_id, ad_account_name, access_token, backfill_done, "
            "is_active, sync_frequency_hours, last_synced_at, geo_grain, enabled_breakdowns, "
            "performance_sync_frequency_hours, leads_sync_frequency_hours, metadata_sync_frequency_hours, "
            "breakdown_sync_frequency_hours, health_sync_frequency_hours, last_performance_synced_at, "
            "last_leads_synced_at, last_metadata_synced_at, last_breakdown_synced_at, last_health_synced_at, "
            "token_status, token_expires_at, permissions"
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


def account_lane_is_due(account, now: datetime, last_key: str, frequency_key: str, default_hours: int) -> tuple[bool, str]:
    last_synced_at = parse_supabase_datetime(account.get(last_key))
    if last_synced_at is None:
        return True, "never_synced"

    frequency_hours = max(to_int(account.get(frequency_key)) or default_hours, 1)
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
        "spend", "ctr", "cpc", "actions", "action_values", "cost_per_action_type",
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


def fetch_graph_edge(path: str, access_token: str, fields: list[str] | None = None, params: dict | None = None):
    url = graph_url(path)
    request_params = {"access_token": access_token, "limit": 500, **(params or {})}
    if fields:
        request_params["fields"] = ",".join(fields)

    rows = []
    while url:
        response = requests.get(url, params=request_params, timeout=60)
        data = response.json()
        if response.status_code >= 400 or "error" in data:
            raise RuntimeError(f"Meta API failed for {path}: {json.dumps(data)}")
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        request_params = None
    return rows


def fetch_graph_object(path: str, access_token: str, fields: list[str] | None = None, params: dict | None = None):
    request_params = {"access_token": access_token, **(params or {})}
    if fields:
        request_params["fields"] = ",".join(fields)
    response = requests.get(graph_url(path), params=request_params, timeout=60)
    data = response.json()
    if response.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Meta API failed for {path}: {json.dumps(data)}")
    return data


def parse_meta_timestamp(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


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
    base_fields = [
        "id", "name", "campaign_id", "adset_id", "status", "effective_status",
        "configured_status", "created_time", "updated_time",
    ]
    creative_fields = (
        "creative{id,name,title,body,call_to_action_type,image_url,thumbnail_url,video_id,"
        "image_hash,object_type,object_story_id,effective_object_story_id,url_tags,"
        "object_story_spec,asset_feed_spec}"
    )

    try:
        return fetch_meta_edge(meta_account, "ads", [*base_fields, creative_fields], ad_ids or None)
    except Exception:
        fallback_creative_fields = (
            "creative{id,name,title,body,call_to_action_type,image_url,thumbnail_url,video_id,"
            "object_type,object_story_spec}"
        )
        return fetch_meta_edge(meta_account, "ads", [*base_fields, fallback_creative_fields], ad_ids or None)


def fetch_dimension_metadata(meta_account, rows):
    campaign_ids = {row.get("campaign_id") for row in rows if row.get("campaign_id")}
    adset_ids = {row.get("adset_id") for row in rows if row.get("adset_id")}
    ad_ids = {row.get("ad_id") for row in rows if row.get("ad_id")}

    campaigns = fetch_meta_edge(meta_account, "campaigns", [
        "id", "name", "objective", "status", "effective_status", "configured_status", "buying_type",
        "bid_strategy", "daily_budget", "lifetime_budget", "budget_remaining",
        "start_time", "stop_time", "created_time", "updated_time",
    ], campaign_ids) if campaign_ids else []

    ad_sets = fetch_meta_edge(meta_account, "adsets", [
        "id", "name", "campaign_id", "status", "effective_status", "optimization_goal",
        "configured_status", "billing_event", "bid_strategy", "bid_amount", "daily_budget",
        "lifetime_budget", "budget_remaining", "pacing_type", "promoted_object", "attribution_spec",
        "targeting", "start_time", "end_time", "created_time", "updated_time",
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


def action_cost_map(row: dict) -> dict[str, float]:
    return {
        item.get("action_type"): to_float(item.get("value"))
        for item in (row.get("cost_per_action_type") or [])
        if item.get("action_type")
    }


def action_value_map(row: dict) -> dict[str, float]:
    return {
        item.get("action_type"): to_float(item.get("value"))
        for item in (row.get("action_values") or [])
        if item.get("action_type")
    }


def normalize_action_rows(meta_account, rows):
    output = []
    account_id = normalize_account_id(meta_account["ad_account_id"])
    for row in rows:
        costs = action_cost_map(row)
        values = action_value_map(row)
        for action in row.get("actions") or []:
            action_type = action.get("action_type")
            if not action_type:
                continue
            output.append({
                "perf_date": row.get("date_start"),
                "client_id": meta_account["client_id"],
                "platform": "meta",
                "account_id": account_id,
                "campaign_id": row.get("campaign_id") or "all",
                "campaign_name": row.get("campaign_name"),
                "adset_id": row.get("adset_id") or "all",
                "adset_name": row.get("adset_name"),
                "ad_id": row.get("ad_id") or "all",
                "ad_name": row.get("ad_name"),
                "action_type": action_type,
                "action_destination": action.get("action_destination") or "all",
                "action_device": action.get("action_device") or "all",
                "action_target_id": action.get("action_target_id") or "all",
                "action_reaction": action.get("action_reaction") or "all",
                "action_video_type": action.get("action_video_type") or "all",
                "value": to_float(action.get("value")),
                "cost": costs.get(action_type),
                "conversion_value": values.get(action_type, 0),
                "attribution_window": action.get("attribution_window") or "default",
                "raw_payload": {"action": action, "costs": row.get("cost_per_action_type"), "values": row.get("action_values")},
            })
    return output


def sync_action_rows(meta_account, raw_rows) -> int:
    return upsert_rows(
        ACTION_TABLE,
        normalize_action_rows(meta_account, raw_rows),
        (
            "perf_date,client_id,account_id,campaign_id,adset_id,ad_id,action_type,"
            "action_destination,action_device,action_target_id,action_reaction,action_video_type,attribution_window"
        ),
    )


def creative_texts_from_asset_feed(asset_feed_spec: dict, key: str, value_key: str = "text") -> list[str]:
    values = []
    for item in asset_feed_spec.get(key) or []:
        if isinstance(item, dict) and item.get(value_key):
            values.append(item[value_key])
    return values


def creative_asset_rows(account, ads: list[dict]) -> tuple[list[dict], list[dict]]:
    account_id = normalize_account_id(account["ad_account_id"])
    assets = []
    usage = []
    for ad in ads:
        creative = ad.get("creative") or {}
        creative_id = creative.get("id")
        if not creative_id:
            continue
        story_spec = creative.get("object_story_spec") or {}
        link_data = story_spec.get("link_data") or {}
        video_data = story_spec.get("video_data") or {}
        asset_feed_spec = creative.get("asset_feed_spec") or {}
        cta_type = (
            creative.get("call_to_action_type")
            or nested_value(link_data, "call_to_action", "type")
            or nested_value(video_data, "call_to_action", "type")
            or first_asset_text(asset_feed_spec, "call_to_action_types", "type")
        )
        base = {
            "client_id": account["client_id"],
            "platform": "meta",
            "account_id": account_id,
            "creative_id": creative_id,
            "headline": creative.get("title") or link_data.get("name") or video_data.get("title") or first_asset_text(asset_feed_spec, "titles"),
            "primary_text": creative.get("body") or link_data.get("message") or video_data.get("message") or first_asset_text(asset_feed_spec, "bodies"),
            "description": link_data.get("description") or first_asset_text(asset_feed_spec, "descriptions"),
            "cta_type": cta_type,
            "cta_text": cta_type,
            "destination_url": nested_value(link_data, "call_to_action", "value", "link") or link_data.get("link") or nested_value(video_data, "call_to_action", "value", "link"),
            "display_url": link_data.get("caption"),
            "link_url": link_data.get("link"),
            "image_url": creative.get("image_url") or link_data.get("image_url"),
            "thumbnail_url": creative.get("thumbnail_url"),
            "video_id": creative.get("video_id") or video_data.get("video_id"),
            "facebook_post_id": creative.get("object_story_id") or creative.get("effective_object_story_id"),
            "url_tags": creative.get("url_tags"),
            "raw_payload": creative,
        }
        assets.append({**base, "asset_kind": creative.get("object_type") or "creative", "asset_id": creative_id, "asset_hash": creative.get("image_hash") or ""})
        for idx, text in enumerate(creative_texts_from_asset_feed(asset_feed_spec, "bodies")):
            assets.append({**base, "asset_kind": "body", "asset_id": f"body_{idx}", "asset_hash": "", "primary_text": text, "asset_payload": {"text": text}})
        for idx, text in enumerate(creative_texts_from_asset_feed(asset_feed_spec, "titles")):
            assets.append({**base, "asset_kind": "headline", "asset_id": f"headline_{idx}", "asset_hash": "", "headline": text, "asset_payload": {"text": text}})
        for idx, text in enumerate(creative_texts_from_asset_feed(asset_feed_spec, "descriptions")):
            assets.append({**base, "asset_kind": "description", "asset_id": f"description_{idx}", "asset_hash": "", "description": text, "asset_payload": {"text": text}})
        usage.append({
            "client_id": account["client_id"],
            "platform": "meta",
            "account_id": account_id,
            "campaign_id": ad.get("campaign_id"),
            "adset_id": ad.get("adset_id"),
            "ad_id": ad["id"],
            "creative_id": creative_id,
            "creative_name": creative.get("name"),
            "effective_object_story_id": creative.get("effective_object_story_id"),
            "object_story_id": creative.get("object_story_id"),
            "object_story_spec": story_spec,
            "last_seen_at": datetime.utcnow().isoformat(),
            "raw_payload": creative,
        })
    return assets, usage


def sync_creative_asset_tables(account, ads: list[dict]) -> dict:
    assets, usage = creative_asset_rows(account, ads)
    return {
        "creative_assets": upsert_rows(
            CREATIVE_ASSETS_TABLE,
            assets,
            "client_id,account_id,creative_id,asset_kind,asset_id,asset_hash",
        ),
        "ad_creative_usage": upsert_rows(
            AD_CREATIVE_USAGE_TABLE,
            usage,
            "client_id,account_id,ad_id,creative_id",
        ),
    }


def fetch_full_dimension_metadata(meta_account) -> dict:
    campaigns = fetch_meta_edge(meta_account, "campaigns", [
        "id", "name", "objective", "status", "effective_status", "configured_status", "buying_type",
        "bid_strategy", "daily_budget", "lifetime_budget", "budget_remaining",
        "start_time", "stop_time", "created_time", "updated_time",
    ])
    ad_sets = fetch_meta_edge(meta_account, "adsets", [
        "id", "name", "campaign_id", "status", "effective_status", "configured_status",
        "optimization_goal", "billing_event", "bid_strategy", "bid_amount", "daily_budget",
        "lifetime_budget", "budget_remaining", "pacing_type", "promoted_object", "attribution_spec",
        "targeting", "start_time", "end_time", "created_time", "updated_time",
    ])
    ads = fetch_ads_metadata(meta_account, set())
    return {
        "campaigns": campaigns,
        "ad_sets": ad_sets,
        "ads": ads,
        "creatives": [creative for ad in ads if (creative := creative_from_ad(ad))],
        "client_id": meta_account["client_id"],
        "account_id": meta_account["ad_account_id"],
    }


def entity_snapshot_rows(account, metadata: dict, snapshot_date: date) -> list[dict]:
    account_id = normalize_account_id(account["ad_account_id"])
    rows = []
    for campaign in metadata.get("campaigns", []):
        rows.append({
            "snapshot_date": snapshot_date.isoformat(),
            "client_id": account["client_id"],
            "platform": "meta",
            "account_id": account_id,
            "entity_type": "campaign",
            "entity_id": campaign["id"],
            "entity_name": campaign.get("name"),
            "status": campaign.get("status"),
            "effective_status": campaign.get("effective_status"),
            "configured_status": campaign.get("configured_status"),
            "objective": campaign.get("objective"),
            "buying_type": campaign.get("buying_type"),
            "bid_strategy": campaign.get("bid_strategy"),
            "daily_budget": campaign.get("daily_budget"),
            "lifetime_budget": campaign.get("lifetime_budget"),
            "budget_remaining": campaign.get("budget_remaining"),
            "start_time": campaign.get("start_time"),
            "stop_time": campaign.get("stop_time"),
            "created_time": campaign.get("created_time"),
            "updated_time": campaign.get("updated_time"),
            "raw_payload": campaign,
        })
    for ad_set in metadata.get("ad_sets", []):
        rows.append({
            "snapshot_date": snapshot_date.isoformat(),
            "client_id": account["client_id"],
            "platform": "meta",
            "account_id": account_id,
            "entity_type": "adset",
            "entity_id": ad_set["id"],
            "entity_name": ad_set.get("name"),
            "parent_campaign_id": ad_set.get("campaign_id"),
            "status": ad_set.get("status"),
            "effective_status": ad_set.get("effective_status"),
            "configured_status": ad_set.get("configured_status"),
            "optimization_goal": ad_set.get("optimization_goal"),
            "billing_event": ad_set.get("billing_event"),
            "bid_strategy": ad_set.get("bid_strategy"),
            "bid_amount": ad_set.get("bid_amount"),
            "daily_budget": ad_set.get("daily_budget"),
            "lifetime_budget": ad_set.get("lifetime_budget"),
            "budget_remaining": ad_set.get("budget_remaining"),
            "pacing_type": ad_set.get("pacing_type"),
            "promoted_object": ad_set.get("promoted_object"),
            "attribution_spec": ad_set.get("attribution_spec"),
            "start_time": ad_set.get("start_time"),
            "stop_time": ad_set.get("end_time"),
            "created_time": ad_set.get("created_time"),
            "updated_time": ad_set.get("updated_time"),
            "raw_payload": ad_set,
        })
    for ad in metadata.get("ads", []):
        rows.append({
            "snapshot_date": snapshot_date.isoformat(),
            "client_id": account["client_id"],
            "platform": "meta",
            "account_id": account_id,
            "entity_type": "ad",
            "entity_id": ad["id"],
            "entity_name": ad.get("name"),
            "parent_campaign_id": ad.get("campaign_id"),
            "parent_adset_id": ad.get("adset_id"),
            "status": ad.get("status"),
            "effective_status": ad.get("effective_status"),
            "configured_status": ad.get("configured_status"),
            "created_time": ad.get("created_time"),
            "updated_time": ad.get("updated_time"),
            "raw_payload": ad,
        })
    return rows


def targeting_snapshot_rows(account, ad_sets: list[dict], snapshot_date: date) -> list[dict]:
    account_id = normalize_account_id(account["ad_account_id"])
    rows = []
    for ad_set in ad_sets:
        targeting = ad_set.get("targeting") or {}
        geo = targeting.get("geo_locations") or {}
        rows.append({
            "snapshot_date": snapshot_date.isoformat(),
            "client_id": account["client_id"],
            "platform": "meta",
            "account_id": account_id,
            "campaign_id": ad_set.get("campaign_id"),
            "adset_id": ad_set["id"],
            "adset_name": ad_set.get("name"),
            "age_min": targeting.get("age_min"),
            "age_max": targeting.get("age_max"),
            "genders": targeting.get("genders"),
            "geo_locations": geo,
            "countries": geo.get("countries"),
            "regions": geo.get("regions"),
            "cities": geo.get("cities"),
            "languages": targeting.get("locales"),
            "interests": targeting.get("interests"),
            "behaviors": targeting.get("behaviors"),
            "custom_audiences": targeting.get("custom_audiences"),
            "excluded_custom_audiences": targeting.get("excluded_custom_audiences"),
            "flexible_spec": targeting.get("flexible_spec"),
            "exclusions": targeting.get("exclusions"),
            "publisher_platforms": targeting.get("publisher_platforms"),
            "facebook_positions": targeting.get("facebook_positions"),
            "instagram_positions": targeting.get("instagram_positions"),
            "audience_network_positions": targeting.get("audience_network_positions"),
            "messenger_positions": targeting.get("messenger_positions"),
            "device_platforms": targeting.get("device_platforms"),
            "user_os": targeting.get("user_os"),
            "raw_targeting": targeting,
            "raw_payload": ad_set,
        })
    return rows


def safe_fetch(label: str, fn):
    try:
        return fn(), None
    except Exception as exc:
        return [], {"source": label, "error": str(exc)}


def fetch_lead_forms(account):
    fields = [
        "id", "name", "status", "locale", "questions", "privacy_policy_url",
        "thank_you_page", "context_card", "follow_up_action_url", "created_time",
    ]
    return fetch_meta_edge(account, "leadgen_forms", fields)


def normalize_lead_form(account, form: dict) -> dict:
    questions = form.get("questions") or []
    return {
        "client_id": account["client_id"],
        "platform": "meta",
        "account_id": normalize_account_id(account["ad_account_id"]),
        "form_id": form["id"],
        "form_name": form.get("name"),
        "status": form.get("status"),
        "locale": form.get("locale"),
        "question_count": len(questions),
        "questions": questions,
        "privacy_policy_url": form.get("privacy_policy_url"),
        "thank_you_screen": form.get("thank_you_page"),
        "context_card": form.get("context_card"),
        "follow_up_action_url": form.get("follow_up_action_url"),
        "created_time": form.get("created_time"),
        "raw_payload": form,
    }


def sync_lead_forms(account, forms: list[dict]) -> int:
    return upsert_rows(
        LEAD_FORMS_TABLE,
        [normalize_lead_form(account, form) for form in forms if form.get("id")],
        "client_id,account_id,form_id",
    )


def fetch_form_leads(account, form_id: str):
    fields = [
        "id", "created_time", "field_data", "ad_id", "ad_name", "adset_id", "adset_name",
        "campaign_id", "campaign_name", "form_id", "is_organic", "platform",
    ]
    return fetch_graph_edge(f"{form_id}/leads", account["access_token"], fields)


def normalized_field_value(field_data: list[dict], names: set[str]) -> str | None:
    for item in field_data or []:
        name = (item.get("name") or "").lower()
        if name in names:
            values = item.get("values") or []
            return values[0] if values else None
    return None


def normalize_lead(account, lead: dict, form: dict | None = None) -> dict:
    field_data = lead.get("field_data") or []
    return {
        "client_id": account["client_id"],
        "platform": "meta",
        "account_id": normalize_account_id(account["ad_account_id"]),
        "lead_id": lead["id"],
        "form_id": lead.get("form_id") or (form or {}).get("id"),
        "form_name": (form or {}).get("name"),
        "campaign_id": lead.get("campaign_id"),
        "campaign_name": lead.get("campaign_name"),
        "adset_id": lead.get("adset_id"),
        "adset_name": lead.get("adset_name"),
        "ad_id": lead.get("ad_id"),
        "ad_name": lead.get("ad_name"),
        "lead_created_time": lead.get("created_time"),
        "field_data": field_data,
        "normalized_email": normalized_field_value(
            field_data,
            {"email", "e-mail", "work_email", "business_email", "company_email"},
        ),
        "normalized_phone": normalized_field_value(field_data, {"phone_number", "phone", "mobile"}),
        "normalized_name": normalized_field_value(field_data, {"full_name", "name", "first_name"}),
        "normalized_city": normalized_field_value(field_data, {"city"}),
        "normalized_country": normalized_field_value(field_data, {"country"}),
        "is_organic": lead.get("is_organic"),
        "source": lead.get("platform"),
        "raw_payload": lead,
    }


def normalize_lead_answers(account, lead: dict) -> list[dict]:
    rows = []
    for item in lead.get("field_data") or []:
        field_name = item.get("name")
        if not field_name:
            continue
        values = item.get("values") or []
        rows.append({
            "client_id": account["client_id"],
            "account_id": normalize_account_id(account["ad_account_id"]),
            "lead_id": lead["id"],
            "form_id": lead.get("form_id"),
            "field_name": field_name,
            "field_label": field_name,
            "field_value": values[0] if values else None,
            "field_values": values,
        })
    return rows


def sync_leads(account, forms: list[dict]) -> dict:
    leads = []
    answers = []
    errors = []
    forms_by_id = {form.get("id"): form for form in forms}
    for form in forms:
        form_id = form.get("id")
        if not form_id:
            continue
        raw_leads, error = safe_fetch(f"lead_form_{form_id}", lambda form_id=form_id: fetch_form_leads(account, form_id))
        if error:
            errors.append(error)
            continue
        for lead in raw_leads:
            lead.setdefault("form_id", form_id)
            leads.append(normalize_lead(account, lead, forms_by_id.get(form_id)))
            answers.extend(normalize_lead_answers(account, lead))
    return {
        "leads": upsert_rows(LEADS_TABLE, leads, "client_id,account_id,lead_id"),
        "lead_answers": upsert_rows(LEAD_ANSWERS_TABLE, answers, "client_id,account_id,lead_id,field_name"),
        "errors": errors,
    }


def sync_lead_data(account) -> dict:
    forms, error = safe_fetch("leadgen_forms", lambda: fetch_lead_forms(account))
    errors = [error] if error else []
    forms_saved = sync_lead_forms(account, forms)
    lead_result = sync_leads(account, forms) if forms else {"leads": 0, "lead_answers": 0, "errors": []}
    errors.extend(lead_result.get("errors", []))
    supabase().table("meta_accounts").update({"last_leads_synced_at": datetime.utcnow().isoformat()}).eq("id", account["id"]).execute()
    return {"forms": forms_saved, **lead_result, "errors": errors}


def sync_best_effort_account_assets(account, metadata: dict) -> dict:
    account_id = normalize_account_id(account["ad_account_id"])
    errors = []
    account_info, error = safe_fetch(
        "ad_account",
        lambda: fetch_graph_object(
            normalize_ad_account_id(account["ad_account_id"]),
            account["access_token"],
            [
                "id", "name", "account_status", "disable_reason", "currency", "timezone_name",
                "timezone_offset_hours_utc", "amount_spent", "spend_cap", "balance", "business",
            ],
        ),
    )
    if error:
        errors.append(error)
        account_info = {}
    pixels, error = safe_fetch("adspixels", lambda: fetch_meta_edge(account, "adspixels", ["id", "name", "code", "last_fired_time", "is_unavailable"]))
    if error:
        errors.append(error)
    pixel_rows = [{
        "client_id": account["client_id"],
        "platform": "meta",
        "account_id": account_id,
        "pixel_id": pixel["id"],
        "pixel_name": pixel.get("name"),
        "last_fired_time": parse_meta_timestamp(pixel.get("last_fired_time")),
        "is_unavailable": pixel.get("is_unavailable"),
        "code": pixel.get("code"),
            "raw_payload": pixel,
    } for pixel in pixels if pixel.get("id")]
    event_rows = [{
        "client_id": account["client_id"],
        "platform": "meta",
        "account_id": account_id,
        "source_type": "pixel",
        "source_id": pixel["id"],
        "source_name": pixel.get("name"),
        "event_name": "unknown",
        "last_received_at": parse_meta_timestamp(pixel.get("last_fired_time")),
        "setup_status": "unavailable" if pixel.get("is_unavailable") else "active",
        "raw_payload": pixel,
    } for pixel in pixels if pixel.get("id")]

    activities, error = safe_fetch(
        "activities",
        lambda: fetch_meta_edge(account, "activities", [
            "event_time", "event_type", "object_type", "object_id", "object_name",
            "actor_id", "actor_name", "application_id", "translated_event_type", "extra_data",
        ]),
    )
    if error:
        errors.append(error)
    activity_rows = []
    for activity in activities:
        activity_id = activity.get("id") or f"{activity.get('event_time')}:{activity.get('event_type')}:{activity.get('object_id')}"
        activity_rows.append({
            "client_id": account["client_id"],
            "platform": "meta",
            "account_id": account_id,
            "activity_id": activity_id,
            "event_time": parse_meta_timestamp(activity.get("event_time")),
            "event_type": activity.get("event_type"),
            "object_type": activity.get("object_type"),
            "object_id": activity.get("object_id"),
            "object_name": activity.get("object_name"),
            "actor_id": activity.get("actor_id"),
            "actor_name": activity.get("actor_name"),
            "application_id": activity.get("application_id"),
            "translated_event_type": activity.get("translated_event_type"),
            "extra_data": activity.get("extra_data"),
            "raw_payload": activity,
        })

    assets = []
    if account_info.get("id"):
        assets.append({
            "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
            "asset_type": "ad_account", "asset_id": account_id,
            "asset_name": account_info.get("name") or account.get("ad_account_name"),
            "status": str(account_info.get("account_status") or "unknown"),
            "raw_payload": account_info,
        })
    for row in pixels:
        if row.get("id"):
            assets.append({
                "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
                "asset_type": "pixel", "asset_id": row["id"], "asset_name": row.get("name"),
                "status": "active" if not row.get("is_unavailable") else "unavailable", "raw_payload": row,
            })

    health = {
        "snapshot_date": app_today().isoformat(),
        "client_id": account["client_id"],
        "platform": "meta",
        "account_id": account_id,
        "account_name": account_info.get("name") or account.get("ad_account_name"),
        "account_status": str(account_info.get("account_status")) if account_info.get("account_status") is not None else None,
        "disable_reason": str(account_info.get("disable_reason")) if account_info.get("disable_reason") is not None else None,
        "currency": account_info.get("currency"),
        "timezone_name": account_info.get("timezone_name"),
        "timezone_offset_hours_utc": account_info.get("timezone_offset_hours_utc"),
        "amount_spent": account_info.get("amount_spent"),
        "spend_cap": account_info.get("spend_cap"),
        "balance": account_info.get("balance"),
        "token_status": account.get("token_status"),
        "token_expires_at": account.get("token_expires_at"),
        "permissions": account.get("permissions"),
        "pixels_count": len(pixels),
        "active_campaigns_count": sum(1 for c in metadata.get("campaigns", []) if c.get("effective_status") == "ACTIVE"),
        "active_adsets_count": sum(1 for a in metadata.get("ad_sets", []) if a.get("effective_status") == "ACTIVE"),
        "active_ads_count": sum(1 for a in metadata.get("ads", []) if a.get("effective_status") == "ACTIVE"),
        "rejected_ads_count": sum(1 for a in metadata.get("ads", []) if a.get("effective_status") == "DISAPPROVED"),
        "last_successful_sync_at": datetime.utcnow().isoformat(),
        "raw_payload": {"account": account_info, "asset_errors": errors},
    }
    return {
        "pixels": upsert_rows(PIXELS_TABLE, pixel_rows, "client_id,account_id,pixel_id"),
        "event_sources": upsert_rows(EVENT_SOURCES_TABLE, event_rows, "client_id,account_id,source_type,source_id,event_name"),
        "activities": upsert_rows(ACCOUNT_ACTIVITIES_TABLE, activity_rows, "client_id,account_id,activity_id"),
        "business_assets": upsert_rows(BUSINESS_ASSETS_TABLE, assets, "client_id,account_id,asset_type,asset_id"),
        "health": upsert_rows(ACCOUNT_HEALTH_TABLE, [health], "snapshot_date,client_id,account_id"),
        "errors": errors,
    }


def upsert_rows(table_name: str, rows: list[dict], on_conflict: str) -> int:
    if not rows:
        return 0
    conflict_fields = [field.strip() for field in on_conflict.split(",")]
    deduplicated = {}
    for row in rows:
        deduplicated[tuple(row.get(field) for field in conflict_fields)] = row

    total = 0
    for batch in chunk_list(list(deduplicated.values())):
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


def sync_account_for_date(account, target_date: date, include_breakdowns: bool = True, include_dimensions: bool = True) -> dict:
    raw_rows = fetch_meta_insights(account, target_date)
    metadata = fetch_dimension_metadata(account, raw_rows) if include_dimensions else {"ads": [], "campaigns": [], "ad_sets": [], "creatives": []}
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
    action_count = sync_action_rows(account, raw_rows)
    breakdowns, breakdown_errors = ({}, [])
    if include_breakdowns:
        breakdowns, breakdown_errors = sync_breakdown_rows(account, target_date, ad_creatives)
        supabase().table("meta_accounts").update({"last_breakdown_synced_at": datetime.utcnow().isoformat()}).eq("id", account["id"]).execute()
    return {
        "date": target_date.isoformat(),
        "rows_fetched": len(raw_rows),
        "performance_rows": performance_count,
        "action_rows": action_count,
        "breakdowns": breakdowns,
        "breakdown_errors": breakdown_errors,
        "dimensions": upsert_dimensions(rows, metadata) if include_dimensions else {},
    }


def sync_account_window(
    account,
    sync_type: str,
    start_date: date,
    end_date: date,
    include_breakdowns: bool = True,
    include_dimensions: bool = True,
    timestamp_columns: list[str] | None = None,
) -> dict:
    sync_run_id = create_sync_run(account, sync_type, start_date, end_date)
    counters = {"rows_fetched": 0, "rows_inserted": 0, "rows_updated": 0, "metadata": {"dates": []}}
    try:
        current = start_date
        while current <= end_date:
            result = sync_account_for_date(account, current, include_breakdowns=include_breakdowns, include_dimensions=include_dimensions)
            counters["rows_fetched"] += result["rows_fetched"]
            counters["rows_updated"] += result["performance_rows"]
            counters["metadata"]["action_rows"] = counters["metadata"].get("action_rows", 0) + result["action_rows"]
            counters["metadata"]["dates"].append(result)
            current += timedelta(days=1)
        now_iso = datetime.utcnow().isoformat()
        update_payload = {"last_synced_at": now_iso}
        for column in timestamp_columns or []:
            update_payload[column] = now_iso
        supabase().table("meta_accounts").update(update_payload).eq("id", account["id"]).execute()
        if sync_type == "backfill":
            supabase().table("meta_accounts").update({"backfill_done": True}).eq("id", account["id"]).execute()
        finish_sync_run(sync_run_id, "success", counters)
        return {"account_id": account["ad_account_id"], "status": "success", **counters}
    except Exception as exc:
        finish_sync_run(sync_run_id, "failed", counters, str(exc))
        raise


def sync_account_daily_metadata(account, start_date: date, end_date: date) -> dict:
    sync_run_id = create_sync_run(account, "daily_metadata", start_date, end_date)
    counters = {"rows_fetched": 0, "rows_inserted": 0, "rows_updated": 0, "metadata": {"dates": [], "errors": []}}
    try:
        snapshot_date = app_today()
        metadata = fetch_full_dimension_metadata(account)
        dimension_counts = upsert_dimensions([], metadata)
        counters["rows_updated"] += sum(dimension_counts.values())

        snapshot_rows = entity_snapshot_rows(account, metadata, snapshot_date)
        targeting_rows = targeting_snapshot_rows(account, metadata.get("ad_sets", []), snapshot_date)
        creative_counts = sync_creative_asset_tables(account, metadata.get("ads", []))
        counters["metadata"]["dimensions"] = dimension_counts
        counters["metadata"]["entity_snapshots"] = upsert_rows(
            ENTITY_SNAPSHOTS_TABLE,
            snapshot_rows,
            "snapshot_date,client_id,account_id,entity_type,entity_id",
        )
        counters["metadata"]["targeting_snapshots"] = upsert_rows(
            TARGETING_SNAPSHOTS_TABLE,
            targeting_rows,
            "snapshot_date,client_id,account_id,adset_id",
        )
        counters["metadata"].update(creative_counts)

        current = start_date
        ad_creatives = {
            ad["id"]: creative["creative_id"]
            for ad in metadata["ads"]
            if (creative := creative_from_ad(ad))
        }
        while current <= end_date:
            breakdowns, breakdown_errors = sync_breakdown_rows(account, current, ad_creatives)
            counters["metadata"]["dates"].append({
                "date": current.isoformat(),
                "breakdowns": breakdowns,
                "breakdown_errors": breakdown_errors,
            })
            counters["metadata"]["errors"].extend(breakdown_errors)
            current += timedelta(days=1)

        forms, form_error = safe_fetch("leadgen_forms", lambda: fetch_lead_forms(account))
        if form_error:
            counters["metadata"]["errors"].append(form_error)
        counters["metadata"]["lead_forms"] = sync_lead_forms(account, forms)
        form_health_rows = []
        for form in forms:
            questions = form.get("questions") or []
            field_names = {((q.get("key") or q.get("label") or q.get("type") or "").lower()) for q in questions if isinstance(q, dict)}
            form_health_rows.append({
                "snapshot_date": snapshot_date.isoformat(),
                "client_id": account["client_id"],
                "platform": "meta",
                "account_id": normalize_account_id(account["ad_account_id"]),
                "form_id": form["id"],
                "form_name": form.get("name"),
                "status": form.get("status"),
                "question_count": len(questions),
                "has_phone": any("phone" in name for name in field_names),
                "has_email": any("email" in name for name in field_names),
                "has_custom_questions": len(questions) > 2,
                "follow_up_action_url": form.get("follow_up_action_url"),
                "raw_payload": form,
            })
        counters["metadata"]["form_health"] = upsert_rows(
            FORM_HEALTH_TABLE,
            form_health_rows,
            "snapshot_date,client_id,account_id,form_id",
        )

        asset_counts = sync_best_effort_account_assets(account, metadata)
        counters["metadata"].update(asset_counts)

        now_iso = datetime.utcnow().isoformat()
        supabase().table("meta_accounts").update({
            "last_metadata_synced_at": now_iso,
            "last_breakdown_synced_at": now_iso,
            "last_health_synced_at": now_iso,
            "last_synced_at": now_iso,
        }).eq("id", account["id"]).execute()

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
        result = sync_account_window(
            account,
            "daily",
            start_date,
            end_date,
            include_breakdowns=True,
            include_dimensions=True,
            timestamp_columns=["last_performance_synced_at", "last_breakdown_synced_at"],
        )
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
        result = sync_account_window(
            account,
            "backfill",
            start_date,
            end_date,
            include_breakdowns=True,
            include_dimensions=True,
            timestamp_columns=["last_performance_synced_at", "last_breakdown_synced_at"],
        )
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
        performance_due, performance_reason = account_lane_is_due(
            account,
            now,
            "last_performance_synced_at",
            "performance_sync_frequency_hours",
            account.get("sync_frequency_hours") or 24,
        )
        legacy_due, legacy_reason = account_is_due(account, now)
        is_due = performance_due or legacy_due
        reason = performance_reason if performance_due else legacy_reason

        leads_due, leads_reason = account_lane_is_due(
            account,
            now,
            "last_leads_synced_at",
            "leads_sync_frequency_hours",
            4,
        )

        if not is_due and not leads_due:
            skipped.append({
                "account_id": account.get("ad_account_id"),
                "account_name": account.get("ad_account_name"),
                "reason": {"performance": reason, "leads": leads_reason},
                "performance_sync_frequency_hours": account.get("performance_sync_frequency_hours") or account.get("sync_frequency_hours") or 24,
                "leads_sync_frequency_hours": account.get("leads_sync_frequency_hours") or 4,
                "last_performance_synced_at": account.get("last_performance_synced_at") or account.get("last_synced_at"),
                "last_leads_synced_at": account.get("last_leads_synced_at"),
            })
            continue

        result = {
            "account_id": account.get("ad_account_id"),
            "status": "success",
            "performance": None,
            "leads": None,
        }
        if is_due:
            performance_result = sync_account_window(
                account,
                "scheduled",
                start_date,
                end_date,
                include_breakdowns=False,
                include_dimensions=False,
                timestamp_columns=["last_performance_synced_at"],
            )
            performance_result["reason"] = reason
            performance_result["performance_sync_frequency_hours"] = account.get("performance_sync_frequency_hours") or account.get("sync_frequency_hours") or 24
            performance_result["insights"] = generate_basic_insights(account.get("client_id"))
            result["performance"] = performance_result
        if leads_due:
            result["leads"] = {"reason": leads_reason, **sync_lead_data(account)}
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


def run_daily_metadata_sync() -> dict:
    accounts = get_metadata_accounts()
    now = datetime.now(timezone.utc)
    today = app_today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=max(settings.daily_lookback_days - 1, 0))
    results = []
    skipped = []

    for account in accounts:
        metadata_due, metadata_reason = account_lane_is_due(
            account,
            now,
            "last_metadata_synced_at",
            "metadata_sync_frequency_hours",
            24,
        )
        breakdown_due, breakdown_reason = account_lane_is_due(
            account,
            now,
            "last_breakdown_synced_at",
            "breakdown_sync_frequency_hours",
            24,
        )
        health_due, health_reason = account_lane_is_due(
            account,
            now,
            "last_health_synced_at",
            "health_sync_frequency_hours",
            24,
        )
        if not (metadata_due or breakdown_due or health_due):
            skipped.append({
                "account_id": account.get("ad_account_id"),
                "account_name": account.get("ad_account_name"),
                "reason": {
                    "metadata": metadata_reason,
                    "breakdowns": breakdown_reason,
                    "health": health_reason,
                },
            })
            continue
        result = sync_account_daily_metadata(account, start_date, end_date)
        result["reason"] = {
            "metadata": metadata_reason,
            "breakdowns": breakdown_reason,
            "health": health_reason,
        }
        results.append(result)

    return {
        "mode": "daily_metadata",
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
