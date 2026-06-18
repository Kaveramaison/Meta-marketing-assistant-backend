create table if not exists public.meta_connection_sessions (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(client_id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  access_token text not null,
  token_expires_at timestamptz,
  permissions jsonb not null default '[]'::jsonb,
  accounts jsonb not null default '[]'::jsonb,
  status text not null default 'pending' check (status in ('pending', 'consumed')),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists meta_connection_sessions_lookup_idx
  on public.meta_connection_sessions (client_id, user_id, status, expires_at);

alter table public.meta_connection_sessions enable row level security;
revoke all on table public.meta_connection_sessions from anon, authenticated;
grant all on table public.meta_connection_sessions to service_role;

comment on table public.meta_connection_sessions is
  'Short-lived server-only Meta OAuth handoff sessions. Access tokens never reach the browser.';
