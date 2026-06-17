import base64
import json
from functools import lru_cache

from supabase import create_client

from core.config import settings


def decode_jwt_payload(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception as exc:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is not a valid Supabase JWT. "
            "In Railway, paste the actual service_role key from Supabase Project Settings > API, "
            "not the key name, project ref, anon key, URL, or a redacted value."
        ) from exc


def validate_supabase_settings():
    if not settings.supabase_url:
        raise RuntimeError("Missing SUPABASE_URL in Railway variables.")
    if not settings.supabase_service_role_key:
        raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY in Railway variables.")
    if not settings.supabase_url.startswith("https://"):
        raise RuntimeError("SUPABASE_URL should look like https://<project-ref>.supabase.co.")

    key = settings.supabase_service_role_key
    if key.startswith("sb_secret_"):
        return

    if key.count(".") != 2:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is not a valid Supabase JWT. "
            "Paste either the new sb_secret_ key or the legacy service_role JWT from Supabase Project Settings > API."
        )

    payload = decode_jwt_payload(key)
    role = payload.get("role")
    if role != "service_role":
        raise RuntimeError(
            f"SUPABASE_SERVICE_ROLE_KEY has role={role!r}. "
            "This backend needs the service_role key, not the anon/public key."
        )


@lru_cache
def get_supabase():
    validate_supabase_settings()

    return create_client(settings.supabase_url, settings.supabase_service_role_key)
