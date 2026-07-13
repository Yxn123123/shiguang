alter table public.user_progress
  add column if not exists user_id uuid,
  add column if not exists active boolean not null default true,
  add column if not exists updated_at timestamptz not null default now();

comment on column public.user_progress.user_id is
  'Reserved for future Supabase Auth users. Anonymous browser users continue to use anonymous_id.';

comment on column public.user_progress.active is
  'Current-state flag for read and favorite rows. Explored rows are historical events and should remain active.';

create or replace function public.set_user_progress_updated_at()
returns trigger
language plpgsql
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

create unique index if not exists user_progress_anon_current_unique
  on public.user_progress (anonymous_id, card_id, status)
  where user_id is null
    and anonymous_id is not null
    and status in ('read', 'favorite');

create unique index if not exists user_progress_user_current_unique
  on public.user_progress (user_id, card_id, status)
  where user_id is not null
    and status in ('read', 'favorite');

create index if not exists user_progress_anon_active_idx
  on public.user_progress (anonymous_id, status, active, updated_at desc);

create index if not exists user_progress_user_active_idx
  on public.user_progress (user_id, status, active, updated_at desc);

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
    and status in ('read', 'favorite', 'explored')
  );

create policy "Anonymous users can update current progress"
  on public.user_progress
  for update
  to anon
  using (
    user_id is null
    and anonymous_id is not null
    and status in ('read', 'favorite')
  )
  with check (
    user_id is null
    and anonymous_id is not null
    and status in ('read', 'favorite')
  );
