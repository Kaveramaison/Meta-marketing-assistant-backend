import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from core.config import settings
from core.supabase_client import get_supabase
from services.meta_sync import (
    BREAKDOWN_TABLE,
    BREAKDOWN_CONFIGS,
    DEFAULT_BREAKDOWNS,
    GEO_GRAIN_BREAKDOWNS,
    app_today,
    chunk_list,
    creative_from_ad,
    fetch_meta_insights,
    normalize_account_id,
    normalize_ad_account_id,
    sync_breakdown_rows,
)

BATCH_SIZE = 500
ACTION_BREAKDOWNS = (
    "action_destination",
    "action_device",
    "action_target_id",
    "action_reaction",
    "action_video_type",
)
ACTION_ATTRIBUTION_WINDOWS = ("1d_view", "7d_click")


def supabase():
    return get_supabase()


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


def related_id(value):
    if isinstance(value, dict):
        return value.get("id")
    return str(value) if value else None


def graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_api_version}/{path.lstrip('/')}"


def parse_meta_timestamp(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


def parse_supabase_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def account_lane_is_due(account, now: datetime, last_key: str, frequency_key: str, default_hours: int):
    last_synced_at = parse_supabase_datetime(account.get(last_key))
    if last_synced_at is None:
        return True, "never_synced"
    frequency_hours = max(to_int(account.get(frequency_key)) or default_hours, 1)
    next_sync_at = last_synced_at + timedelta(hours=frequency_hours)
    if now >= next_sync_at:
        return True, "frequency_elapsed"
    return False, f"next_sync_at={next_sync_at.isoformat()}"


def upsert_rows(table_name: str, rows: list[dict], on_conflict: str) -> int:
    if not rows:
        return 0
    conflict_fields = [field.strip() for field in on_conflict.split(",")]
    deduplicated = {}
    for row in rows:
        deduplicated[tuple(row.get(field) for field in conflict_fields)] = row

    total = 0
    for batch in chunk_list(list(deduplicated.values()), BATCH_SIZE):
        result = supabase().table(table_name).upsert(batch, on_conflict=on_conflict).execute()
        total += len(result.data or [])
    return total


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


def fetch_graph_object(path: str, access_token: str, fields: list[str] | None = None):
    params = {"access_token": access_token}
    if fields:
        params["fields"] = ",".join(fields)
    response = requests.get(graph_url(path), params=params, timeout=60)
    data = response.json()
    if response.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Meta API failed for {path}: {json.dumps(data)}")
    return data


def safe_fetch(label: str, fn, fallback=None):
    try:
        return fn(), None
    except Exception as exc:
        return ([] if fallback is None else fallback), {"source": label, "error": str(exc)}


def get_metadata_accounts():
    result = (
        supabase()
        .table("meta_accounts")
        .select(
            "id, client_id, ad_account_id, ad_account_name, access_token, is_active, backfill_done, "
            "geo_grain, enabled_breakdowns, metadata_sync_frequency_hours, breakdown_sync_frequency_hours, "
            "health_sync_frequency_hours, last_metadata_synced_at, last_breakdown_synced_at, "
            "last_health_synced_at, token_status, token_expires_at, permissions"
        )
        .eq("is_active", True)
        .eq("backfill_done", True)
        .execute()
    )
    return result.data or []


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
    supabase().table("sync_runs").update({
        "status": status,
        "finished_at": datetime.utcnow().isoformat(),
        "rows_fetched": counters.get("rows_fetched", 0),
        "rows_inserted": counters.get("rows_inserted", 0),
        "rows_updated": counters.get("rows_updated", 0),
        "error_message": error_message,
        "metadata": counters.get("metadata", {}),
    }).eq("id", sync_run_id).execute()


def fetch_full_metadata(account):
    access_token = account["access_token"]
    ad_account = normalize_ad_account_id(account["ad_account_id"])
    campaigns = fetch_graph_edge(f"{ad_account}/campaigns", access_token, [
        "id", "name", "objective", "status", "effective_status", "configured_status", "buying_type",
        "bid_strategy", "daily_budget", "lifetime_budget", "budget_remaining",
        "start_time", "stop_time", "created_time", "updated_time",
    ])
    ad_sets = fetch_graph_edge(f"{ad_account}/adsets", access_token, [
        "id", "name", "campaign_id", "status", "effective_status", "configured_status",
        "optimization_goal", "billing_event", "bid_strategy", "bid_amount", "daily_budget",
        "lifetime_budget", "budget_remaining", "pacing_type", "promoted_object", "attribution_spec",
        "targeting", "start_time", "end_time", "created_time", "updated_time",
    ])
    # Fetch the ad list without expanding full creative payloads. Meta rejects the
    # expanded /ads request once story specs and flexible assets become large.
    ads = fetch_graph_edge(f"{ad_account}/ads", access_token, [
        "id", "name", "campaign_id", "adset_id", "status", "effective_status",
        "configured_status", "created_time", "updated_time", "creative{id}",
    ])
    creative_fields = [
        "id", "name", "title", "body", "call_to_action_type", "image_url",
        "thumbnail_url", "video_id", "image_hash", "object_type", "object_story_id",
        "effective_object_story_id", "url_tags", "object_story_spec", "asset_feed_spec",
    ]
    creatives_by_id = {}
    for creative_id in {
        (ad.get("creative") or {}).get("id") for ad in ads if (ad.get("creative") or {}).get("id")
    }:
        creative, error = safe_fetch(
            f"creative_{creative_id}",
            lambda creative_id=creative_id: fetch_graph_object(creative_id, access_token, creative_fields),
            fallback={"id": creative_id},
        )
        creatives_by_id[creative_id] = creative

    for ad in ads:
        creative_id = (ad.get("creative") or {}).get("id")
        if creative_id:
            ad["creative"] = creatives_by_id.get(creative_id, {"id": creative_id})
    return {
        "campaigns": campaigns,
        "ad_sets": ad_sets,
        "ads": ads,
        "creatives": [creative for ad in ads if (creative := creative_from_ad(ad))],
        "client_id": account["client_id"],
        "account_id": account["ad_account_id"],
    }


def upsert_dimensions(account, metadata: dict) -> dict:
    account_id = normalize_account_id(account["ad_account_id"])
    client_id = account["client_id"]
    campaigns = [{
        "client_id": client_id, "platform": "meta", "account_id": account_id,
        "campaign_id": row["id"], "campaign_name": row.get("name"),
        "objective": row.get("objective"), "status": row.get("status"),
        "effective_status": row.get("effective_status"), "buying_type": row.get("buying_type"),
        "daily_budget": row.get("daily_budget"), "lifetime_budget": row.get("lifetime_budget"),
        "start_time": row.get("start_time"), "stop_time": row.get("stop_time"),
        "created_time": row.get("created_time"), "updated_time": row.get("updated_time"),
        "raw_payload": row,
    } for row in metadata.get("campaigns", []) if row.get("id")]
    ad_sets = [{
        "client_id": client_id, "platform": "meta", "account_id": account_id,
        "campaign_id": row.get("campaign_id"), "adset_id": row["id"], "adset_name": row.get("name"),
        "status": row.get("status"), "effective_status": row.get("effective_status"),
        "optimization_goal": row.get("optimization_goal"), "billing_event": row.get("billing_event"),
        "bid_strategy": row.get("bid_strategy"), "daily_budget": row.get("daily_budget"),
        "lifetime_budget": row.get("lifetime_budget"), "targeting": row.get("targeting"),
        "start_time": row.get("start_time"), "end_time": row.get("end_time"),
        "created_time": row.get("created_time"), "updated_time": row.get("updated_time"),
        "raw_payload": row,
    } for row in metadata.get("ad_sets", []) if row.get("id")]
    ads = []
    creatives = []
    for row in metadata.get("ads", []):
        if not row.get("id"):
            continue
        creative = creative_from_ad(row)
        ads.append({
            "client_id": client_id, "platform": "meta", "account_id": account_id,
            "campaign_id": row.get("campaign_id"), "adset_id": row.get("adset_id"), "ad_id": row["id"],
            "ad_name": row.get("name"), "creative_id": creative.get("creative_id") if creative else None,
            "status": row.get("status"), "effective_status": row.get("effective_status"),
            "updated_time": row.get("updated_time"), "raw_payload": row,
        })
        if creative:
            creatives.append({"client_id": client_id, "platform": "meta", "account_id": account_id, **creative})
    return {
        "campaigns": upsert_rows("campaigns", campaigns, "client_id,platform,account_id,campaign_id"),
        "ad_sets": upsert_rows("ad_sets", ad_sets, "client_id,platform,account_id,adset_id"),
        "ads": upsert_rows("ads", ads, "client_id,platform,account_id,ad_id"),
        "creatives": upsert_rows("creatives", creatives, "client_id,platform,account_id,creative_id"),
    }


def snapshot_rows(account, metadata: dict, snapshot_date: date):
    account_id = normalize_account_id(account["ad_account_id"])
    client_id = account["client_id"]
    rows = []
    for row in metadata.get("campaigns", []):
        rows.append({
            "snapshot_date": snapshot_date.isoformat(), "client_id": client_id, "platform": "meta",
            "account_id": account_id, "entity_type": "campaign", "entity_id": row["id"],
            "entity_name": row.get("name"), "status": row.get("status"),
            "effective_status": row.get("effective_status"), "configured_status": row.get("configured_status"),
            "objective": row.get("objective"), "buying_type": row.get("buying_type"),
            "bid_strategy": row.get("bid_strategy"), "daily_budget": row.get("daily_budget"),
            "lifetime_budget": row.get("lifetime_budget"), "budget_remaining": row.get("budget_remaining"),
            "start_time": row.get("start_time"), "stop_time": row.get("stop_time"),
            "created_time": row.get("created_time"), "updated_time": row.get("updated_time"),
            "raw_payload": row,
        })
    for row in metadata.get("ad_sets", []):
        rows.append({
            "snapshot_date": snapshot_date.isoformat(), "client_id": client_id, "platform": "meta",
            "account_id": account_id, "entity_type": "adset", "entity_id": row["id"],
            "entity_name": row.get("name"), "parent_campaign_id": row.get("campaign_id"),
            "status": row.get("status"), "effective_status": row.get("effective_status"),
            "configured_status": row.get("configured_status"), "optimization_goal": row.get("optimization_goal"),
            "billing_event": row.get("billing_event"), "bid_strategy": row.get("bid_strategy"),
            "bid_amount": row.get("bid_amount"), "daily_budget": row.get("daily_budget"),
            "lifetime_budget": row.get("lifetime_budget"), "budget_remaining": row.get("budget_remaining"),
            "pacing_type": row.get("pacing_type"), "promoted_object": row.get("promoted_object"),
            "attribution_spec": row.get("attribution_spec"), "start_time": row.get("start_time"),
            "stop_time": row.get("end_time"), "created_time": row.get("created_time"),
            "updated_time": row.get("updated_time"), "raw_payload": row,
        })
    for row in metadata.get("ads", []):
        rows.append({
            "snapshot_date": snapshot_date.isoformat(), "client_id": client_id, "platform": "meta",
            "account_id": account_id, "entity_type": "ad", "entity_id": row["id"],
            "entity_name": row.get("name"), "parent_campaign_id": row.get("campaign_id"),
            "parent_adset_id": row.get("adset_id"), "status": row.get("status"),
            "effective_status": row.get("effective_status"), "configured_status": row.get("configured_status"),
            "created_time": row.get("created_time"), "updated_time": row.get("updated_time"),
            "raw_payload": row,
        })
    return rows


def targeting_rows(account, ad_sets: list[dict], snapshot_date: date):
    account_id = normalize_account_id(account["ad_account_id"])
    rows = []
    for row in ad_sets:
        targeting = row.get("targeting") or {}
        geo = targeting.get("geo_locations") or {}
        rows.append({
            "snapshot_date": snapshot_date.isoformat(), "client_id": account["client_id"], "platform": "meta",
            "account_id": account_id, "campaign_id": row.get("campaign_id"), "adset_id": row["id"],
            "adset_name": row.get("name"), "age_min": targeting.get("age_min"), "age_max": targeting.get("age_max"),
            "genders": targeting.get("genders"), "geo_locations": geo, "countries": geo.get("countries"),
            "regions": geo.get("regions"), "cities": geo.get("cities"), "languages": targeting.get("locales"),
            "interests": targeting.get("interests"), "behaviors": targeting.get("behaviors"),
            "custom_audiences": targeting.get("custom_audiences"),
            "excluded_custom_audiences": targeting.get("excluded_custom_audiences"),
            "flexible_spec": targeting.get("flexible_spec"), "exclusions": targeting.get("exclusions"),
            "publisher_platforms": targeting.get("publisher_platforms"),
            "facebook_positions": targeting.get("facebook_positions"),
            "instagram_positions": targeting.get("instagram_positions"),
            "audience_network_positions": targeting.get("audience_network_positions"),
            "messenger_positions": targeting.get("messenger_positions"),
            "device_platforms": targeting.get("device_platforms"), "user_os": targeting.get("user_os"),
            "raw_targeting": targeting, "raw_payload": row,
        })
    return rows


def nested_value(payload: dict, *path):
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_asset_text(asset_feed_spec: dict, key: str, value_key: str = "text"):
    items = asset_feed_spec.get(key) or []
    first = items[0] if items and isinstance(items[0], dict) else {}
    return first.get(value_key)


def creative_asset_rows(account, ads: list[dict]):
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
            "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
            "creative_id": creative_id,
            "headline": creative.get("title") or link_data.get("name") or video_data.get("title") or first_asset_text(asset_feed_spec, "titles"),
            "primary_text": creative.get("body") or link_data.get("message") or video_data.get("message") or first_asset_text(asset_feed_spec, "bodies"),
            "description": link_data.get("description") or first_asset_text(asset_feed_spec, "descriptions"),
            "cta_type": cta_type, "cta_text": cta_type,
            "destination_url": nested_value(link_data, "call_to_action", "value", "link") or link_data.get("link") or nested_value(video_data, "call_to_action", "value", "link"),
            "display_url": link_data.get("caption"), "link_url": link_data.get("link"),
            "image_url": creative.get("image_url") or link_data.get("image_url"),
            "thumbnail_url": creative.get("thumbnail_url"), "video_id": creative.get("video_id") or video_data.get("video_id"),
            "facebook_post_id": creative.get("object_story_id") or creative.get("effective_object_story_id"),
            "url_tags": creative.get("url_tags"), "raw_payload": creative,
        }
        assets.append({**base, "asset_kind": creative.get("object_type") or "creative", "asset_id": creative_id, "asset_hash": creative.get("image_hash") or ""})
        usage.append({
            "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
            "campaign_id": ad.get("campaign_id"), "adset_id": ad.get("adset_id"), "ad_id": ad["id"],
            "creative_id": creative_id, "creative_name": creative.get("name"),
            "effective_object_story_id": creative.get("effective_object_story_id"),
            "object_story_id": creative.get("object_story_id"), "object_story_spec": story_spec,
            "last_seen_at": datetime.utcnow().isoformat(), "raw_payload": creative,
        })
    return assets, usage


