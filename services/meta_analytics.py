from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from fastapi import HTTPException, status

from core.auth import WorkspaceContext
from core.supabase_client import get_supabase


PERFORMANCE_COLUMNS = (
    "perf_date,account_id,campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,"
    "creative_id,country,spend,impressions,clicks,reach,frequency,link_clicks,results,"
    "purchases,revenue"
)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    return int(_number(value))


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def metric_summary(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    spend = sum(_number(row.get("spend")) for row in rows)
    impressions = sum(_integer(row.get("impressions")) for row in rows)
    clicks = sum(_integer(row.get("clicks")) for row in rows)
    link_clicks = sum(_integer(row.get("link_clicks")) for row in rows)
    results = sum(_number(row.get("results")) for row in rows)
    purchases = sum(_number(row.get("purchases")) for row in rows)
    revenue = sum(_number(row.get("revenue")) for row in rows)
    reach = sum(_integer(row.get("reach")) for row in rows)
    return {
        "spend": _round(spend),
        "impressions": impressions,
        "clicks": clicks,
        "link_clicks": link_clicks,
        "results": _round(results),
        "purchases": _round(purchases),
        "revenue": _round(revenue),
        "reach": reach,
        "ctr": _round(clicks / impressions * 100 if impressions else None),
        "cpc": _round(spend / clicks if clicks else None),
        "cpm": _round(spend / impressions * 1000 if impressions else None),
        "cpl": _round(spend / results if results else None),
        "roas": _round(revenue / spend if spend else None),
        "frequency": _round(impressions / reach if reach else None),
    }


def metric_delta(current: dict, previous: dict) -> dict:
    output = {}
    for key, value in current.items():
        old_value = previous.get(key)
        if not isinstance(value, (int, float)) or old_value in (None, 0):
            output[key] = None
        else:
            output[key] = _round((value - old_value) / abs(old_value) * 100)
    return output


def date_window(date_from: date | None, date_to: date | None, days: int = 30) -> tuple[date, date]:
    end = date_to or date.today()
    start = date_from or (end - timedelta(days=days - 1))
    if start > end:
        raise HTTPException(status_code=422, detail="date_from must be before date_to.")
    if (end - start).days > 365:
        raise HTTPException(status_code=422, detail="Date ranges are limited to 366 days.")
    return start, end


def comparison_window(start: date, end: date, comparison: str) -> tuple[date, date] | None:
    if comparison == "off":
        return None
    days = (end - start).days + 1
    if comparison == "previous_month":
        previous_end = start.replace(day=1) - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
        return previous_start, previous_end
    if comparison == "previous_year":
        try:
            return start.replace(year=start.year - 1), end.replace(year=end.year - 1)
        except ValueError:
            return start - timedelta(days=365), end - timedelta(days=365)
    previous_end = start - timedelta(days=1)
    return previous_end - timedelta(days=days - 1), previous_end


def account_for_workspace(workspace: WorkspaceContext, account_id: str | None) -> dict:
    query = (
        get_supabase()
        .table("meta_accounts")
        .select(
            "ad_account_id,ad_account_name,is_active,last_synced_at,sync_frequency_hours,"
            "backfill_done,token_status,token_expires_at,permissions,geo_grain,enabled_breakdowns,"
            "last_performance_synced_at,last_leads_synced_at,last_metadata_synced_at,"
            "last_breakdown_synced_at,last_health_synced_at"
        )
        .eq("client_id", workspace.client_id)
    )
    if account_id:
        query = query.eq("ad_account_id", account_id)
    result = query.order("created_at").limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meta account not found in this workspace.")
    return result.data[0]


def list_accounts(workspace: WorkspaceContext) -> list[dict]:
    result = (
        get_supabase()
        .table("meta_accounts")
        .select(
            "ad_account_id,ad_account_name,is_active,last_synced_at,sync_frequency_hours,"
            "backfill_done,token_status,token_expires_at,permissions,geo_grain,enabled_breakdowns"
        )
        .eq("client_id", workspace.client_id)
        .order("created_at")
        .execute()
    )
    accounts = result.data or []
    for account in accounts:
        health = (
            get_supabase().table("meta_account_health_snapshots")
            .select("currency,timezone_name,account_status,disable_reason")
            .eq("client_id", workspace.client_id)
            .eq("account_id", account["ad_account_id"])
            .order("snapshot_at", desc=True).limit(1).execute().data or []
        )
        if health:
            account.update(health[0])
    return accounts


def performance_rows(
    workspace: WorkspaceContext,
    account_id: str,
    start: date,
    end: date,
    filters: dict[str, str | None] | None = None,
) -> list[dict]:
    rows = []
    page_size = 1000
    for offset in range(0, 50000, page_size):
        query = (
            get_supabase()
            .table("marketing_performance_daily")
            .select(PERFORMANCE_COLUMNS)
            .eq("client_id", workspace.client_id)
            .eq("account_id", account_id)
            .gte("perf_date", start.isoformat())
            .lte("perf_date", end.isoformat())
        )
        for key, value in (filters or {}).items():
            if value:
                query = query.eq(key, value)
        batch = query.limit(page_size).offset(offset).execute().data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows


def grouped_metrics(rows: Iterable[dict], key: str, name_key: str | None = None) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    names: dict[str, str] = {}
    for row in rows:
        entity_id = row.get(key)
        if not entity_id:
            continue
        entity_id = str(entity_id)
        grouped[entity_id].append(row)
        if name_key and row.get(name_key):
            names[entity_id] = str(row[name_key])
    output = []
    for entity_id, entity_rows in grouped.items():
        output.append({
            "id": entity_id,
            "name": names.get(entity_id) or entity_id,
            **metric_summary(entity_rows),
        })
    return output


def trend(rows: Iterable[dict]) -> list[dict]:
    return sorted(grouped_metrics(rows, "perf_date"), key=lambda item: item["id"])


def _dimension_rows(table: str, workspace: WorkspaceContext, account_id: str, columns: str) -> list[dict]:
    return (
        get_supabase().table(table).select(columns)
        .eq("client_id", workspace.client_id)
        .eq("account_id", account_id)
        .limit(10000).execute().data or []
    )


def entity_report(
    workspace: WorkspaceContext,
    account_id: str,
    start: date,
    end: date,
    entity: str,
    filters: dict[str, str | None],
) -> list[dict]:
    config = {
        "campaign": ("campaigns", "campaign_id", "campaign_name", "campaign_id,campaign_name,objective,status,effective_status,buying_type,daily_budget,lifetime_budget,start_time,stop_time"),
        "adset": ("ad_sets", "adset_id", "adset_name", "adset_id,adset_name,campaign_id,status,effective_status,optimization_goal,billing_event,bid_strategy,daily_budget,lifetime_budget,targeting,start_time,end_time"),
        "ad": ("ads", "ad_id", "ad_name", "ad_id,ad_name,campaign_id,adset_id,creative_id,status,effective_status,created_time,updated_time"),
    }
    table, entity_key, name_key, columns = config[entity]
    dimensions = _dimension_rows(table, workspace, account_id, columns)
    dimension_map = {str(row[entity_key]): row for row in dimensions}
    rows = performance_rows(workspace, account_id, start, end, filters)
    metrics = grouped_metrics(rows, entity_key, name_key)
    output = []
    seen = set()
    for item in metrics:
        seen.add(item["id"])
        output.append({**dimension_map.get(item["id"], {}), **item})
    for entity_id, dimension in dimension_map.items():
        if entity_id not in seen:
            output.append({**dimension, "id": entity_id, "name": dimension.get(name_key) or entity_id, **metric_summary([])})
    return output


def paginate_sort(items: list[dict], page: int, page_size: int, sort_by: str, sort_dir: str) -> dict:
    reverse = sort_dir == "desc"
    items.sort(key=lambda item: (item.get(sort_by) is not None, item.get(sort_by) or 0), reverse=reverse)
    total = len(items)
    start = (page - 1) * page_size
    return {"data": items[start:start + page_size], "page": page, "page_size": page_size, "total": total}


def breakdown_rows(
    workspace: WorkspaceContext,
    account_id: str,
    start: date,
    end: date,
    breakdown_type: str,
) -> list[dict]:
    rows = []
    page_size = 1000
    for offset in range(0, 50000, page_size):
        batch = (
            get_supabase().table("marketing_performance_breakdowns")
            .select("*")
            .eq("client_id", workspace.client_id)
            .eq("account_id", account_id)
            .eq("breakdown_type", breakdown_type)
            .gte("perf_date", start.isoformat())
            .lte("perf_date", end.isoformat())
            .limit(page_size).offset(offset).execute().data or []
        )
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows


def iso_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None
