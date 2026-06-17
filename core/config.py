import os
from dataclasses import dataclass


def clean_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip('"').strip("'")
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    return cleaned or None


def first_env(*names: str) -> str | None:
    for name in names:
        value = clean_env_value(os.getenv(name))
        if value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None = first_env("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
    supabase_service_role_key: str | None = first_env(
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SECRET_KEY",
    )
    meta_graph_api_version: str = os.getenv("META_GRAPH_API_VERSION", "v20.0")
    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")
    daily_lookback_days: int = int(os.getenv("META_DAILY_LOOKBACK_DAYS", "3"))
    backfill_days: int = int(os.getenv("META_BACKFILL_DAYS", "90"))
    cron_secret: str | None = os.getenv("CRON_SECRET")

    @property
    def supabase_ready(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


settings = Settings()