def sync_creative_tables(account, ads: list[dict]):
    assets, usage = creative_asset_rows(account, ads)
    return {
        "creative_assets": upsert_rows("meta_creative_assets", assets, "client_id,account_id,creative_id,asset_kind,asset_id,asset_hash"),
        "ad_creative_usage": upsert_rows("meta_ad_creative_usage", usage, "client_id,account_id,ad_id,creative_id"),
    }


def fetch_action_insights(account, target_date: date, action_breakdown: str | None = None):
    params = {
        "level": "ad",
        "time_increment": 1,
        "time_range": json.dumps({"since": target_date.isoformat(), "until": target_date.isoformat()}),
        "action_attribution_windows": json.dumps(list(ACTION_ATTRIBUTION_WINDOWS)),
    }
    if action_breakdown:
        params["action_breakdowns"] = json.dumps(["action_type", action_breakdown])
    return fetch_graph_edge(
        normalize_ad_account_id(account["ad_account_id"]) + "/insights",
        account["access_token"],
        [
            "date_start", "campaign_id", "campaign_name", "adset_id", "adset_name",
            "ad_id", "ad_name", "actions", "action_values", "cost_per_action_type",
        ],
        params,
    )


def action_metric(items: list[dict], action_type: str, window: str):
    item = next((item for item in items or [] if item.get("action_type") == action_type), {})
    key = "value" if window == "default" else window
    value = item.get(key)
    return None if value is None else to_float(value)


