alter table public.user_progress
  drop constraint if exists user_progress_status_check;

alter table public.user_progress
  add constraint user_progress_status_check
  check (status in ('read', 'favorite', 'disliked', 'explored'));

drop index if exists public.user_progress_anon_current_unique;
drop index if exists public.user_progress_user_current_unique;

create unique index user_progress_anon_current_unique
  on public.user_progress (anonymous_id, card_id, status)
  where user_id is null
    and anonymous_id is not null
    and status in ('read', 'favorite', 'disliked');

create unique index user_progress_user_current_unique
  on public.user_progress (user_id, card_id, status)
  where user_id is not null
    and status in ('read', 'favorite', 'disliked');

drop policy if exists "Anonymous users can insert progress" on public.user_progress;
drop policy if exists "Anonymous users can update current progress" on public.user_progress;

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
