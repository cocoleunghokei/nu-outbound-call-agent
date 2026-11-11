-- Backend-only. One row per LLM brief build (PC-3), for the 10-per-hour limit per user
-- (architecture §5.4). The backend deletes a user's rows older than an hour as it checks.
create table public.brief_builds (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.brief_builds enable row level security;
create index brief_builds_user_id_created_at_idx on public.brief_builds (user_id, created_at);

revoke all on public.brief_builds from anon, authenticated;