def normalize_action_insights(account, target_date: date, rows: list[dict]):
    output = []
    for row in rows:
        for action in row.get("actions") or []:
            action_type = action.get("action_type")
            if not action_type:
                continue
            windows = ["default", *[window for window in ACTION_ATTRIBUTION_WINDOWS if action.get(window) is not None]]
            for window in windows:
                value_key = "value" if window == "default" else window
                output.append({
                    "perf_date": row.get("date_start") or target_date.isoformat(),
                    "client_id": account["client_id"], "platform": "meta",
                    "account_id": normalize_account_id(account["ad_account_id"]),
                    "campaign_id": row.get("campaign_id") or "all", "campaign_name": row.get("campaign_name"),
                    "adset_id": row.get("adset_id") or "all", "adset_name": row.get("adset_name"),
                    "ad_id": row.get("ad_id") or "all", "ad_name": row.get("ad_name"),
                    "action_type": action_type, "action_destination": action.get("action_destination") or "all",
                    "action_device": action.get("action_device") or "all",
                    "action_target_id": action.get("action_target_id") or "all",
                    "action_reaction": action.get("action_reaction") or "all",
                    "action_video_type": action.get("action_video_type") or "all",
                    "value": to_float(action.get(value_key)),
                    "cost": action_metric(row.get("cost_per_action_type") or [], action_type, window),
                    "conversion_value": action_metric(row.get("action_values") or [], action_type, window) or 0,
                    "attribution_window": window,
                    "raw_payload": {
                        "action": action,
                        "costs": row.get("cost_per_action_type"),
                        "values": row.get("action_values"),
                    },
                })
    return output


