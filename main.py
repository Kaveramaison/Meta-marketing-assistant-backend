from fastapi import FastAPI

from routes.auth import router as auth_router
from routes.meta import router as meta_router
from routes.google_ads import router as google_ads_router
from routes.claude import router as claude_router

app = FastAPI(
    title="AI Marketing OS Backend",
    version="0.1.0"
)

app.include_router(auth_router)
app.include_router(meta_router)
app.include_router(google_ads_router)
app.include_router(claude_router)


@app.get("/")
def home():
    return {
        "status": "working",
        "service": "ai-marketing-os-backend"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }
