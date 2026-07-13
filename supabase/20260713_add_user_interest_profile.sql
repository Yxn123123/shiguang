create table if not exists public.user_interest_profile (
  id uuid primary key default gen_random_uuid(),
  anonymous_id text,
  user_id uuid,
  profile jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.user_interest_profile is
  'Cached interest profile derived from user_progress. Anonymous users use anonymous_id; future authenticated users can use user_id.';

create or replace function public.set_user_interest_profile_updated_at()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_user_interest_profile_updated_at
  on public.user_interest_profile;

create trigger set_user_interest_profile_updated_at
  before update on public.user_interest_profile
  for each row
  execute function public.set_user_interest_profile_updated_at();

create unique index if not exists user_interest_profile_anon_unique
  on public.user_interest_profile (anonymous_id)
  where user_id is null
    and anonymous_id is not null;

create unique index if not exists user_interest_profile_user_unique
  on public.user_interest_profile (user_id)
  where user_id is not null;

alter table public.user_interest_profile enable row level security;

revoke all privileges on public.user_interest_profile from anon;
grant select, insert, update on public.user_interest_profile to anon;

drop policy if exists "Anonymous users can read interest profile" on public.user_interest_profile;
drop policy if exists "Anonymous users can insert interest profile" on public.user_interest_profile;
drop policy if exists "Anonymous users can update interest profile" on public.user_interest_profile;

create policy "Anonymous users can read interest profile"
  on public.user_interest_profile
  for select
  to anon
  using (
    user_id is null
    and anonymous_id is not null
  );

create policy "Anonymous users can insert interest profile"
  on public.user_interest_profile
  for insert
  to anon
  with check (
    user_id is null
    and anonymous_id is not null
  );

create policy "Anonymous users can update interest profile"
  on public.user_interest_profile
  for update
  to anon
  using (
    user_id is null
    and anonymous_id is not null
  )
  with check (
    user_id is null
    and anonymous_id is not null
  );
