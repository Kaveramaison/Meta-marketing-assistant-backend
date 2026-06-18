create table if not exists public.meta_event_daily (
  id uuid primary key default gen_random_uuid(),
  event_date date not null,
  client_id uuid references public.clients(client_id) on delete cascade,
  platform text not null default 'meta',
  account_id text not null,
  source_type text not null,
  source_id text not null,
  source_name text,
  event_name text not null,
  event_source text not null default 'all',
  event_count bigint not null default 0,
  aggregation text not null default 'event',
  raw_payload jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (event_date, client_id, account_id, source_type, source_id, event_name, event_source)
);

create index if not exists idx_meta_event_daily_account_date
  on public.meta_event_daily (client_id, account_id, event_date desc);
create index if not exists idx_meta_event_daily_source
  on public.meta_event_daily (source_id, event_name, event_date desc);
alter table public.meta_event_daily enable row level security;

create table if not exists public.meta_event_diagnostics_daily (
  id uuid primary key default gen_random_uuid(),
  snapshot_date date not null,
  client_id uuid references public.clients(client_id) on delete cascade,
  platform text not null default 'meta',
  account_id text not null,
  source_type text not null,
  source_id text not null,
  source_name text,
  diagnostic_code text not null default 'unknown',
  severity text,
  title text,
  description text,
  status text,
  first_detected_at timestamptz,
  last_detected_at timestamptz,
  raw_payload jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (snapshot_date, client_id, account_id, source_type, source_id, diagnostic_code)
);

create index if not exists idx_meta_event_diagnostics_account_date
  on public.meta_event_diagnostics_daily (client_id, account_id, snapshot_date desc);
alter table public.meta_event_diagnostics_daily enable row level security;

alter table public.meta_pixels
  add column if not exists creation_time timestamptz,
  add column if not exists data_use_setting text,
  add column if not exists automatic_matching_enabled boolean,
  add column if not exists automatic_matching_fields jsonb,
  add column if not exists first_party_cookie_status text,
  add column if not exists owner_ad_account_id text;
