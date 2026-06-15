from fastapi import FastAPI
from routes.auth import router as auth_router

app = FastAPI(
    title="AI Marketing OS Backend",
    version="0.1.0"
)

app.include_router(auth_router)

@app.get("/")
def home():
    return {
        "status": "working",
        "service": "ai-marketing-os-backend"
    }

@app.get("/health")
def health():
    return {"status": "healthy"}
