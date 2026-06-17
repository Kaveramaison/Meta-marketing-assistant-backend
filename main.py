from fastapi import FastAPI

from core.config import settings
from routes.dashboard import router as dashboard_router
from routes.jobs import router as jobs_router

app = FastAPI(title="AI Marketing OS Backend", version="0.2.0")

app.include_router(dashboard_router)
app.include_router(jobs_router)


@app.get("/")
def home():
    return {
        "status": "working",
        "service": "AI Marketing OS Backend",
        "version": "0.2.0",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "supabase_configured": settings.supabase_ready,
        "timezone": settings.default_timezone,
        "daily_lookback_days": settings.daily_lookback_days,
    }
