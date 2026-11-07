-- Per-call profile snapshot (backend-only) and what was shared during the call (PO-3).

create table public.call_profile_snapshots (
  call_id uuid not null references public.calls (id) on delete cascade,
  key text not null,
  value text not null,
  sharing text not null check (sharing in ('share_freely', 'ask_first')),
  primary key (call_id, key)
);
alter table public.call_profile_snapshots enable row level security;
revoke all on public.call_profile_snapshots from anon, authenticated;

-- Disclosure state moves approved -> sent_to_agent -> spoken. Written by the backend.
create table public.shared_fields (
  call_id uuid not null references public.calls (id) on delete cascade,
  field_key text not null,
  state text not null check (state in ('approved', 'sent_to_agent', 'spoken')),
  approved_at timestamptz,
  sent_to_agent_at timestamptz,
  spoken_at timestamptz,
  primary key (call_id, field_key)
);
alter table public.shared_fields enable row level security;

revoke all on public.shared_fields from anon, authenticated;
grant select on public.shared_fields to authenticated;
create policy shared_fields_select_own on public.shared_fields
  for select to authenticated using (
    exists (select 1 from public.calls c where c.id = call_id and c.user_id = (select auth.uid())));
