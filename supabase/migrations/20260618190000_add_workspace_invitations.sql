create table if not exists public.workspace_invitations (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(client_id) on delete cascade,
  email text not null,
  role text not null default 'viewer' check (role in ('admin', 'viewer')),
  status text not null default 'pending'
    check (status in ('pending', 'accepted', 'revoked', 'expired')),
  invited_by uuid references auth.users(id) on delete set null,
  accepted_by uuid references auth.users(id) on delete set null,
  accepted_at timestamptz,
  expires_at timestamptz not null default (now() + interval '14 days'),
  email_sent_at timestamptz,
  email_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists workspace_invitations_one_pending
  on public.workspace_invitations (client_id, lower(email))
  where status = 'pending';
create index if not exists workspace_invitations_email_status
  on public.workspace_invitations (lower(email), status, expires_at);
create index if not exists workspace_invitations_invited_by
  on public.workspace_invitations (invited_by);
create index if not exists workspace_invitations_accepted_by
  on public.workspace_invitations (accepted_by);

alter table public.workspace_invitations enable row level security;
revoke all on table public.workspace_invitations from anon, authenticated;
grant all on table public.workspace_invitations to service_role;

alter table public.workspace_access_requests
  add column if not exists owner_notified_at timestamptz,
  add column if not exists owner_notification_error text,
  add column if not exists decision_notified_at timestamptz,
  add column if not exists decision_notification_error text;

create or replace function private.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  target_client_id uuid;
  target_role text;
  invitation_id uuid;
  workspace_name text;
begin
  lock table public.client_users in share row exclusive mode;

  select invitation.id, invitation.client_id, invitation.role
  into invitation_id, target_client_id, target_role
  from public.workspace_invitations invitation
  where lower(invitation.email) = lower(new.email)
    and invitation.status = 'pending'
    and invitation.expires_at > now()
  order by invitation.created_at
  limit 1;

  if target_client_id is null then
    if not exists (select 1 from public.client_users)
       and (select count(*) from public.clients) = 1 then
      select client_id into target_client_id
      from public.clients
      order by created_at
      limit 1;
    else
      workspace_name := coalesce(
        nullif(new.raw_user_meta_data ->> 'full_name', ''),
        nullif(new.raw_user_meta_data ->> 'name', ''),
        nullif(split_part(coalesce(new.email, 'New workspace'), '@', 1), ''),
        'New workspace'
      );
      insert into public.clients (client_name)
      values (workspace_name)
      returning client_id into target_client_id;
    end if;
    target_role := 'owner';
  end if;

  insert into public.client_users (client_id, user_id, email, role, accepted_at)
  values (target_client_id, new.id, new.email, target_role, now())
  on conflict (client_id, user_id) do update
  set email = excluded.email,
      role = excluded.role,
      accepted_at = coalesce(public.client_users.accepted_at, excluded.accepted_at),
      updated_at = now();

  if invitation_id is not null then
    update public.workspace_invitations
    set status = 'accepted', accepted_by = new.id, accepted_at = now(), updated_at = now()
    where id = invitation_id;
  end if;

  return new;
end;
$$;

revoke all on function private.handle_new_auth_user() from public, anon, authenticated;