def sync_action_insights(account, target_date: date):
    output = []
    errors = []
    for breakdown in (None, *ACTION_BREAKDOWNS):
        label = breakdown or "base"
        rows, error = safe_fetch(
            f"actions_{label}_{target_date.isoformat()}",
            lambda breakdown=breakdown: fetch_action_insights(account, target_date, breakdown),
        )
        if error:
            errors.append(error)
            continue
        output.extend(normalize_action_insights(account, target_date, rows))
    saved = upsert_rows(
        "meta_action_daily", output,
        "perf_date,client_id,account_id,campaign_id,adset_id,ad_id,action_type,action_destination,action_device,action_target_id,action_reaction,action_video_type,attribution_window",
    )
    return {"rows_saved": saved, "errors": errors}


def discover_managed_pages(account):
    pages, error = safe_fetch(
        "managed_pages",
        lambda: fetch_graph_edge("me/accounts", account["access_token"], ["id", "name", "access_token", "tasks"]),
    )
    pages_by_id = {page["id"]: page for page in pages if page.get("id")}

    # Creative story specs provide a fallback when /me/accounts is restricted.
    result = (
        supabase()
        .table("creatives")
        .select("raw_payload")
        .eq("client_id", account["client_id"])
        .eq("account_id", normalize_account_id(account["ad_account_id"]))
        .execute()
    )
    for row in result.data or []:
        page_id = ((row.get("raw_payload") or {}).get("object_story_spec") or {}).get("page_id")
        if page_id and page_id not in pages_by_id:
            pages_by_id[page_id] = {"id": page_id, "access_token": account["access_token"]}

    account["_managed_pages"] = list(pages_by_id.values())
    account["_managed_pages_error"] = error
    return account["_managed_pages"]


def fetch_lead_forms(account):
    fields = [
        "id", "name", "status", "locale", "questions", "privacy_policy_url",
        "thank_you_page", "context_card", "follow_up_action_url", "created_time",
    ]
    forms_by_id = {}
    errors = []
    for page in discover_managed_pages(account):
        page_id = page.get("id")
        page_token = page.get("access_token") or account["access_token"]
        forms, error = safe_fetch(
            f"page_{page_id}_leadgen_forms",
            lambda page_id=page_id, page_token=page_token: fetch_graph_edge(f"{page_id}/leadgen_forms", page_token, fields),
        )
        if error:
            errors.append(error)
            continue
        for form in forms:
            if form.get("id"):
                form["_page_id"] = page_id
                form["_page_name"] = page.get("name")
                form["_page_access_token"] = page_token
                forms_by_id[form["id"]] = form
    account["_lead_form_errors"] = errors
    return list(forms_by_id.values())


def sync_lead_forms(account, forms: list[dict]):
    rows = []
    for form in forms:
        questions = form.get("questions") or []
        rows.append({
            "client_id": account["client_id"], "platform": "meta",
            "account_id": normalize_account_id(account["ad_account_id"]),
            "page_id": form.get("_page_id"), "page_name": form.get("_page_name"),
            "form_id": form["id"], "form_name": form.get("name"), "status": form.get("status"),
            "locale": form.get("locale"), "question_count": len(questions), "questions": questions,
            "privacy_policy_url": form.get("privacy_policy_url"), "thank_you_screen": form.get("thank_you_page"),
            "context_card": form.get("context_card"), "follow_up_action_url": form.get("follow_up_action_url"),
            "created_time": form.get("created_time"),
            "raw_payload": {key: value for key, value in form.items() if not key.startswith("_")},
        })
    return upsert_rows("meta_lead_forms", rows, "client_id,account_id,form_id")


