create table if not exists public.workspace_access_requests (
  id uuid primary key default gen_random_uuid(),
  requester_user_id uuid not null references auth.users(id) on delete cascade,
  requester_client_id uuid not null references public.clients(client_id) on delete cascade,
  requester_email text,
  target_client_id uuid not null references public.clients(client_id) on delete cascade,
  ad_account_id text not null,
  ad_account_name text,
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected', 'cancelled')),
  decided_by uuid references auth.users(id) on delete set null,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists workspace_access_requests_one_pending
  on public.workspace_access_requests (requester_user_id, target_client_id, ad_account_id)
  where status = 'pending';

create index if not exists workspace_access_requests_target_status
  on public.workspace_access_requests (target_client_id, status, created_at);

create index if not exists workspace_access_requests_requester_status
  on public.workspace_access_requests (requester_user_id, status, created_at);

alter table public.workspace_access_requests enable row level security;
revoke all on table public.workspace_access_requests from anon, authenticated;
grant all on table public.workspace_access_requests to service_role;

alter table public.meta_accounts
  add constraint meta_accounts_ad_account_id_key unique (ad_account_id);
