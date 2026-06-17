from datetime import timedelta

from fastapi import APIRouter, Query

from core.supabase_client import get_supabase
from services.meta_sync import app_today, round_or_none

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview")
def overview(client_id: str = Query(...), days: int = Query(30, ge=1, le=365)):
    start_date = (app_today() - timedelta(days=days - 1)).isoformat()
    result = (
        get_supabase()
        .table("marketing_performance_daily")
        .select("perf_date, spend, impressions, clicks, results")
        .eq("client_id", client_id)
        .gte("perf_date", start_date)
        .limit(10000)
        .execute()
    )
    rows = result.data or []
    spend = sum(float(row.get("spend") or 0) for row in rows)
    impressions = sum(int(row.get("impressions") or 0) for row in rows)
    clicks = sum(int(row.get("clicks") or 0) for row in rows)
    results_count = sum(int(row.get("results") or 0) for row in rows)
    return {
        "date_from": min([row["perf_date"] for row in rows], default=None),
        "date_to": max([row["perf_date"] for row in rows], default=None),
        "spend": round_or_none(spend, 2),
        "impressions": impressions,
        "clicks": clicks,
        "results": results_count,
        "ctr": round_or_none((clicks / impressions) * 100 if impressions else None),
        "cpc": round_or_none(spend / clicks if clicks else None),
        "cpl": round_or_none(spend / results_count if results_count else None),
    }


@router.get("/insights")
def insights(client_id: str = Query(...), limit: int = Query(20, ge=1, le=100)):
    result = (
        get_supabase()
        .table("insights")
        .select("*")
        .eq("client_id", client_id)
        .order("insight_date", desc=True)
        .limit(limit)
        .execute()
    )
    return {"data": result.data or []}
