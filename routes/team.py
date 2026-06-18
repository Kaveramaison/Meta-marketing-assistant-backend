from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from core.auth import WorkspaceContext, get_workspace_context, require_workspace_manager
from core.supabase_client import get_supabase

router = APIRouter(prefix="/team", tags=["team"])


def _request_for_workspace(request_id: str, workspace: WorkspaceContext) -> dict:
    result = (
        get_supabase()
        .table("workspace_access_requests")
        .select("*")
        .eq("id", request_id)
        .eq("target_client_id", workspace.client_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Access request was not found.")
    return result.data[0]


@router.get("")
def team(workspace: WorkspaceContext = Depends(get_workspace_context)):
    members_result = (
        get_supabase()
        .table("client_users")
        .select("id, user_id, email, role, accepted_at, created_at")
        .eq("client_id", workspace.client_id)
        .order("created_at")
        .execute()
    )
    requests = []
    if workspace.role in {"owner", "admin"}:
        requests_result = (
            get_supabase()
            .table("workspace_access_requests")
            .select("id, requester_email, ad_account_id, ad_account_name, status, created_at")
            .eq("target_client_id", workspace.client_id)
            .eq("status", "pending")
            .order("created_at")
            .execute()
        )
        requests = requests_result.data or []
    return {
        "workspace": {
            "client_id": workspace.client_id,
            "client_name": workspace.client_name,
            "role": workspace.role,
        },
        "members": members_result.data or [],
        "requests": requests,
    }


@router.post("/requests/{request_id}/approve")
def approve_request(
    request_id: str,
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_manager(workspace)
    request = _request_for_workspace(request_id, workspace)
    if request["status"] != "pending":
        raise HTTPException(status_code=409, detail="This request has already been decided.")

    now = datetime.now(timezone.utc).isoformat()
    get_supabase().table("client_users").upsert(
        {
            "client_id": workspace.client_id,
            "user_id": request["requester_user_id"],
            "email": request.get("requester_email"),
            "role": "viewer",
            "accepted_at": now,
        },
        on_conflict="client_id,user_id",
    ).execute()
    get_supabase().table("workspace_access_requests").update(
        {
            "status": "approved",
            "decided_at": now,
            "decided_by": workspace.user_id,
        }
    ).eq("id", request_id).execute()
    return {"status": "approved", "workspace_id": workspace.client_id}


@router.post("/requests/{request_id}/reject")
def reject_request(
    request_id: str,
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_manager(workspace)
    request = _request_for_workspace(request_id, workspace)
    if request["status"] != "pending":
        raise HTTPException(status_code=409, detail="This request has already been decided.")
    get_supabase().table("workspace_access_requests").update(
        {
            "status": "rejected",
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "decided_by": workspace.user_id,
        }
    ).eq("id", request_id).execute()
    return {"status": "rejected"}
