from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.auth import WorkspaceContext, get_workspace_context, require_workspace_manager
from core.supabase_client import get_supabase
from services.email_notifications import send_team_email

router = APIRouter(prefix="/team", tags=["team"])


class InviteMemberRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="viewer", pattern="^(admin|viewer)$")


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
    invitations = []
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
        invitations_result = (
            get_supabase()
            .table("workspace_invitations")
            .select("id, email, role, status, expires_at, email_sent_at, email_error, created_at")
            .eq("client_id", workspace.client_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .execute()
        )
        invitations = invitations_result.data or []
    return {
        "workspace": {
            "client_id": workspace.client_id,
            "client_name": workspace.client_name,
            "role": workspace.role,
        },
        "members": members_result.data or [],
        "requests": requests,
        "invitations": invitations,
    }


@router.post("/invitations")
def invite_member(
    payload: InviteMemberRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_manager(workspace)
    email = payload.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")

    member_result = (
        get_supabase()
        .table("client_users")
        .select("id")
        .eq("client_id", workspace.client_id)
        .ilike("email", email)
        .limit(1)
        .execute()
    )
    if member_result.data:
        raise HTTPException(status_code=409, detail="This person is already a team member.")

    pending_result = (
        get_supabase()
        .table("workspace_invitations")
        .select("id")
        .eq("client_id", workspace.client_id)
        .ilike("email", email)
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    if pending_result.data:
        raise HTTPException(status_code=409, detail="An invitation is already pending for this email.")

    invitation_result = get_supabase().table("workspace_invitations").insert(
        {
            "client_id": workspace.client_id,
            "email": email,
            "role": payload.role,
            "invited_by": workspace.user_id,
        }
    ).execute()
    invitation = invitation_result.data[0]
    sent, email_error = send_team_email(
        to=email,
        subject=f"You are invited to {workspace.client_name} on Kavera Maison",
        heading=f"Join {workspace.client_name}",
        message=(
            f"{workspace.email or 'A workspace owner'} invited you to join their "
            f"Kavera Maison organization as {payload.role}. Sign in with this email to accept."
        ),
        action_label="Accept invitation",
        action_path="/login?next=/dashboard",
        idempotency_key=f"workspace-invitation-{invitation['id']}",
    )
    now = datetime.now(timezone.utc).isoformat()
    get_supabase().table("workspace_invitations").update(
        {
            "email_sent_at": now if sent else None,
            "email_error": email_error,
            "updated_at": now,
        }
    ).eq("id", invitation["id"]).execute()
    return {"status": "invited", "email_sent": sent, "invitation_id": invitation["id"]}


@router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(
    invitation_id: str,
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    require_workspace_manager(workspace)
    result = (
        get_supabase()
        .table("workspace_invitations")
        .select("id, status")
        .eq("id", invitation_id)
        .eq("client_id", workspace.client_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Invitation was not found.")
    if result.data[0]["status"] != "pending":
        raise HTTPException(status_code=409, detail="This invitation is no longer pending.")
    get_supabase().table("workspace_invitations").update(
        {"status": "revoked", "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", invitation_id).execute()
    return {"status": "revoked"}


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
    sent, email_error = send_team_email(
        to=request.get("requester_email") or [],
        subject=f"Your request to join {workspace.client_name} was approved",
        heading="Access approved",
        message=f"You can now open {workspace.client_name} and use its shared Meta dashboard.",
        idempotency_key=f"access-request-approved-{request_id}",
    )
    get_supabase().table("workspace_access_requests").update(
        {
            "decision_notified_at": now if sent else None,
            "decision_notification_error": email_error,
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
    now = datetime.now(timezone.utc).isoformat()
    get_supabase().table("workspace_access_requests").update(
        {
            "status": "rejected",
            "decided_at": now,
            "decided_by": workspace.user_id,
        }
    ).eq("id", request_id).execute()
    sent, email_error = send_team_email(
        to=request.get("requester_email") or [],
        subject=f"Update on your request to join {workspace.client_name}",
        heading="Access request declined",
        message=f"The owner of {workspace.client_name} did not approve this access request.",
        action_path="/dashboard",
        idempotency_key=f"access-request-rejected-{request_id}",
    )
    get_supabase().table("workspace_access_requests").update(
        {
            "decision_notified_at": now if sent else None,
            "decision_notification_error": email_error,
        }
    ).eq("id", request_id).execute()
    return {"status": "rejected"}
