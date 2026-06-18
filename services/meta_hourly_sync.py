from datetime import datetime, timezone

from services.meta_sync import run_scheduled_sync as run_performance_scheduled_sync
from services.meta_sync import run_backfill_sync
from core.config import settings
from services.meta_warehouse_sync import account_lane_is_due, fetch_lead_forms, safe_fetch, supabase, sync_lead_forms, sync_leads


def get_lead_accounts():
    result = (
        supabase()
        .table("meta_accounts")
        .select(
            "id, client_id, ad_account_id, ad_account_name, access_token, is_active, backfill_done, "
            "leads_sync_frequency_hours, last_leads_synced_at"
        )
        .eq("is_active", True)
        .eq("backfill_done", True)
        .execute()
    )
    return result.data or []


def run_lead_sync_for_due_accounts(now: datetime):
    results = []
    skipped = []
    for account in get_lead_accounts():
        due, reason = account_lane_is_due(account, now, "last_leads_synced_at", "leads_sync_frequency_hours", 4)
        if not due:
            skipped.append({
                "account_id": account.get("ad_account_id"),
                "account_name": account.get("ad_account_name"),
                "reason": reason,
                "leads_sync_frequency_hours": account.get("leads_sync_frequency_hours") or 4,
                "last_leads_synced_at": account.get("last_leads_synced_at"),
            })
            continue

        forms, error = safe_fetch("leadgen_forms", lambda account=account: fetch_lead_forms(account))
        errors = [error] if error else []
        errors.extend(account.get("_lead_form_errors") or [])
        result = {
            "account_id": account.get("ad_account_id"),
            "account_name": account.get("ad_account_name"),
            "reason": reason,
            "forms": sync_lead_forms(account, forms),
            **sync_leads(account, forms),
            "errors": errors,
        }
        supabase().table("meta_accounts").update({"last_leads_synced_at": datetime.utcnow().isoformat()}).eq("id", account["id"]).execute()
        results.append(result)
    return {
        "accounts_checked": len(results) + len(skipped),
        "accounts_synced": len(results),
        "accounts_skipped": len(skipped),
        "results": results,
        "skipped": skipped,
    }


def run_scheduled_sync() -> dict:
    now = datetime.now(timezone.utc)
    onboarding = run_backfill_sync(days=settings.initial_backfill_days)
    performance = run_performance_scheduled_sync()
    leads = run_lead_sync_for_due_accounts(now)
    return {
        "mode": "scheduled",
        "checked_at": now.isoformat(),
        "onboarding": onboarding,
        "performance": performance,
        "leads": leads,
    }
