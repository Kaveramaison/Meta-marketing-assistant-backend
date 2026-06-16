from fastapi import FastAPI
from routes.meta_test import router as meta_test_router

app = FastAPI(
    title="AI Marketing OS Backend",
    version="0.1.0"
)

app.include_router(meta_test_router)

@app.get("/")
def home():
    return {"status": "working"}

@app.get("/health")
def health():
    return {"status": "healthy"}
