from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from routes.dashboard import router as dashboard_router
from routes.jobs import router as jobs_router
from routes.meta_auth import router as meta_auth_router
from routes.meta_analytics import router as meta_analytics_router
from routes.team import router as team_router

app = FastAPI(title="AI Marketing OS Backend", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.frontend_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)
app.include_router(jobs_router)
app.include_router(meta_auth_router)
app.include_router(meta_analytics_router)
app.include_router(team_router)


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
