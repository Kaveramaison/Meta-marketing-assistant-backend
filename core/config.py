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


def list_env(name: str, default: str = "") -> list[str]:
    raw_value = clean_env_value(os.getenv(name)) or default
    return [item.strip() for item in raw_value.split(",") if item.strip()]


DEFAULT_FRONTEND_ORIGINS = (
    "https://kavera-maison-web.vercel.app",
    "https://kaveramaison.com",
    "https://www.kaveramaison.com",
    "http://localhost:3000",
    "http://localhost:3007",
)


def frontend_origins() -> tuple[str, ...]:
    origins = [*DEFAULT_FRONTEND_ORIGINS, *list_env("FRONTEND_URL")]
    return tuple(dict.fromkeys(origins))


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None = first_env("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
    supabase_service_role_key: str | None = first_env(
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_SECRET_KEY",
    )
    meta_graph_api_version: str = os.getenv("META_GRAPH_API_VERSION", "v20.0")
    meta_app_id: str | None = first_env("META_APP_ID")
    meta_app_secret: str | None = first_env("META_APP_SECRET")
    meta_login_config_id: str | None = first_env("META_LOGIN_CONFIG_ID")
    meta_redirect_uri: str = os.getenv(
        "META_REDIRECT_URI",
        "https://meta-marketing-assistant-backend-production.up.railway.app/auth/meta/callback",
    )
    meta_oauth_state_secret: str | None = first_env("META_OAUTH_STATE_SECRET", "META_APP_SECRET")
    app_frontend_url: str = os.getenv("APP_FRONTEND_URL", "https://www.kaveramaison.com").rstrip("/")
    resend_api_key: str | None = first_env("RESEND_API_KEY")
    email_from: str = os.getenv(
        "EMAIL_FROM", "Kavera Maison <team@kaveramaison.com>"
    )
    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")
    daily_lookback_days: int = int(os.getenv("META_DAILY_LOOKBACK_DAYS", "3"))
    backfill_days: int = int(os.getenv("META_BACKFILL_DAYS", "90"))
    initial_backfill_days: int = int(os.getenv("META_INITIAL_BACKFILL_DAYS", "180"))
    cron_secret: str | None = os.getenv("CRON_SECRET")
    frontend_origins: tuple[str, ...] = frontend_origins()

    @property
    def supabase_ready(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def email_ready(self) -> bool:
        return bool(self.resend_api_key)


settings = Settings()
