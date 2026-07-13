create extension if not exists pgcrypto;

create table if not exists public.user_progress (
  id uuid primary key default gen_random_uuid(),
  anonymous_id text,
  card_id text not null,
  status text not null check (status in ('read', 'favorite', 'explored')),
  created_at timestamptz not null default now()
);

create index if not exists user_progress_anonymous_status_idx
  on public.user_progress (anonymous_id, status, created_at desc);

create index if not exists user_progress_card_status_idx
  on public.user_progress (card_id, status);

create index if not exists user_progress_created_at_idx
  on public.user_progress (created_at desc);

alter table public.user_progress enable row level security;

drop policy if exists "Allow anonymous progress inserts" on public.user_progress;

create policy "Allow anonymous progress inserts"
  on public.user_progress
  for insert
  to anon
  with check (status in ('read', 'favorite', 'explored'));
