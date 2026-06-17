import base64
import json
from functools import lru_cache
from types import SimpleNamespace

import requests
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


class RestQuery:
    def __init__(self, client, table_name: str):
        self.client = client
        self.table_name = table_name
        self.method = "GET"
        self.params = {}
        self.headers = {}
        self.payload = None

    def select(self, columns: str):
        self.method = "GET"
        self.params["select"] = columns
        return self

    def insert(self, payload):
        self.method = "POST"
        self.payload = payload
        self.headers["Prefer"] = "return=representation"
        return self

    def update(self, payload):
        self.method = "PATCH"
        self.payload = payload
        self.headers["Prefer"] = "return=representation"
        return self

    def upsert(self, payload, on_conflict: str | None = None):
        self.method = "POST"
        self.payload = payload
        self.headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        if on_conflict:
            self.params["on_conflict"] = on_conflict
        return self

    def eq(self, column: str, value):
        self.params[column] = f"eq.{self._format_value(value)}"
        return self

    def gte(self, column: str, value):
        self.params[column] = f"gte.{self._format_value(value)}"
        return self

    def limit(self, count: int):
        self.params["limit"] = str(count)
        return self

    def order(self, column: str, desc: bool = False):
        direction = "desc" if desc else "asc"
        self.params["order"] = f"{column}.{direction}"
        return self

    def execute(self):
        response = requests.request(
            self.method,
            f"{self.client.rest_url}/{self.table_name}",
            headers={**self.client.headers, **self.headers},
            params=self.params,
            json=self.payload,
            timeout=60,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"Supabase REST request failed for {self.table_name}: "
                f"{response.status_code} {response.text}"
            )
        return SimpleNamespace(data=response.json() if response.text else [])

    @staticmethod
    def _format_value(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        return str(value)


class RestSupabaseClient:
    def __init__(self, supabase_url: str, supabase_key: str):
        self.rest_url = f"{supabase_url.rstrip('/')}/rest/v1"
        self.headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        }

    def table(self, table_name: str):
        return RestQuery(self, table_name)


@lru_cache
def get_supabase():
    validate_supabase_settings()
    if settings.supabase_service_role_key.startswith("sb_secret_"):
        return RestSupabaseClient(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )

    return create_client(settings.supabase_url, settings.supabase_service_role_key)
