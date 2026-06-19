from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, Query

from core.auth import WorkspaceContext, get_workspace_context
from core.supabase_client import get_supabase
from services.meta_analytics import (
    account_for_workspace,
    breakdown_rows,
    comparison_window,
    date_window,
    entity_report,
    grouped_metrics,
    list_accounts,
    metric_delta,
    metric_summary,
    paginate_sort,
    performance_rows,
    trend,
)

router = APIRouter(prefix="/meta", tags=["meta-analytics"])


def common_filters(campaign_id=None, adset_id=None, ad_id=None, country=None):
    return {"campaign_id": campaign_id, "adset_id": adset_id, "ad_id": ad_id, "country": country}


@router.get("/accounts")
def accounts(workspace: WorkspaceContext = Depends(get_workspace_context)):
    return {"data": list_accounts(workspace)}


@router.get("/overview")
def overview(
    account_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    comparison: str = Query("previous_period", pattern="^(previous_period|previous_month|previous_year|off)$"),
    campaign_id: str | None = None,
    adset_id: str | None = None,
    ad_id: str | None = None,
    country: str | None = None,
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    filters = common_filters(campaign_id, adset_id, ad_id, country)
    rows = performance_rows(workspace, account["ad_account_id"], start, end, filters)
    current = metric_summary(rows)
    previous_range = comparison_window(start, end, comparison)
    previous_rows = []
    previous = metric_summary([])
    if previous_range:
        previous_rows = performance_rows(workspace, account["ad_account_id"], *previous_range, filters)
        previous = metric_summary(previous_rows)
    campaigns = grouped_metrics(rows, "campaign_id", "campaign_name")
    campaigns.sort(key=lambda item: item.get("spend") or 0, reverse=True)
    activities = (
        get_supabase().table("meta_account_activities")
        .select("activity_id,event_time,event_type,object_type,object_id,object_name,actor_name,translated_event_type")
        .eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"])
        .order("event_time", desc=True).limit(12).execute().data or []
    )
    return {
        "account": account,
        "date_from": start.isoformat(), "date_to": end.isoformat(),
        "available_from": min((row["perf_date"] for row in rows), default=None),
        "available_to": max((row["perf_date"] for row in rows), default=None),
        "current": current, "previous": previous,
        "delta": metric_delta(current, previous),
        "trend": trend(rows), "previous_trend": trend(previous_rows),
        "top_performers": sorted(campaigns, key=lambda item: (item.get("results") or 0, -(item.get("cpl") or 10**12)), reverse=True)[:5],
        "bottom_performers": sorted([item for item in campaigns if item.get("spend")], key=lambda item: (item.get("results") or 0, -(item.get("cpl") or 0)))[:5],
        "activities": activities,
    }


def entity_endpoint(entity, account_id, date_from, date_to, page, page_size, sort_by, sort_dir,
                    campaign_id, adset_id, ad_id, country, workspace):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    items = entity_report(
        workspace, account["ad_account_id"], start, end, entity,
        common_filters(campaign_id, adset_id, ad_id, country),
    )
    return {"account": account, "date_from": start.isoformat(), "date_to": end.isoformat(), **paginate_sort(items, page, page_size, sort_by, sort_dir)}


@router.get("/campaigns")
def campaigns(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
              sort_by: str = "spend", sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
              status: str | None = None, objective: str | None = None, country: str | None = None,
              workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    items = entity_report(
        workspace, account["ad_account_id"], start, end, "campaign",
        common_filters(None, None, None, country),
    )
    if status: items = [item for item in items if item.get("effective_status") == status or item.get("status") == status]
    if objective: items = [item for item in items if item.get("objective") == objective]
    return {
        "account": account,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        **paginate_sort(items, page, page_size, sort_by, sort_dir),
    }


@router.get("/ad-sets")
def ad_sets(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
            campaign_id: str | None = None, country: str | None = None,
            page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
            sort_by: str = "spend", sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
            workspace: WorkspaceContext = Depends(get_workspace_context)):
    return entity_endpoint("adset", account_id, date_from, date_to, page, page_size, sort_by, sort_dir, campaign_id, None, None, country, workspace)


@router.get("/ads")
def ads(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
        campaign_id: str | None = None, adset_id: str | None = None, country: str | None = None,
        page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
        sort_by: str = "spend", sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
        workspace: WorkspaceContext = Depends(get_workspace_context)):
    return entity_endpoint("ad", account_id, date_from, date_to, page, page_size, sort_by, sort_dir, campaign_id, adset_id, None, country, workspace)


@router.get("/creatives")
def creatives(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
              campaign_id: str | None = None, adset_id: str | None = None, ad_id: str | None = None,
              page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100),
              sort_by: str = "spend", sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
              workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    rows = performance_rows(workspace, account["ad_account_id"], start, end, common_filters(campaign_id, adset_id, ad_id, None))
    metrics = {item["id"]: item for item in grouped_metrics(rows, "creative_id")}
    metadata = (
        get_supabase().table("creatives").select("creative_id,name,title,body,call_to_action,image_url,video_id,thumbnail_url,asset_type,created_time")
        .eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).limit(10000).execute().data or []
    )
    items = [{**item, **metrics.get(str(item["creative_id"]), metric_summary([])), "id": str(item["creative_id"])} for item in metadata]
    return {"account": account, "date_from": start.isoformat(), "date_to": end.isoformat(), **paginate_sort(items, page, page_size, sort_by, sort_dir)}


@router.get("/geography")
def geography(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
              grain: str | None = Query(None, pattern="^(country|region|city)$"),
              workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    selected_grain = grain or account.get("geo_grain") or "country"
    if selected_grain == "country":
        rows = performance_rows(workspace, account["ad_account_id"], start, end)
        items = grouped_metrics(rows, "country")
    else:
        rows = breakdown_rows(workspace, account["ad_account_id"], start, end, f"geo_{selected_grain}")
        items = grouped_metrics(rows, selected_grain)
    items.sort(key=lambda item: item.get("spend") or 0, reverse=True)
    return {"account": account, "grain": selected_grain, "date_from": start.isoformat(), "date_to": end.isoformat(), "trend": trend(rows), "data": items}


@router.get("/audience")
def audience(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
             dimension: str = Query("placement", pattern="^(placement|platform|device|age|gender|hourly)$"),
             workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    config = {
        "placement": ("placement", "placement"), "platform": ("placement", "publisher_platform"),
        "device": ("device", "impression_device"), "age": ("demographic", "age"),
        "gender": ("demographic", "gender"), "hourly": ("hourly", "hourly_window"),
    }
    breakdown_type, field = config[dimension]
    rows = breakdown_rows(workspace, account["ad_account_id"], start, end, breakdown_type)
    items = grouped_metrics(rows, field)
    items.sort(key=lambda item: item.get("spend") or 0, reverse=True)
    return {"account": account, "dimension": dimension, "date_from": start.isoformat(), "date_to": end.isoformat(), "trend": trend(rows), "data": items}


@router.get("/forms")
def forms(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
          workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    forms_data = (
        get_supabase().table("meta_lead_forms").select("form_id,form_name,page_id,page_name,status,locale,question_count,questions,privacy_policy_url,follow_up_action_url,created_time,updated_time")
        .eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).limit(10000).execute().data or []
    )
    leads = (
        get_supabase().table("meta_leads").select("lead_id,form_id,lead_created_time,campaign_id,adset_id,ad_id,is_organic,lead_status")
        .eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"])
        .gte("lead_created_time", f"{start.isoformat()}T00:00:00Z").lte("lead_created_time", f"{end.isoformat()}T23:59:59Z")
        .limit(50000).execute().data or []
    )
    counts = defaultdict(int)
    for lead in leads: counts[str(lead.get("form_id"))] += 1
    return {"account": account, "date_from": start.isoformat(), "date_to": end.isoformat(), "summary": {"forms": len(forms_data), "leads": len(leads)}, "trend": grouped_metrics(leads, "lead_created_time"), "data": [{**item, "leads": counts[str(item["form_id"])]} for item in forms_data]}


@router.get("/leads")
def leads(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
          form_id: str | None = None, campaign_id: str | None = None,
          page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
          workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    query = (
        get_supabase().table("meta_leads").select("lead_id,form_id,form_name,page_id,page_name,campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,lead_created_time,normalized_name,normalized_email,normalized_phone,normalized_city,normalized_country,is_organic,source,lead_status,field_data")
        .eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"])
        .gte("lead_created_time", f"{start.isoformat()}T00:00:00Z").lte("lead_created_time", f"{end.isoformat()}T23:59:59Z")
    )
    if form_id: query = query.eq("form_id", form_id)
    if campaign_id: query = query.eq("campaign_id", campaign_id)
    items = query.order("lead_created_time", desc=True).limit(50000).execute().data or []
    return {"account": account, "date_from": start.isoformat(), "date_to": end.isoformat(), **paginate_sort(items, page, page_size, "lead_created_time", "desc")}


@router.get("/events")
def events(account_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
           workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    start, end = date_window(date_from, date_to)
    sources = get_supabase().table("meta_event_sources").select("*").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).limit(1000).execute().data or []
    daily = get_supabase().table("meta_event_daily").select("*").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).gte("event_date", start.isoformat()).lte("event_date", end.isoformat()).limit(10000).execute().data or []
    diagnostics = get_supabase().table("meta_event_diagnostics_daily").select("*").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).gte("snapshot_date", start.isoformat()).lte("snapshot_date", end.isoformat()).order("snapshot_date", desc=True).limit(1000).execute().data or []
    return {"account": account, "date_from": start.isoformat(), "date_to": end.isoformat(), "summary": {"sources": len(sources), "events": sum(int(item.get("event_count") or 0) for item in daily), "diagnostics": len(diagnostics)}, "sources": sources, "daily": daily, "diagnostics": diagnostics}


@router.get("/health")
def health(account_id: str | None = None, workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    client = get_supabase()
    snapshots = client.table("meta_account_health_snapshots").select("*").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).order("snapshot_at", desc=True).limit(30).execute().data or []
    activities = client.table("meta_account_activities").select("*").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).order("event_time", desc=True).limit(100).execute().data or []
    runs = client.table("sync_runs").select("*").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).order("started_at", desc=True).limit(100).execute().data or []
    return {"account": account, "latest": snapshots[0] if snapshots else None, "snapshots": snapshots, "activities": activities, "sync_runs": runs}


@router.get("/filter-options")
def filter_options(account_id: str | None = None, workspace: WorkspaceContext = Depends(get_workspace_context)):
    account = account_for_workspace(workspace, account_id)
    client = get_supabase()
    campaigns = client.table("campaigns").select("campaign_id,campaign_name,objective,status,effective_status").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).limit(10000).execute().data or []
    adsets = client.table("ad_sets").select("adset_id,adset_name,campaign_id,status,effective_status").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).limit(10000).execute().data or []
    ads = client.table("ads").select("ad_id,ad_name,adset_id,campaign_id,status,effective_status,creative_id").eq("client_id", workspace.client_id).eq("account_id", account["ad_account_id"]).limit(10000).execute().data or []
    return {"account": account, "campaigns": campaigns, "ad_sets": adsets, "ads": ads, "objectives": sorted({item.get("objective") for item in campaigns if item.get("objective")}), "statuses": sorted({item.get("effective_status") or item.get("status") for item in campaigns + adsets + ads if item.get("effective_status") or item.get("status")})}
