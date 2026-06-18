from datetime import timedelta

from fastapi import APIRouter, Depends, Query

from core.auth import WorkspaceContext, get_workspace_context
from core.supabase_client import get_supabase
from services.meta_sync import app_today, round_or_none

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(
    days: int = Query(30, ge=1, le=365),
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    start_date = (app_today() - timedelta(days=days - 1)).isoformat()
    result = (
        get_supabase()
        .table("marketing_performance_daily")
        .select("perf_date, spend, impressions, clicks, results")
        .eq("client_id", workspace.client_id)
        .gte("perf_date", start_date)
        .limit(10000)
        .execute()
    )
    rows = result.data or []
    spend = sum(float(row.get("spend") or 0) for row in rows)
    impressions = sum(int(row.get("impressions") or 0) for row in rows)
    clicks = sum(int(row.get("clicks") or 0) for row in rows)
    results_count = sum(int(row.get("results") or 0) for row in rows)
    return {
        "date_from": min([row["perf_date"] for row in rows], default=None),
        "date_to": max([row["perf_date"] for row in rows], default=None),
        "spend": round_or_none(spend, 2),
        "impressions": impressions,
        "clicks": clicks,
        "results": results_count,
        "ctr": round_or_none((clicks / impressions) * 100 if impressions else None),
        "cpc": round_or_none(spend / clicks if clicks else None),
        "cpl": round_or_none(spend / results_count if results_count else None),
    }


@router.get("/insights")
def insights(
    limit: int = Query(20, ge=1, le=100),
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    result = (
        get_supabase()
        .table("insights")
        .select("*")
        .eq("client_id", workspace.client_id)
        .order("insight_date", desc=True)
        .limit(limit)
        .execute()
    )
    return {"data": result.data or []}


@router.get("/context")
def context(workspace: WorkspaceContext = Depends(get_workspace_context)):
    memberships_result = (
        get_supabase()
        .table("client_users")
        .select("client_id, role")
        .eq("user_id", workspace.user_id)
        .order("created_at")
        .execute()
    )
    workspaces = []
    for membership in memberships_result.data or []:
        client_result = (
            get_supabase()
            .table("clients")
            .select("client_id, client_name")
            .eq("client_id", membership["client_id"])
            .limit(1)
            .execute()
        )
        if client_result.data:
            workspaces.append({**client_result.data[0], "role": membership["role"]})

    outgoing_result = (
        get_supabase()
        .table("workspace_access_requests")
        .select("id, ad_account_id, ad_account_name, status, created_at")
        .eq("requester_user_id", workspace.user_id)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .execute()
    )
    incoming_count = 0
    if workspace.role in {"owner", "admin"}:
        incoming_result = (
            get_supabase()
            .table("workspace_access_requests")
            .select("id")
            .eq("target_client_id", workspace.client_id)
            .eq("status", "pending")
            .execute()
        )
        incoming_count = len(incoming_result.data or [])
    accounts_result = (
        get_supabase()
        .table("meta_accounts")
        .select(
            "ad_account_id, ad_account_name, is_active, last_synced_at, "
            "sync_frequency_hours, backfill_done"
        )
        .eq("client_id", workspace.client_id)
        .order("created_at")
        .execute()
    )
    return {
        "workspace": {
            "client_id": workspace.client_id,
            "client_name": workspace.client_name,
            "role": workspace.role,
        },
        "user": {"id": workspace.user_id, "email": workspace.email},
        "workspaces": workspaces,
        "pending_access_requests": outgoing_result.data or [],
        "pending_team_requests": incoming_count,
        "meta_accounts": [
            {
                "account_id": account["ad_account_id"],
                "account_name": account.get("ad_account_name"),
                "is_active": account.get("is_active"),
                "last_synced_at": account.get("last_synced_at"),
                "sync_frequency_hours": account.get("sync_frequency_hours"),
                "backfill_done": account.get("backfill_done"),
            }
            for account in (accounts_result.data or [])
        ],
    }
