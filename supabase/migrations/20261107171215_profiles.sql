-- Profiles and the fields a user lets the agent share (ON-5, ON-8, DC-1).
-- Browser access: owner select/insert/update. Row-level security is the boundary.

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  onboarded_at timestamptz
);
alter table public.profiles enable row level security;

create table public.profile_fields (
  user_id uuid not null references public.profiles (id) on delete cascade,
  key text not null,
  value text not null,
  sharing text not null check (sharing in ('share_freely', 'ask_first')),
  primary key (user_id, key),
  -- DC-1: the opening line always says who we are calling for.
  constraint profile_fields_calling_name_shared check (key <> 'calling_name' or sharing = 'share_freely')
);
alter table public.profile_fields enable row level security;

revoke all on public.profiles, public.profile_fields from anon, authenticated;
grant select, insert, update on public.profiles, public.profile_fields to authenticated;

create policy profiles_select_own on public.profiles
  for select to authenticated using (id = (select auth.uid()));
create policy profiles_insert_own on public.profiles
  for insert to authenticated with check (id = (select auth.uid()));
create policy profiles_update_own on public.profiles
  for update to authenticated
  using (id = (select auth.uid())) with check (id = (select auth.uid()));

create policy profile_fields_select_own on public.profile_fields
  for select to authenticated using (user_id = (select auth.uid()));
create policy profile_fields_insert_own on public.profile_fields
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy profile_fields_update_own on public.profile_fields
  for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
