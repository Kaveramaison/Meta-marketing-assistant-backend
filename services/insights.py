from collections import defaultdict
from datetime import timedelta

from core.supabase_client import get_supabase
from services.meta_sync import app_today, round_or_none


def _metric_rows(client_id=None):
    start_date = (app_today() - timedelta(days=14)).isoformat()
    query = (
        get_supabase()
        .table("marketing_performance_daily")
        .select("perf_date, client_id, platform, account_id, campaign_id, campaign_name, ad_id, ad_name, country, spend, impressions, clicks, reach, results")
        .gte("perf_date", start_date)
        .limit(10000)
    )
    if client_id:
        query = query.eq("client_id", client_id)
    return query.execute().data or []


def _aggregate(rows, key_fields, name_field=None):
    grouped = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0, "reach": 0, "results": 0, "name": None})
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        item = grouped[key]
        item["spend"] += float(row.get("spend") or 0)
        item["impressions"] += int(row.get("impressions") or 0)
        item["clicks"] += int(row.get("clicks") or 0)
        item["reach"] += int(row.get("reach") or 0)
        item["results"] += int(row.get("results") or 0)
        item["name"] = row.get(name_field) if name_field else row.get(key_fields[-1])
    return grouped


def _metrics(item):
    return {
        "ctr": (item["clicks"] / item["impressions"]) * 100 if item["impressions"] else None,
        "cpc": item["spend"] / item["clicks"] if item["clicks"] else None,
        "cpl": item["spend"] / item["results"] if item["results"] else None,
        "frequency": item["impressions"] / item["reach"] if item["reach"] else None,
    }


def _existing_keys(client_id, insight_date):
    result = (
        get_supabase()
        .table("insights")
        .select("entity_type, entity_id, insight_type")
        .eq("client_id", client_id)
        .eq("insight_date", insight_date)
        .execute()
    )
    return {(row["entity_type"], row.get("entity_id"), row["insight_type"]) for row in (result.data or [])}


def _insert(rows):
    if not rows:
        return 0
    result = get_supabase().table("insights").insert(rows).execute()
    return len(result.data or [])