def fetch_form_leads(account, form: dict):
    return fetch_graph_edge(f"{form['id']}/leads", form.get("_page_access_token") or account["access_token"], [
        "id", "created_time", "field_data", "ad_id", "ad_name", "adset_id", "adset_name",
        "campaign_id", "campaign_name", "form_id", "is_organic", "platform",
    ])


def field_value(field_data: list[dict], names: set[str]):
    for item in field_data or []:
        if (item.get("name") or "").lower() in names:
            values = item.get("values") or []
            return values[0] if values else None
    return None


def sync_leads(account, forms: list[dict]):
    leads = []
    answers = []
    errors = []
    forms_by_id = {form.get("id"): form for form in forms}
    for form in forms:
        raw_leads, error = safe_fetch(
            f"lead_form_{form.get('id')}",
            lambda form=form: fetch_form_leads(account, form),
        )
        if error:
            errors.append(error)
            continue
        for lead in raw_leads:
            lead.setdefault("form_id", form.get("id"))
            field_data = lead.get("field_data") or []
            form_ref = forms_by_id.get(lead.get("form_id")) or {}
            leads.append({
                "client_id": account["client_id"], "platform": "meta", "account_id": normalize_account_id(account["ad_account_id"]),
                "lead_id": lead["id"], "form_id": lead.get("form_id"), "form_name": form_ref.get("name"),
                "page_id": form_ref.get("_page_id"), "page_name": form_ref.get("_page_name"),
                "campaign_id": lead.get("campaign_id"), "campaign_name": lead.get("campaign_name"),
                "adset_id": lead.get("adset_id"), "adset_name": lead.get("adset_name"),
                "ad_id": lead.get("ad_id"), "ad_name": lead.get("ad_name"), "lead_created_time": lead.get("created_time"),
                "field_data": field_data,
                "normalized_email": field_value(
                    field_data,
                    {"email", "e-mail", "work_email", "business_email", "company_email"},
                ),
                "normalized_phone": field_value(field_data, {"phone_number", "phone", "mobile"}),
                "normalized_name": field_value(field_data, {"full_name", "name", "first_name"}),
                "normalized_city": field_value(field_data, {"city"}), "normalized_country": field_value(field_data, {"country"}),
                "is_organic": lead.get("is_organic"), "source": lead.get("platform"), "raw_payload": lead,
            })
            for item in field_data:
                values = item.get("values") or []
                answers.append({
                    "client_id": account["client_id"], "account_id": normalize_account_id(account["ad_account_id"]),
                    "lead_id": lead["id"], "form_id": lead.get("form_id"), "field_name": item.get("name"),
                    "field_label": item.get("name"), "field_value": values[0] if values else None,
                    "field_values": values,
                })
    return {
        "leads": upsert_rows("meta_leads", leads, "client_id,account_id,lead_id"),
        "lead_answers": upsert_rows("meta_lead_answers", answers, "client_id,account_id,lead_id,field_name"),
        "lead_errors": errors,
    }


def fetch_pixel_details(account, pixels: list[dict]):
    groups = (
        ["id", "name", "last_fired_time", "is_unavailable", "creation_time"],
        ["id", "data_use_setting", "enable_automatic_matching", "automatic_matching_fields", "first_party_cookie_status"],
        ["id", "owner_ad_account", "owner_business"],
    )
    errors = []
    enriched = []
    for pixel in pixels:
        combined = dict(pixel)
        for index, fields in enumerate(groups):
            details, error = safe_fetch(
                f"pixel_{pixel.get('id')}_details_{index}",
                lambda fields=fields, pixel_id=pixel.get("id"): fetch_graph_object(pixel_id, account["access_token"], fields),
                fallback={},
            )
            if error:
                errors.append(error)
            else:
                combined.update(details)
        enriched.append(combined)
    return enriched, errors


def fetch_pixel_stats(account, pixel_id: str, target_date: date, aggregation: str):
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return fetch_graph_edge(
        f"{pixel_id}/stats", account["access_token"], params={
            "aggregation": aggregation,
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
        },
    )


def normalize_event_stats(account, pixel: dict, target_date: date, aggregation: str, rows: list[dict]):
    output = []
    for row in rows:
        series = row.get("data") or row.get("values")
        items = series if isinstance(series, list) and series else [row]
        for item in items:
            if not isinstance(item, dict):
                continue
            label = (
                item.get("event") or item.get("event_name") or item.get("name")
                or item.get("key") or item.get("value")
            )
            count = item.get("count", item.get("total"))
            if count is None and item is row:
                count = row.get("value")

            if aggregation == "event_source":
                event_name = "all"
                event_source = label or row.get("event_source") or row.get("source") or "unknown"
            else:
                event_name = label or "unknown"
                event_source = row.get("event_source") or row.get("source") or "all"

            output.append({
                "event_date": target_date.isoformat(), "client_id": account["client_id"], "platform": "meta",
                "account_id": normalize_account_id(account["ad_account_id"]), "source_type": "pixel",
                "source_id": pixel["id"], "source_name": pixel.get("name"),
                "event_name": str(event_name), "event_source": str(event_source),
                "event_count": to_int(count), "aggregation": aggregation,
                "raw_payload": {"stat": item, "response": row},
            })
    return output


