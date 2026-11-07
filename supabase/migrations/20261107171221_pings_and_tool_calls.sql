-- Pings (questions to the user mid-call), tool calls and agent scores.
-- Pings are written only by the backend; the browser reads them over Realtime.

create table public.pings (
  ping_id uuid primary key default gen_random_uuid(),
  call_id uuid not null references public.calls (id) on delete cascade,
  kind text not null check (kind in ('decision', 'field_approval')),
  field_key text,
  question text not null,
  reason text,
  suggested_replies jsonb not null default '[]',
  state text not null default 'open'
    check (state in ('open', 'answered', 'expired', 'superseded', 'cancelled')),
  expires_at timestamptz,
  remaining_ms int,
  vapi_tool_call_id text not null unique,
  source_ts timestamptz not null,
  answered_at timestamptz
);
alter table public.pings enable row level security;
-- At most one open ping per call.
create unique index pings_one_open_per_call on public.pings (call_id) where state = 'open';

revoke all on public.pings from anon, authenticated;
grant select on public.pings to authenticated;
create policy pings_select_own on public.pings
  for select to authenticated using (
    exists (select 1 from public.calls c where c.id = call_id and c.user_id = (select auth.uid())));

alter publication supabase_realtime add table public.pings;

-- Backend-only.
create table public.tool_calls (
  call_id uuid not null references public.calls (id) on delete cascade,
  tool_call_id text not null,
  name text not null,
  arguments jsonb not null,
  source_ts timestamptz,
  state text not null default 'pending' check (state in ('pending', 'handled', 'failed')),
  result jsonb,
  error text,
  created_at timestamptz not null default now(),
  primary key (call_id, tool_call_id)
);
alter table public.tool_calls enable row level security;

-- Backend-only.
create table public.agent_scores (
  call_id uuid not null references public.calls (id) on delete cascade,
  tool_call_id text not null,
  tool text not null,
  confidence text not null check (confidence in ('high', 'medium', 'low')),
  dimensions jsonb not null,
  primary key (call_id, tool_call_id)
);
alter table public.agent_scores enable row level security;

revoke all on public.tool_calls, public.agent_scores from anon, authenticated;
