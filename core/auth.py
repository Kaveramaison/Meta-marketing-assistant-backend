from dataclasses import dataclass

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


def get_workspace_context(
    authorization: str | None = Header(default=None),
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
    membership_result = (
        get_supabase()
        .table("client_users")
        .select("client_id, role")
        .eq("user_id", user["id"])
        .order("created_at")
        .limit(1)
        .execute()
    )
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
