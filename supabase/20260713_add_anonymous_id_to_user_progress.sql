alter table public.user_progress
  add column if not exists anonymous_id text;

create index if not exists user_progress_anonymous_status_idx
  on public.user_progress (anonymous_id, status, created_at desc);

comment on column public.user_progress.anonymous_id is
  'Browser-scoped anonymous identifier stored in localStorage. Future authenticated users can be linked with a separate user_id column.';
