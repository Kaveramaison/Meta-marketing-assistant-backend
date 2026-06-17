from functools import lru_cache

from supabase import create_client

from core.config import settings


@lru_cache
def get_supabase():
    if not settings.supabase_ready:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

    return create_client(settings.supabase_url, settings.supabase_service_role_key)
