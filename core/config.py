import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None = os.getenv("SUPABASE_URL")
    supabase_service_role_key: str | None = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    meta_graph_api_version: str = os.getenv("META_GRAPH_API_VERSION", "v20.0")
    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")
    daily_lookback_days: int = int(os.getenv("META_DAILY_LOOKBACK_DAYS", "3"))
    backfill_days: int = int(os.getenv("META_BACKFILL_DAYS", "90"))
    cron_secret: str | None = os.getenv("CRON_SECRET")

    @property
    def supabase_ready(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


settings = Settings()
