import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from core.auth import WorkspaceContext, get_workspace_context
from core.config import settings
from core.supabase_client import get_supabase
from services.email_notifications import send_team_email

router = APIRouter(prefix="/auth/meta", tags=["meta-auth"])


class ConnectMetaRequest(BaseModel):
    session_id: str
    account_ids: list[str] = Field(min_length=1, max_length=25)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _oauth_ready():
    if not settings.meta_app_id or not settings.meta_app_secret or not settings.meta_oauth_state_secret:
        raise HTTPException(status_code=503, detail="Meta OAuth is not configured on the backend.")


def _sign_state(workspace: WorkspaceContext) -> str:
    _oauth_ready()
    payload = {
        "user_id": workspace.user_id,
        "client_id": workspace.client_id,
        "exp": int(time.time()) + 600,
        "nonce": secrets.token_urlsafe(18),
    }
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(
        settings.meta_oauth_state_secret.encode(), encoded.encode(), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64encode(signature)}"


def _verify_state(value: str) -> dict:
    _oauth_ready()
    try:
        encoded, supplied_signature = value.split(".", 1)
        expected = hmac.new(
            settings.meta_oauth_state_secret.encode(), encoded.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(supplied_signature)):
            raise ValueError("signature")
        payload = json.loads(_b64decode(encoded))
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception as exc:
        raise HTTPException(status_code=400, detail="The Meta connection request is invalid or expired.") from exc


def _graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_api_version}/{path.lstrip('/')}"


def _graph_get(path: str, access_token: str, **params):
    response = requests.get(
        _graph_url(path), params={"access_token": access_token, **params}, timeout=45
    )
    data = response.json()
    if response.status_code >= 400 or data.get("error"):
        message = data.get("error", {}).get("message", "Meta API request failed.")
        raise HTTPException(status_code=400, detail=message)
    return data


def _fetch_accounts(access_token: str) -> list[dict]:
    data = _graph_get(
        "me/adaccounts",
        access_token,
        fields="id,account_id,name,currency,timezone_name,account_status,business",
        limit=200,
    )
    return [
        {
            "account_id": str(row.get("account_id") or row.get("id", "").replace("act_", "")),
            "account_name": row.get("name") or "Meta ad account",
            "currency": row.get("currency"),
            "timezone_name": row.get("timezone_name"),
            "account_status": row.get("account_status"),
            "business": row.get("business"),
        }
        for row in data.get("data", [])
        if row.get("account_id") or row.get("id")
    ]


@router.get("/start")
def start_meta_oauth(workspace: WorkspaceContext = Depends(get_workspace_context)):
    state_value = _sign_state(workspace)
    scopes = [
        "ads_read",
        "business_management",
        "pages_show_list",
        "pages_read_engagement",
        "leads_retrieval",
        "pages_manage_metadata",
    ]
    oauth_params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": settings.meta_redirect_uri,
        "state": state_value,
        "response_type": "code",
    }
    if settings.meta_login_config_id:
        oauth_params.update(
            {
                "config_id": settings.meta_login_config_id,
                "override_default_response_type": "true",
            }
        )
    else:
        oauth_params["scope"] = ",".join(scopes)
    query = urlencode(oauth_params)
    return {"authorization_url": f"https://www.facebook.com/{settings.meta_graph_api_version}/dialog/oauth?{query}"}


@router.get("/callback")
def meta_oauth_callback(code: str = Query(), state_value: str = Query(alias="state")):
    try:
        state_data = _verify_state(state_value)
        token_response = requests.get(
            _graph_url("oauth/access_token"),
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": settings.meta_redirect_uri,
                "code": code,
            },
            timeout=45,
        )
        token_data = token_response.json()
        if token_response.status_code >= 400 or not token_data.get("access_token"):
            raise HTTPException(status_code=400, detail="Meta did not return an access token.")

        short_token = token_data["access_token"]
        long_response = requests.get(
            _graph_url("oauth/access_token"),
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=45,
        )
        long_data = long_response.json()
        access_token = long_data.get("access_token") or short_token
        expires_in = int(long_data.get("expires_in") or token_data.get("expires_in") or 0)
        token_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            if expires_in
            else None
        )

        permission_data = _graph_get("me/permissions", access_token).get("data", [])
        permissions = [
            row["permission"] for row in permission_data if row.get("status") == "granted"
        ]
        accounts = _fetch_accounts(access_token)
        if not accounts:
            raise HTTPException(status_code=400, detail="No accessible Meta ad accounts were found.")

        session_result = get_supabase().table("meta_connection_sessions").insert(
            {
                "client_id": state_data["client_id"],
                "user_id": state_data["user_id"],
                "access_token": access_token,
                "token_expires_at": token_expires_at.isoformat() if token_expires_at else None,
                "permissions": permissions,
                "accounts": accounts,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
            }
        ).execute()
        session_id = session_result.data[0]["id"]
        return RedirectResponse(
            f"{settings.app_frontend_url}/dashboard/meta/connect?session={session_id}",
            status_code=status.HTTP_302_FOUND,
        )
    except HTTPException as exc:
        message = urlencode({"meta_error": exc.detail})
        return RedirectResponse(
            f"{settings.app_frontend_url}/dashboard?{message}",
            status_code=status.HTTP_302_FOUND,
        )


