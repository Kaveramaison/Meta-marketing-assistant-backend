# Meta Marketing Assistant Backend

FastAPI backend for the Meta-only MVP of the AI Marketing OS.

## What this backend does now

- Runs a FastAPI app with health checks.
- Pulls Meta Ads daily ad-level performance by country.
- Upserts data into Supabase `marketing_performance_daily`.
- Keeps `campaigns`, `ad_sets`, and `ads` in sync from Meta performance rows.
- Writes every sync attempt to `sync_runs`.
- Generates first-pass rule-based insights into `insights`.
- Supports dynamic dates. No hardcoded daily dates.

## Important Railway variables

Set these in Railway:

```text
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
```

Optional:

```text
META_GRAPH_API_VERSION=v20.0
DEFAULT_TIMEZONE=Asia/Kolkata
META_DAILY_LOOKBACK_DAYS=3
META_BACKFILL_DAYS=90
CRON_SECRET=your-private-job-secret
```

`META_DAILY_LOOKBACK_DAYS=3` means the daily job re-fetches the last 3 completed days. This helps Meta delayed attribution correct recent numbers.

## Run API locally

```bash
uvicorn main:app --reload
```

## Run Meta daily sync

```bash
python -m jobs.pull_meta daily
```

On June 17, using `Asia/Kolkata`, this fetches through June 16. On June 18, it fetches through June 17.

## Run Meta scheduled sync

```bash
python -m jobs.pull_meta scheduled
```

This is the long-term SaaS scheduler mode. Run it hourly from Railway using:

```text
0 * * * *
```

The job checks `meta_accounts.sync_frequency_hours` and `last_synced_at`, then syncs only accounts that are due.

## Run Meta backfill

```bash
python -m jobs.pull_meta backfill --days 90
```

## Railway scheduled update

Create a separate Railway cron/scheduled service that runs:

```bash
python -m jobs.pull_meta scheduled
```

Set its cron schedule to:

```text
0 * * * *
```

Keep the main API service running `uvicorn main:app --host 0.0.0.0 --port $PORT`.

## API endpoints

```text
GET  /
GET  /health
GET  /dashboard/overview?client_id=...&days=30
GET  /dashboard/insights?client_id=...
POST /jobs/meta/daily
POST /jobs/meta/backfill
POST /jobs/meta/scheduled
```

If `CRON_SECRET` is set, call job endpoints with:

```text
X-Cron-Secret: your-private-job-secret
```
