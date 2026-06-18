from html import escape

import requests

from core.config import settings


def send_team_email(
    *,
    to: str | list[str],
    subject: str,
    heading: str,
    message: str,
    idempotency_key: str,
    action_label: str = "Open Kavera Maison",
    action_path: str = "/dashboard/team",
) -> tuple[bool, str | None]:
    recipients = [to] if isinstance(to, str) else [email for email in to if email]
    if not recipients:
        return False, "No recipient email is available."
    if not settings.resend_api_key:
        return False, "RESEND_API_KEY is not configured."

    action_url = f"{settings.app_frontend_url}{action_path}"
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key[:256],
        },
        json={
            "from": settings.email_from,
            "to": recipients,
            "subject": subject,
            "html": f"""
                <div style="background:#f5f6f4;padding:40px 20px;font-family:Arial,sans-serif;color:#18201e">
                  <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #d9ddd9;padding:32px">
                    <p style="margin:0 0 18px;color:#177a66;font-weight:700">Kavera Maison</p>
                    <h1 style="font-size:24px;margin:0 0 16px">{escape(heading)}</h1>
                    <p style="font-size:16px;line-height:1.6;color:#56605b">{escape(message)}</p>
                    <a href="{escape(action_url)}" style="display:inline-block;margin-top:20px;background:#177a66;color:#ffffff;text-decoration:none;padding:12px 18px;font-weight:700">{escape(action_label)}</a>
                  </div>
                </div>
            """,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        return False, f"Resend returned {response.status_code}: {response.text[:300]}"
    return True, None
