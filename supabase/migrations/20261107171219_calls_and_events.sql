-- Calls, the append-only event log, and backend-only delivery/secret tables.
-- Calls and events are written only by the backend (billing and audit data).
-- New status/outcome/source values need a new migration (expand step).

create table public.calls (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  brief_id uuid references public.briefs (id),
  status text not null default 'creating' check (status in (
    'creating', 'creation_uncertain', 'dialing', 'in_phone_menu', 'talking',
    'waiting_on_you', 'on_hold', 'ended', 'failed')),
  outcome text check (outcome in (
    'booked', 'cancelled', 'rescheduled', 'needs_follow_up', 'refused_ai', 'wrong_number',
    'failed', 'no_answer')),
  vapi_call_id text unique,
  create_attempted_at timestamptz,
  last_seq int not null default 0,
  latest_ask_at timestamptz,
  max_duration_s int not null default 900,
  created_at timestamptz not null default now(),
  ended_at timestamptz,
  paused_at timestamptz
);
comment on column public.calls.paused_at is
  'Set when the user pauses, cleared on resume; the sweeper ends calls paused longer than the cap (DC-11).';
alter table public.calls enable row level security;
create index calls_user_id_idx on public.calls (user_id);
create index calls_brief_id_idx on public.calls (brief_id);

create table public.call_events (
  event_id uuid primary key default gen_random_uuid(),
  call_id uuid not null references public.calls (id) on delete cascade,
  seq int not null,
  type text not null,
  source text not null check (source in ('vapi', 'backend', 'user')),
  dedup_key text,
  occurred_at timestamptz not null,
  received_at timestamptz not null default now(),
  processed_at timestamptz,
  schema_version smallint not null default 1,
  payload jsonb not null default '{}',
  unique (call_id, seq),
  unique (call_id, dedup_key)
);
alter table public.call_events enable row level security;

-- Backend-only: events that failed processing (dead letter).
create table public.call_events_failed (
  id uuid primary key default gen_random_uuid(),
  call_id uuid references public.calls (id) on delete set null,
  raw_event_id text not null,
  error text not null,
  created_at timestamptz not null default now()
);
alter table public.call_events_failed enable row level security;
create index call_events_failed_call_id_idx on public.call_events_failed (call_id);

-- Backend-only delivery log: every Vapi delivery gets a row, no dedup. `body` is redacted.
create table public.vapi_raw_events (
  id bigserial primary key,
  call_id uuid references public.calls (id) on delete set null,
  vapi_call_id text,
  message_type text not null,
  body jsonb not null,
  received_at timestamptz not null default now()
);
alter table public.vapi_raw_events enable row level security;
create index vapi_raw_events_call_id_idx on public.vapi_raw_events (call_id);

-- Backend-only: live control/listen URLs. Deleted when the call ends. Writes go through
-- backend/app/calls_lock.py (lock_call, write_call_secrets, end_call_transition), which
-- take `select ... for update` on the call row and write nothing once ended_at is set.
create table public.call_secrets (
  call_id uuid primary key references public.calls (id) on delete cascade,
  control_url text not null,
  listen_url text not null,
  updated_at timestamptz not null default now()
);
alter table public.call_secrets enable row level security;

revoke all on public.calls, public.call_events from anon, authenticated;
grant select on public.calls, public.call_events to authenticated;
revoke all on public.call_events_failed, public.vapi_raw_events, public.call_secrets
  from anon, authenticated;
revoke all on sequence public.vapi_raw_events_id_seq from anon, authenticated;

create policy calls_select_own on public.calls
  for select to authenticated using (user_id = (select auth.uid()));
create policy call_events_select_own on public.call_events
  for select to authenticated using (
    exists (select 1 from public.calls c
            where c.id = call_events.call_id and c.user_id = (select auth.uid())));

alter publication supabase_realtime add table public.call_events;
