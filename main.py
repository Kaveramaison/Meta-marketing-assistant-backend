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

    # Exchange code for token
    token_response = requests.get(
        "https://graph.facebook.com/v20.0/oauth/access_token",
        params={
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )

    token_data = token_response.json()

    access_token = token_data.get("access_token")

    if not access_token:
        return {
            "error": "Failed to get access token",
            "details": token_data,
        }

    # Fetch ad accounts
    ad_accounts_response = requests.get(
        "https://graph.facebook.com/v20.0/me/adaccounts",
        params={
            "access_token": access_token,
            "fields": "id,name,account_status,currency,timezone_name",
        },
    )

    ad_accounts = ad_accounts_response.json()

    return {
        "message": "Meta connected successfully",
        "ad_accounts": ad_accounts,
    }
