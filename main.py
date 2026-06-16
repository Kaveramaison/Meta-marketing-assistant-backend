from fastapi import FastAPI

app = FastAPI(
    title="AI Marketing OS Backend",
    version="0.1.0"
)

@app.get("/")
def home():
    return {"status": "working"}

@app.get("/health")
def health():
    return {"status": "healthy"}
