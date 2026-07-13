create extension if not exists pgcrypto;

create table if not exists public.user_progress (
  id uuid primary key default gen_random_uuid(),
  anonymous_id text,
  user_id uuid,
  card_id text not null,
  status text not null check (status in ('read', 'favorite', 'disliked', 'explored')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on column public.user_progress.anonymous_id is
  'Browser-scoped anonymous identifier stored in localStorage. Future authenticated users can be linked with a separate user_id column.';

comment on column public.user_progress.user_id is
  'Reserved for future Supabase Auth users. Anonymous browser users continue to use anonymous_id.';

comment on column public.user_progress.active is
  'Current-state flag for read and favorite rows. Explored rows are historical events and should remain active.';

create or replace function public.set_user_progress_updated_at()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_user_progress_updated_at
  on public.user_progress;

create trigger set_user_progress_updated_at
  before update on public.user_progress
  for each row
  execute function public.set_user_progress_updated_at();

create index if not exists user_progress_anonymous_status_idx
  on public.user_progress (anonymous_id, status, created_at desc);

create unique index if not exists user_progress_anon_current_unique
  on public.user_progress (anonymous_id, card_id, status)
  where user_id is null
    and anonymous_id is not null
    and status in ('read', 'favorite', 'disliked');

create unique index if not exists user_progress_user_current_unique
  on public.user_progress (user_id, card_id, status)
  where user_id is not null
    and status in ('read', 'favorite', 'disliked');

create index if not exists user_progress_anon_active_idx
  on public.user_progress (anonymous_id, status, active, updated_at desc);

create index if not exists user_progress_user_active_idx
  on public.user_progress (user_id, status, active, updated_at desc);

create index if not exists user_progress_card_status_idx
  on public.user_progress (card_id, status);

create index if not exists user_progress_created_at_idx
  on public.user_progress (created_at desc);

alter table public.user_progress enable row level security;

revoke all privileges on public.user_progress from anon;
grant select, insert, update on public.user_progress to anon;

drop policy if exists "Allow anonymous progress inserts" on public.user_progress;
drop policy if exists "Anonymous users can read progress" on public.user_progress;
drop policy if exists "Anonymous users can insert progress" on public.user_progress;
drop policy if exists "Anonymous users can update current progress" on public.user_progress;

create policy "Anonymous users can read progress"
  on public.user_progress
  for select
  to anon
  using (
    user_id is null
    and anonymous_id is not null
  );

create policy "Anonymous users can insert progress"
  on public.user_progress
  for insert
  to anon
  with check (
    user_id is null
    and anonymous_id is not null
    and status in ('read', 'favorite', 'disliked', 'explored')
  );

create policy "Anonymous users can update current progress"
  on public.user_progress
  for update
  to anon
  using (
    user_id is null
    and anonymous_id is not null
    and status in ('read', 'favorite', 'disliked')
  )
  with check (
    user_id is null
    and anonymous_id is not null
    and status in ('read', 'favorite', 'disliked')
  );

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
