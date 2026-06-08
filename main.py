import os
import requests
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

app = FastAPI()

META_APP_ID = os.getenv("META_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")
BACKEND_URL = os.getenv("BACKEND_URL")
FRONTEND_URL = os.getenv("FRONTEND_URL")

@app.get("/")
def home():
    return {"status": "working"}

@app.get("/auth/meta/start")
def meta_start():
    redirect_uri = f"{BACKEND_URL}/auth/meta/callback"

    meta_login_url = (
        "https://www.facebook.com/v20.0/dialog/oauth"
        f"?client_id={META_APP_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=ads_read"
        f"&response_type=code"
    )

    return RedirectResponse(meta_login_url)

@app.get("/auth/meta/callback")
def meta_callback(request: Request):
    code = request.query_params.get("code")

    if not code:
        return {"error": "No code received from Meta"}

    redirect_uri = f"{BACKEND_URL}/auth/meta/callback"

    token_url = "https://graph.facebook.com/v20.0/oauth/access_token"

    params = {
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "redirect_uri": redirect_uri,
        "code": code,
    }

    response = requests.get(token_url, params=params)
    token_data = response.json()

    return {
        "message": "Meta OAuth connected",
        "token_data": token_data
    }