def fetch_pixel_diagnostics(account, pixel_id: str):
    errors = []
    for edge in ("diagnostics", "da_checks"):
        rows, error = safe_fetch(
            f"pixel_{pixel_id}_{edge}",
            lambda edge=edge: fetch_graph_edge(f"{pixel_id}/{edge}", account["access_token"]),
        )
        if not error:
            return rows, errors
        errors.append(error)
    return [], errors


def normalize_pixel_diagnostic(account, pixel: dict, diagnostic: dict, snapshot_date: date, index: int):
    code = (
        diagnostic.get("code") or diagnostic.get("key") or diagnostic.get("id")
        or diagnostic.get("type") or diagnostic.get("name") or f"diagnostic_{index}"
    )
    return {
        "snapshot_date": snapshot_date.isoformat(), "client_id": account["client_id"], "platform": "meta",
        "account_id": normalize_account_id(account["ad_account_id"]), "source_type": "pixel",
        "source_id": pixel["id"], "source_name": pixel.get("name"), "diagnostic_code": str(code),
        "severity": diagnostic.get("severity") or diagnostic.get("level"),
        "title": diagnostic.get("title") or diagnostic.get("name"),
        "description": diagnostic.get("description") or diagnostic.get("message"),
        "status": diagnostic.get("status") or diagnostic.get("result"),
        "first_detected_at": parse_meta_timestamp(diagnostic.get("first_detected_at") or diagnostic.get("first_fired_time")),
        "last_detected_at": parse_meta_timestamp(diagnostic.get("last_detected_at") or diagnostic.get("last_fired_time")),
        "raw_payload": diagnostic,
    }


def sync_event_manager(account, pixels: list[dict], start_date: date, end_date: date, snapshot_date: date):
    event_rows = []
    diagnostic_rows = []
    successful_event_partitions = []
    errors = []
    for pixel in pixels:
        current = start_date
        while current <= end_date:
            for aggregation in ("event", "event_source"):
                stats, error = safe_fetch(
                    f"pixel_{pixel.get('id')}_{aggregation}_{current.isoformat()}",
                    lambda pixel_id=pixel.get("id"), current=current, aggregation=aggregation: fetch_pixel_stats(account, pixel_id, current, aggregation),
                )
                if error:
                    errors.append(error)
                else:
                    successful_event_partitions.append((pixel["id"], current, aggregation))
                    event_rows.extend(normalize_event_stats(account, pixel, current, aggregation, stats))
            current += timedelta(days=1)

        diagnostics, diagnostic_errors = fetch_pixel_diagnostics(account, pixel["id"])
        errors.extend(diagnostic_errors)
        for index, diagnostic in enumerate(diagnostics):
            diagnostic_rows.append(normalize_pixel_diagnostic(account, pixel, diagnostic, snapshot_date, index))

    account_id = normalize_account_id(account["ad_account_id"])
    for pixel_id, event_date, aggregation in successful_event_partitions:
        (
            supabase().table("meta_event_daily").delete()
            .eq("client_id", account["client_id"])
            .eq("account_id", account_id)
            .eq("source_type", "pixel")
            .eq("source_id", pixel_id)
            .eq("event_date", event_date.isoformat())
            .eq("aggregation", aggregation)
            .execute()
        )

    source_rows = [{
        "client_id": row["client_id"], "platform": "meta", "account_id": row["account_id"],
        "source_type": row["source_type"], "source_id": row["source_id"], "source_name": row.get("source_name"),
        "event_name": row["event_name"], "setup_status": "active", "raw_payload": row.get("raw_payload"),
    } for row in event_rows if row.get("event_name") != "unknown"]
    return {
        "event_daily": upsert_rows(
            "meta_event_daily", event_rows,
            "event_date,client_id,account_id,source_type,source_id,event_name,event_source",
        ),
        "event_diagnostics": upsert_rows(
            "meta_event_diagnostics_daily", diagnostic_rows,
            "snapshot_date,client_id,account_id,source_type,source_id,diagnostic_code",
        ),
        "event_source_events": upsert_rows(
            "meta_event_sources", source_rows,
            "client_id,account_id,source_type,source_id,event_name",
        ),
        "event_manager_errors": errors,
    }