def generate_basic_insights(client_id: str | None = None) -> dict:
    rows = _metric_rows(client_id)
    if not rows:
        return {"created": 0, "reason": "no_recent_rows"}

    created_rows = []
    for current_client_id in sorted({row["client_id"] for row in rows}):
        client_rows = [row for row in rows if row["client_id"] == current_client_id]
        latest_date = max(row["perf_date"] for row in client_rows)
        dates = sorted({row["perf_date"] for row in client_rows})
        recent_dates = set(dates[-7:])
        previous_dates = set(dates[-14:-7])
        recent_rows = [row for row in client_rows if row["perf_date"] in recent_dates]
        previous_rows = [row for row in client_rows if row["perf_date"] in previous_dates]
        existing = _existing_keys(current_client_id, latest_date)

        account_totals = _aggregate(recent_rows, ["client_id", "platform", "account_id"])
        account_cpl = {key: _metrics(item)["cpl"] for key, item in account_totals.items()}

        campaign_totals = _aggregate(recent_rows, ["client_id", "platform", "account_id", "campaign_id"], "campaign_name")
        for key, item in campaign_totals.items():
            account_key = key[:3]
            avg_cpl = account_cpl.get(account_key)
            metrics = _metrics(item)
            if item["spend"] < 100:
                continue
            insight_type = None
            severity = "medium"
            title = None
            diagnosis = None
            recommendation = None
            if item["results"] == 0:
                insight_type = "campaign_efficiency_risk"
                severity = "high"
                title = "Campaign efficiency risk"
                diagnosis = "Spend is happening without recorded results."
                recommendation = "Review targeting, offer, and tracking before adding more budget."
            elif avg_cpl and metrics["cpl"] and metrics["cpl"] > avg_cpl * 1.5:
                insight_type = "campaign_efficiency_risk"
                title = "Campaign efficiency risk"
                diagnosis = "CPL is materially higher than the account average."
                recommendation = "Compare ad sets and shift budget toward lower-CPL campaigns."
            elif avg_cpl and metrics["cpl"] and item["results"] >= 3 and metrics["cpl"] < avg_cpl * 0.75:
                insight_type = "campaign_winner"
                title = "Campaign is outperforming"
                diagnosis = "This campaign is producing leads below the account average CPL."
                recommendation = "Protect this campaign and consider gradual budget shift from weaker campaigns."
            if not insight_type or ("campaign", key[3], insight_type) in existing:
                continue
            created_rows.append({
                "client_id": key[0], "platform": key[1], "account_id": key[2], "insight_date": latest_date,
                "entity_type": "campaign", "entity_id": key[3], "entity_name": item["name"],
                "insight_type": insight_type, "severity": severity, "title": title,
                "summary": "This insight is based on recent Meta performance trends.",
                "diagnosis": diagnosis, "recommendation": recommendation,
                "metrics_snapshot": {"window": "recent_7_days", "spend": round_or_none(item["spend"], 2), "results": item["results"], "ctr": round_or_none(metrics["ctr"]), "cpl": round_or_none(metrics["cpl"]), "account_avg_cpl": round_or_none(avg_cpl)},
            })

        geo_totals = _aggregate(recent_rows, ["client_id", "platform", "account_id", "country"])
        for key, item in geo_totals.items():
            avg_cpl = account_cpl.get(key[:3])
            metrics = _metrics(item)
            if item["spend"] < 50:
                continue
            if item["results"] != 0 and not (avg_cpl and metrics["cpl"] and metrics["cpl"] > avg_cpl * 1.75):
                continue
            if ("geo", key[3], "geo_efficiency_risk") in existing:
                continue
            created_rows.append({
                "client_id": key[0], "platform": key[1], "account_id": key[2], "insight_date": latest_date,
                "entity_type": "geo", "entity_id": key[3], "entity_name": key[3],
                "insight_type": "geo_efficiency_risk", "severity": "high" if item["results"] == 0 else "medium",
                "title": "Geo efficiency risk", "summary": "This country is spending but is not matching account efficiency.",
                "diagnosis": "The geo has spend with weak or no recorded results.",
                "recommendation": "Review whether this country should be excluded, separated, or given lower budget until quality improves.",
                "metrics_snapshot": {"window": "recent_7_days", "country": key[3], "spend": round_or_none(item["spend"], 2), "results": item["results"], "cpl": round_or_none(metrics["cpl"]), "account_avg_cpl": round_or_none(avg_cpl)},
            })

        if previous_rows:
            recent_ads = _aggregate(recent_rows, ["client_id", "platform", "account_id", "ad_id"], "ad_name")
            previous_ads = _aggregate(previous_rows, ["client_id", "platform", "account_id", "ad_id"], "ad_name")
            for key, item in recent_ads.items():
                previous = previous_ads.get(key)
                if not previous or item["impressions"] < 500:
                    continue
                recent_metrics = _metrics(item)
                previous_metrics = _metrics(previous)
                if not recent_metrics["ctr"] or not previous_metrics["ctr"] or recent_metrics["ctr"] >= previous_metrics["ctr"] * 0.75:
                    continue
                if ("ad", key[3], "early_ctr_fatigue") in existing:
                    continue
                created_rows.append({
                    "client_id": key[0], "platform": key[1], "account_id": key[2], "insight_date": latest_date,
                    "entity_type": "ad", "entity_id": key[3], "entity_name": item["name"],
                    "insight_type": "early_ctr_fatigue", "severity": "high" if (recent_metrics["frequency"] or 0) >= 2.5 else "medium",
                    "title": "Early CTR fatigue signal", "summary": "This ad has a clear CTR drop compared with the previous period.",
                    "diagnosis": "CTR is falling, which can indicate creative fatigue or weaker audience attention.",
                    "recommendation": "Prepare a new creative variation or angle. Avoid scaling this ad until CTR stabilizes.",
                    "metrics_snapshot": {"window": "recent_7_days_vs_previous_7", "spend": round_or_none(item["spend"], 2), "ctr": round_or_none(recent_metrics["ctr"]), "previous_ctr": round_or_none(previous_metrics["ctr"]), "frequency": round_or_none(recent_metrics["frequency"])},
                })

    return {"created": _insert(created_rows), "latest_date": max(row["perf_date"] for row in rows)}
