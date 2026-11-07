-- Briefs: what the user asked the agent to do. `brief` holds the brief.v1 JSON shape
-- (validated in the backend, not in SQL). Written by the backend; owner select only.

create table public.briefs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  goal text not null,
  business_name text not null,
  phone_e164 text not null check (phone_e164 ~ '^\+[1-9][0-9]{6,14}$'),
  template_key text,
  brief jsonb not null,
  missing_fields jsonb not null default '[]',
  created_at timestamptz not null default now()
);
alter table public.briefs enable row level security;
create index briefs_user_id_idx on public.briefs (user_id);

revoke all on public.briefs from anon, authenticated;
grant select on public.briefs to authenticated;

create policy briefs_select_own on public.briefs
  for select to authenticated using (user_id = (select auth.uid()));
