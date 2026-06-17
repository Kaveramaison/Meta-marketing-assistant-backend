from fastapi import APIRouter, Header, HTTPException

from core.config import settings
from services.meta_sync import run_backfill_sync, run_daily_sync, run_scheduled_sync
from services.meta_warehouse_sync import run_daily_metadata_sync

router = APIRouter(prefix="/jobs", tags=["jobs"])


def verify_cron_secret(x_cron_secret: str | None):
    if settings.cron_secret and x_cron_secret != settings.cron_secret:
        raise HTTPException(status_code=401, detail="Invalid cron secret")


@router.post("/meta/daily")
def trigger_meta_daily(x_cron_secret: str | None = Header(default=None)):
    verify_cron_secret(x_cron_secret)
    return run_daily_sync()


@router.post("/meta/backfill")
def trigger_meta_backfill(days: int | None = None, x_cron_secret: str | None = Header(default=None)):
    verify_cron_secret(x_cron_secret)
    return run_backfill_sync(days=days)


@router.post("/meta/scheduled")
def trigger_meta_scheduled(x_cron_secret: str | None = Header(default=None)):
    verify_cron_secret(x_cron_secret)
    return run_scheduled_sync()


@router.post("/meta/daily-metadata")
def trigger_meta_daily_metadata(x_cron_secret: str | None = Header(default=None)):
    verify_cron_secret(x_cron_secret)
    return run_daily_metadata_sync()