def sync_account_assets(account, metadata: dict, forms: list[dict], snapshot_date: date, start_date: date, end_date: date):
    account_id = normalize_account_id(account["ad_account_id"])
    errors = []
    if account.get("_managed_pages_error"):
        errors.append(account["_managed_pages_error"])
    errors.extend(account.get("_lead_form_errors") or [])
    account_info, error = safe_fetch("ad_account", lambda: fetch_graph_object(normalize_ad_account_id(account["ad_account_id"]), account["access_token"], [
        "id", "name", "account_status", "disable_reason", "currency", "timezone_name",
        "timezone_offset_hours_utc", "amount_spent", "spend_cap", "balance", "business",
    ]), fallback={})
    if error:
        errors.append(error)
    pixels, error = safe_fetch("adspixels", lambda: fetch_graph_edge(normalize_ad_account_id(account["ad_account_id"]) + "/adspixels", account["access_token"], [
        "id", "name", "code", "last_fired_time", "is_unavailable",
    ]))
    if error:
        errors.append(error)
    pixels, pixel_detail_errors = fetch_pixel_details(account, pixels)
    errors.extend(pixel_detail_errors)
    pixel_rows = [{
        "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
        "pixel_id": pixel["id"], "pixel_name": pixel.get("name"),
        "dataset_id": pixel.get("dataset_id") or pixel["id"],
        "dataset_name": pixel.get("dataset_name") or pixel.get("name"),
        "owner_business_id": related_id(pixel.get("owner_business")),
        "owner_ad_account_id": related_id(pixel.get("owner_ad_account")),
        "creation_time": parse_meta_timestamp(pixel.get("creation_time")),
        "data_use_setting": pixel.get("data_use_setting"),
        "automatic_matching_enabled": pixel.get("enable_automatic_matching"),
        "automatic_matching_fields": pixel.get("automatic_matching_fields"),
        "first_party_cookie_status": pixel.get("first_party_cookie_status"),
        "last_fired_time": parse_meta_timestamp(pixel.get("last_fired_time")),
        "is_unavailable": pixel.get("is_unavailable"), "code": pixel.get("code"), "raw_payload": pixel,
    } for pixel in pixels if pixel.get("id")]
    event_rows = [{
        "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
        "source_type": "pixel", "source_id": pixel["id"], "source_name": pixel.get("name"),
        "event_name": "unknown", "last_received_at": parse_meta_timestamp(pixel.get("last_fired_time")),
        "setup_status": "unavailable" if pixel.get("is_unavailable") else "active", "raw_payload": pixel,
    } for pixel in pixels if pixel.get("id")]
    activities, error = safe_fetch("activities", lambda: fetch_graph_edge(normalize_ad_account_id(account["ad_account_id"]) + "/activities", account["access_token"], [
        "event_time", "event_type", "object_type", "object_id", "object_name",
        "actor_id", "actor_name", "application_id", "translated_event_type", "extra_data",
    ]))
    if error:
        errors.append(error)
    activity_rows = []
    for activity in activities:
        activity_id = activity.get("id") or f"{activity.get('event_time')}:{activity.get('event_type')}:{activity.get('object_id')}"
        activity_rows.append({
            "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
            "activity_id": activity_id, "event_time": parse_meta_timestamp(activity.get("event_time")),
            "event_type": activity.get("event_type"), "object_type": activity.get("object_type"),
            "object_id": activity.get("object_id"), "object_name": activity.get("object_name"),
            "actor_id": activity.get("actor_id"), "actor_name": activity.get("actor_name"),
            "application_id": activity.get("application_id"), "translated_event_type": activity.get("translated_event_type"),
            "extra_data": activity.get("extra_data"), "raw_payload": activity,
        })
    health = {
        "snapshot_date": snapshot_date.isoformat(), "client_id": account["client_id"], "platform": "meta",
        "account_id": account_id, "account_name": account_info.get("name") or account.get("ad_account_name"),
        "account_status": str(account_info.get("account_status")) if account_info.get("account_status") is not None else None,
        "disable_reason": str(account_info.get("disable_reason")) if account_info.get("disable_reason") is not None else None,
        "currency": account_info.get("currency"), "timezone_name": account_info.get("timezone_name"),
        "timezone_offset_hours_utc": account_info.get("timezone_offset_hours_utc"),
        "amount_spent": account_info.get("amount_spent"), "spend_cap": account_info.get("spend_cap"),
        "balance": account_info.get("balance"), "token_status": account.get("token_status"),
        "token_expires_at": account.get("token_expires_at"), "permissions": account.get("permissions"),
        "lead_forms_count": len(forms), "pixels_count": len(pixels),
        "active_campaigns_count": sum(1 for row in metadata.get("campaigns", []) if row.get("effective_status") == "ACTIVE"),
        "active_adsets_count": sum(1 for row in metadata.get("ad_sets", []) if row.get("effective_status") == "ACTIVE"),
        "active_ads_count": sum(1 for row in metadata.get("ads", []) if row.get("effective_status") == "ACTIVE"),
        "rejected_ads_count": sum(1 for row in metadata.get("ads", []) if row.get("effective_status") == "DISAPPROVED"),
        "last_successful_sync_at": datetime.utcnow().isoformat(), "raw_payload": {"account": account_info, "errors": errors},
    }
    assets = []
    if account_info.get("id"):
        assets.append({
            "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
            "asset_type": "ad_account", "asset_id": account_id, "asset_name": account_info.get("name"),
            "status": str(account_info.get("account_status") or "unknown"), "raw_payload": account_info,
        })
    for pixel in pixels:
        if pixel.get("id"):
            assets.append({
                "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
                "asset_type": "pixel", "asset_id": pixel["id"], "asset_name": pixel.get("name"),
                "status": "unavailable" if pixel.get("is_unavailable") else "active", "raw_payload": pixel,
            })
    for page in account.get("_managed_pages") or []:
        if page.get("id"):
            assets.append({
                "client_id": account["client_id"], "platform": "meta", "account_id": account_id,
                "asset_type": "page", "asset_id": page["id"], "asset_name": page.get("name"),
                "status": "active", "metadata": {"tasks": page.get("tasks") or []},
                "raw_payload": {key: value for key, value in page.items() if key != "access_token"},
            })
    event_manager = sync_event_manager(account, pixels, start_date, end_date, snapshot_date)
    errors.extend(event_manager.get("event_manager_errors") or [])
    return {
        "pixels": upsert_rows("meta_pixels", pixel_rows, "client_id,account_id,pixel_id"),
        "event_sources": upsert_rows("meta_event_sources", event_rows, "client_id,account_id,source_type,source_id,event_name"),
        "activities": upsert_rows("meta_account_activities", activity_rows, "client_id,account_id,activity_id"),
        "business_assets": upsert_rows("meta_business_assets", assets, "client_id,account_id,asset_type,asset_id"),
        "account_health": upsert_rows("meta_account_health_snapshots", [health], "snapshot_date,client_id,account_id"),
        **event_manager,
        "asset_errors": errors,
    }


