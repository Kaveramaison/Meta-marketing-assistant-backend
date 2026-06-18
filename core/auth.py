from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from fastapi import Header, HTTPException, status

from core.config import settings
from core.supabase_client import get_supabase


@dataclass(frozen=True)
class WorkspaceContext:
    user_id: str
    email: str | None
    client_id: str
    client_name: str
    role: str


def _access_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in is required.",
        )
    return authorization.split(" ", 1)[1].strip()


def _accept_pending_invitations(user: dict) -> str | None:
    email = user.get("email")
    if not email or not user.get("email_confirmed_at"):
        return None

    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_supabase()
        .table("workspace_invitations")
        .select("id, client_id, role")
        .ilike("email", email)
        .eq("status", "pending")
        .gt("expires_at", now)
        .order("created_at")
        .execute()
    )
    invitations = result.data or []
    for invitation in invitations:
        get_supabase().table("client_users").upsert(
            {
                "client_id": invitation["client_id"],
                "user_id": user["id"],
                "email": email,
                "role": invitation["role"],
                "accepted_at": now,
            },
            on_conflict="client_id,user_id",
        ).execute()
        get_supabase().table("workspace_invitations").update(
            {
                "status": "accepted",
                "accepted_by": user["id"],
                "accepted_at": now,
                "updated_at": now,
            }
        ).eq("id", invitation["id"]).execute()
    return invitations[0]["client_id"] if invitations else None


def get_workspace_context(
    authorization: str | None = Header(default=None),
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
) -> WorkspaceContext:
    token = _access_token(authorization)
    response = requests.get(
        f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
        headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {token}",
        },
        timeout=20,
    )
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is invalid or expired.",
        )

    user = response.json()
    accepted_client_id = _accept_pending_invitations(user)
    membership_query = (
        get_supabase()
        .table("client_users")
        .select("client_id, role")
        .eq("user_id", user["id"])
    )
    selected_workspace_id = x_workspace_id or accepted_client_id
    if selected_workspace_id:
        membership_query = membership_query.eq("client_id", selected_workspace_id)
    membership_result = membership_query.order("created_at").limit(1).execute()
    memberships = membership_result.data or []
    if not memberships:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No workspace is assigned to this user.",
        )

    membership = memberships[0]
    client_result = (
        get_supabase()
        .table("clients")
        .select("client_id, client_name")
        .eq("client_id", membership["client_id"])
        .limit(1)
        .execute()
    )
    clients = client_result.data or []
    if not clients:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The assigned workspace no longer exists.",
        )

    client = clients[0]
    return WorkspaceContext(
        user_id=user["id"],
        email=user.get("email"),
        client_id=client["client_id"],
        client_name=client["client_name"],
        role=membership["role"],
    )


def require_workspace_manager(workspace: WorkspaceContext) -> WorkspaceContext:
    if workspace.role not in {"owner", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner or admin access is required.",
        )
    return workspace
