create schema if not exists private;

create or replace function private.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  target_client_id uuid;
  workspace_name text;
begin
  lock table public.client_users in share row exclusive mode;

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

  insert into public.client_users (client_id, user_id, email, role, accepted_at)
  values (target_client_id, new.id, new.email, 'owner', now())
  on conflict (client_id, user_id) do update
  set email = excluded.email,
      accepted_at = coalesce(public.client_users.accepted_at, excluded.accepted_at),
      updated_at = now();

  return new;
end;
$$;

revoke all on function private.handle_new_auth_user() from public, anon, authenticated;

drop trigger if exists on_auth_user_created_create_workspace on auth.users;
create trigger on_auth_user_created_create_workspace
after insert on auth.users
for each row execute function private.handle_new_auth_user();

alter table public.clients enable row level security;
alter table public.client_users enable row level security;

drop policy if exists clients_select_member on public.clients;
create policy clients_select_member on public.clients for select to authenticated
using (
  exists (
    select 1 from public.client_users membership
    where membership.client_id = clients.client_id
      and membership.user_id = (select auth.uid())
  )
);

drop policy if exists client_users_select_own on public.client_users;
create policy client_users_select_own on public.client_users for select to authenticated
using (user_id = (select auth.uid()));

revoke all on table public.clients from anon, authenticated;
revoke all on table public.client_users from anon, authenticated;
grant select on table public.clients to authenticated;
grant select on table public.client_users to authenticated;
