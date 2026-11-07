-- Outbox (side effects driven from committed rows), idempotency keys, push subscriptions.

-- Backend-only.
create table public.outbox (
  id bigserial primary key,
  call_id uuid not null references public.calls (id) on delete cascade,
  lane text not null check (lane in ('control', 'notify', 'jobs')),
  kind text not null,
  payload jsonb not null default '{}',
  state text not null default 'pending'
    check (state in ('pending', 'sent', 'uncertain', 'failed', 'cancelled')),
  attempts int not null default 0,
  not_before timestamptz not null default now(),
  created_at timestamptz not null default now()
);
alter table public.outbox enable row level security;
create index outbox_pending_idx on public.outbox (lane, call_id, id) where state = 'pending';
create index outbox_call_id_idx on public.outbox (call_id);

-- Backend-only. Rows are kept 24 h.
create table public.idempotency_keys (
  user_id uuid not null references public.profiles (id) on delete cascade,
  key text not null,
  status_code int,
  response jsonb,
  created_at timestamptz not null default now(),
  primary key (user_id, key)
);
alter table public.idempotency_keys enable row level security;

revoke all on public.outbox, public.idempotency_keys from anon, authenticated;
revoke all on sequence public.outbox_id_seq from anon, authenticated;

-- Browser: owner can list and delete their own Web Push subscriptions; the backend inserts.
create table public.push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  endpoint text not null unique,
  p256dh text not null,
  auth text not null
);
alter table public.push_subscriptions enable row level security;
create index push_subscriptions_user_id_idx on public.push_subscriptions (user_id);

revoke all on public.push_subscriptions from anon, authenticated;
grant select, delete on public.push_subscriptions to authenticated;
create policy push_subscriptions_select_own on public.push_subscriptions
  for select to authenticated using (user_id = (select auth.uid()));
create policy push_subscriptions_delete_own on public.push_subscriptions
  for delete to authenticated using (user_id = (select auth.uid()));