def _connection_session(session_id: str, workspace: WorkspaceContext) -> dict:
    result = (
        get_supabase()
        .table("meta_connection_sessions")
        .select("*")
        .eq("id", session_id)
        .eq("client_id", workspace.client_id)
        .eq("user_id", workspace.user_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Meta connection session was not found.")
    connection = rows[0]
    expires_at = datetime.fromisoformat(connection["expires_at"].replace("Z", "+00:00"))
    if expires_at < datetime.now(timezone.utc) or connection.get("status") != "pending":
        raise HTTPException(status_code=410, detail="Meta connection session has expired.")
    return connection


def _connected_workspace(account_id: str, current_client_id: str) -> dict | None:
    result = (
        get_supabase()
        .table("meta_accounts")
        .select("id, client_id, ad_account_id, ad_account_name")
        .eq("ad_account_id", account_id)
        .limit(10)
        .execute()
    )
    return next(
        (row for row in (result.data or []) if row["client_id"] != current_client_id),
        None,
    )


def _pending_request(account_id: str, target_client_id: str, user_id: str) -> dict | None:
    result = (
        get_supabase()
        .table("workspace_access_requests")
        .select("id, status")
        .eq("requester_user_id", user_id)
        .eq("target_client_id", target_client_id)
        .eq("ad_account_id", account_id)
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    return (result.data or [None])[0]


def _notify_workspace_owners(request: dict, requester: WorkspaceContext):
    recipients_result = (
        get_supabase()
        .table("client_users")
        .select("email")
        .eq("client_id", request["target_client_id"])
        .in_("role", ["owner", "admin"])
        .execute()
    )
    recipients = list(
        dict.fromkeys(row["email"] for row in (recipients_result.data or []) if row.get("email"))
    )
    sent, email_error = send_team_email(
        to=recipients,
        subject=f"New request to join your Kavera Maison organization",
        heading="New team access request",
        message=(
            f"{requester.email or 'A verified user'} requested access through Meta account "
            f"{request.get('ad_account_name') or request['ad_account_id']}. Review the request before sharing your organization data."
        ),
        action_label="Review request",
        idempotency_key=f"workspace-access-request-{request['id']}",
    )
    get_supabase().table("workspace_access_requests").update(
        {
            "owner_notified_at": datetime.now(timezone.utc).isoformat() if sent else None,
            "owner_notification_error": email_error,
        }
    ).eq("id", request["id"]).execute()


@router.get("/session/{session_id}")
def get_meta_connection_session(
    session_id: str,
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    connection = _connection_session(session_id, workspace)
    accounts = []
    for account in connection.get("accounts") or []:
        existing = _connected_workspace(str(account["account_id"]), workspace.client_id)
        request = (
            _pending_request(str(account["account_id"]), existing["client_id"], workspace.user_id)
            if existing
            else None
        )
        accounts.append(
            {
                **account,
                "connection_status": "connected_elsewhere" if existing else "available",
                "access_request_status": request["status"] if request else None,
            }
        )
    return {"session_id": session_id, "accounts": accounts}


@router.post("/connect")
def connect_meta_accounts(
    payload: ConnectMetaRequest,
    workspace: WorkspaceContext = Depends(get_workspace_context),
):
    connection = _connection_session(payload.session_id, workspace)
    available = {
        str(account["account_id"]): account for account in (connection.get("accounts") or [])
    }
    unknown = [account_id for account_id in payload.account_ids if account_id not in available]
    if unknown:
        raise HTTPException(status_code=400, detail="One or more selected accounts are unavailable.")

    account_rows = []
    requested_accounts = []
    for account_id in dict.fromkeys(payload.account_ids):
        account = available[account_id]
        existing_elsewhere = _connected_workspace(account_id, workspace.client_id)
        if existing_elsewhere:
            existing_request = _pending_request(
                account_id, existing_elsewhere["client_id"], workspace.user_id
            )
            if not existing_request:
                request_result = get_supabase().table("workspace_access_requests").insert(
                    {
                        "requester_user_id": workspace.user_id,
                        "requester_client_id": workspace.client_id,
                        "requester_email": workspace.email,
                        "target_client_id": existing_elsewhere["client_id"],
                        "ad_account_id": account_id,
                        "ad_account_name": account["account_name"],
                    }
                ).execute()
                _notify_workspace_owners(request_result.data[0], workspace)
            requested_accounts.append(account_id)
            continue

        current_result = (
            get_supabase()
            .table("meta_accounts")
            .select("id")
            .eq("client_id", workspace.client_id)
            .eq("ad_account_id", account_id)
            .limit(1)
            .execute()
        )
        if current_result.data:
            get_supabase().table("meta_accounts").update(
                {
                    "ad_account_name": account["account_name"],
                    "access_token": connection["access_token"],
                    "token_expires_at": connection.get("token_expires_at"),
                    "token_status": "active",
                    "permissions": connection.get("permissions") or [],
                    "is_active": True,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", current_result.data[0]["id"]).execute()
            continue

        account_rows.append(
            {
                "client_id": workspace.client_id,
                "ad_account_id": account_id,
                "ad_account_name": account["account_name"],
                "access_token": connection["access_token"],
                "token_expires_at": connection.get("token_expires_at"),
                "token_status": "active",
                "permissions": connection.get("permissions") or [],
                "is_active": True,
                "backfill_done": False,
            }
        )

    saved_count = 0
    if account_rows:
        saved = get_supabase().table("meta_accounts").insert(account_rows).execute()
        saved_count = len(saved.data or account_rows)
    get_supabase().table("meta_connection_sessions").update(
        {"status": "consumed", "consumed_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", payload.session_id).execute()
    return {
        "status": "access_requested" if requested_accounts and not saved_count else "connected",
        "accounts": saved_count,
        "access_requests": len(requested_accounts),
        "initial_backfill_days": settings.initial_backfill_days,
    }