def sync_form_health(account, forms: list[dict], snapshot_date: date):
    rows = []
    for form in forms:
        questions = form.get("questions") or []
        names = {((q.get("key") or q.get("label") or q.get("type") or "").lower()) for q in questions if isinstance(q, dict)}
        rows.append({
            "snapshot_date": snapshot_date.isoformat(), "client_id": account["client_id"], "platform": "meta",
            "account_id": normalize_account_id(account["ad_account_id"]), "form_id": form["id"],
            "form_name": form.get("name"), "status": form.get("status"), "question_count": len(questions),
            "has_phone": any("phone" in name for name in names), "has_email": any("email" in name for name in names),
            "has_custom_questions": len(questions) > 2, "follow_up_action_url": form.get("follow_up_action_url"),
            "raw_payload": form,
        })
    return upsert_rows("meta_form_health_snapshots", rows, "snapshot_date,client_id,account_id,form_id")


def sync_one_account(account, start_date: date, end_date: date):
    sync_run_id = create_sync_run(account, "daily_metadata", start_date, end_date)
    counters = {"rows_fetched": 0, "rows_inserted": 0, "rows_updated": 0, "metadata": {"dates": [], "errors": []}}
    try:
        snapshot_date = app_today()
        metadata = fetch_full_metadata(account)
        counters["metadata"]["dimensions"] = upsert_dimensions(account, metadata)
        counters["metadata"]["entity_snapshots"] = upsert_rows("meta_entity_snapshots_daily", snapshot_rows(account, metadata, snapshot_date), "snapshot_date,client_id,account_id,entity_type,entity_id")
        counters["metadata"]["targeting_snapshots"] = upsert_rows("meta_targeting_snapshots", targeting_rows(account, metadata.get("ad_sets", []), snapshot_date), "snapshot_date,client_id,account_id,adset_id")
        counters["metadata"].update(sync_creative_tables(account, metadata.get("ads", [])))

        ad_creatives = {ad["id"]: creative["creative_id"] for ad in metadata.get("ads", []) if (creative := creative_from_ad(ad))}
        current = start_date
        while current <= end_date:
            breakdowns, errors = sync_breakdown_rows(account, current, ad_creatives)
            action_result, action_error = safe_fetch(
                f"actions_{current.isoformat()}",
                lambda current=current: sync_action_insights(account, current),
                fallback={"rows_saved": 0, "errors": []},
            )
            if action_error:
                errors.append(action_error)
            errors.extend(action_result.get("errors") or [])
            counters["metadata"]["dates"].append({
                "date": current.isoformat(), "breakdowns": breakdowns,
                "action_rows": action_result.get("rows_saved", 0), "errors": errors,
            })
            counters["metadata"]["errors"].extend(errors)
            current += timedelta(days=1)

        forms, error = safe_fetch("leadgen_forms", lambda: fetch_lead_forms(account))
        if error:
            counters["metadata"]["errors"].append(error)
        counters["metadata"]["errors"].extend(account.get("_lead_form_errors") or [])
        counters["metadata"]["lead_forms"] = sync_lead_forms(account, forms)
        counters["metadata"].update(sync_leads(account, forms))
        counters["metadata"]["form_health"] = sync_form_health(account, forms, snapshot_date)
        counters["metadata"].update(sync_account_assets(account, metadata, forms, snapshot_date, start_date, end_date))

        now_iso = datetime.utcnow().isoformat()
        supabase().table("meta_accounts").update({
            "last_metadata_synced_at": now_iso,
            "last_breakdown_synced_at": now_iso,
            "last_health_synced_at": now_iso,
            "last_leads_synced_at": now_iso,
            "last_synced_at": now_iso,
        }).eq("id", account["id"]).execute()
        counters["rows_updated"] = sum(value for value in counters["metadata"].values() if isinstance(value, int))
        finish_sync_run(sync_run_id, "success", counters)
        return {"account_id": account["ad_account_id"], "status": "success", **counters}
    except Exception as exc:
        finish_sync_run(sync_run_id, "failed", counters, str(exc))
        raise


def run_daily_metadata_sync() -> dict:
    accounts = get_metadata_accounts()
    now = datetime.now(timezone.utc)
    today = app_today()
    end_date = today - timedelta(days=1)
    start_date = end_date - timedelta(days=max(settings.daily_lookback_days - 1, 0))
    results = []
    skipped = []
    for account in accounts:
        metadata_due, metadata_reason = account_lane_is_due(account, now, "last_metadata_synced_at", "metadata_sync_frequency_hours", 24)
        breakdown_due, breakdown_reason = account_lane_is_due(account, now, "last_breakdown_synced_at", "breakdown_sync_frequency_hours", 24)
        health_due, health_reason = account_lane_is_due(account, now, "last_health_synced_at", "health_sync_frequency_hours", 24)
        if not (metadata_due or breakdown_due or health_due):
            skipped.append({
                "account_id": account.get("ad_account_id"),
                "account_name": account.get("ad_account_name"),
                "reason": {"metadata": metadata_reason, "breakdowns": breakdown_reason, "health": health_reason},
            })
            continue
        result = sync_one_account(account, start_date, end_date)
        result["reason"] = {"metadata": metadata_reason, "breakdowns": breakdown_reason, "health": health_reason}
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
