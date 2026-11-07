-- pgTAP tests for row-level security and grants. Run by `supabase test db`
-- (called from scripts/check-db.sh). Fake data only.
begin;
select plan(43);

-- Seed as the owner role (bypasses RLS) before switching identity.
insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-0000000000a1', 'alex@example.test'),
  ('00000000-0000-0000-0000-0000000000b2', 'bob@example.test');

insert into public.profiles (id) values
  ('00000000-0000-0000-0000-0000000000a1'),
  ('00000000-0000-0000-0000-0000000000b2');
insert into public.profile_fields (user_id, key, value, sharing) values
  ('00000000-0000-0000-0000-0000000000a1', 'calling_name', 'Alex Chen', 'share_freely'),
  ('00000000-0000-0000-0000-0000000000b2', 'calling_name', 'Bob Diaz', 'share_freely');
insert into public.briefs (id, user_id, goal, business_name, phone_e164, brief, missing_fields) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1',
   'Book a table for two', 'Example Bistro', '+15555550100', '{}', '[]');
insert into public.calls (id, user_id, brief_id) values
  ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a1',
   '00000000-0000-0000-0000-0000000000c1');
insert into public.call_events (call_id, seq, type, source, occurred_at) values
  ('00000000-0000-0000-0000-0000000000d1', 1, 'status.changed', 'backend', now());
insert into public.pings (call_id, kind, question, vapi_tool_call_id, source_ts) values
  ('00000000-0000-0000-0000-0000000000d1', 'decision', 'Is 7pm OK?', 'tc-1', now());
insert into public.shared_fields (call_id, field_key, state) values
  ('00000000-0000-0000-0000-0000000000d1', 'calling_name', 'approved');
insert into public.push_subscriptions (user_id, endpoint, p256dh, auth) values
  ('00000000-0000-0000-0000-0000000000a1', 'https://push.example.test/a', 'k', 'a');
insert into public.call_events_failed (call_id, raw_event_id, error) values
  ('00000000-0000-0000-0000-0000000000d1', 'raw-1', 'boom');
insert into public.vapi_raw_events (call_id, message_type, body) values
  ('00000000-0000-0000-0000-0000000000d1', 'status-update', '{}');
insert into public.call_secrets (call_id, control_url, listen_url) values
  ('00000000-0000-0000-0000-0000000000d1', 'https://control.example.test', 'wss://listen.example.test');
insert into public.tool_calls (call_id, tool_call_id, name, arguments) values
  ('00000000-0000-0000-0000-0000000000d1', 'tc-1', 'ask_user', '{}');
insert into public.agent_scores (call_id, tool_call_id, tool, confidence, dimensions) values
  ('00000000-0000-0000-0000-0000000000d1', 'tc-1', 'ask_user', 'high', '{}');
insert into public.call_profile_snapshots (call_id, key, value, sharing) values
  ('00000000-0000-0000-0000-0000000000d1', 'calling_name', 'Alex Chen', 'share_freely');
insert into public.outbox (call_id, lane, kind, payload) values
  ('00000000-0000-0000-0000-0000000000d1', 'control', 'say', '{}');
insert into public.idempotency_keys (user_id, key) values
  ('00000000-0000-0000-0000-0000000000a1', 'k1');

-- Alex (owner): positive reads on every owner table.
set local role authenticated;
set local request.jwt.claims to '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated"}';

select is((select count(*) from public.profiles), 1::bigint, 'alex reads own profiles');
select is((select count(*) from public.profile_fields), 1::bigint, 'alex reads own profile_fields');
select is((select count(*) from public.briefs), 1::bigint, 'alex reads own briefs');
select is((select count(*) from public.calls), 1::bigint, 'alex reads own calls');
select is((select count(*) from public.call_events), 1::bigint, 'alex reads own call_events');
select is((select count(*) from public.pings), 1::bigint, 'alex reads own pings');
select is((select count(*) from public.shared_fields), 1::bigint, 'alex reads own shared_fields');
select is((select count(*) from public.push_subscriptions), 1::bigint, 'alex reads own push_subscriptions');

-- Alex may write his own profile fields, but not Bob's.
select lives_ok(
  $$insert into public.profile_fields (user_id, key, value, sharing)
    values ('00000000-0000-0000-0000-0000000000a1', 'birthday', '1990-01-01', 'ask_first')$$,
  'alex inserts own profile_field');
select throws_ok(
  $$insert into public.profile_fields (user_id, key, value, sharing)
    values ('00000000-0000-0000-0000-0000000000b2', 'birthday', '1990-01-01', 'ask_first')$$,
  '42501', null, 'alex cannot insert a profile_field for bob');

-- calling_name must stay share_freely.
select throws_ok(
  $$update public.profile_fields set sharing = 'ask_first' where key = 'calling_name'$$,
  '23514', null, 'calling_name cannot be ask_first');

-- Backend-written tables reject browser inserts.
select throws_ok(
  $$insert into public.calls (user_id) values ('00000000-0000-0000-0000-0000000000a1')$$,
  '42501', null, 'authenticated cannot insert calls');
select throws_ok(
  $$insert into public.call_events (call_id, seq, type, source, occurred_at)
    values ('00000000-0000-0000-0000-0000000000d1', 2, 'x', 'user', now())$$,
  '42501', null, 'authenticated cannot insert call_events');
select throws_ok(
  $$insert into public.pings (call_id, kind, question, vapi_tool_call_id, source_ts)
    values ('00000000-0000-0000-0000-0000000000d1', 'decision', 'q', 'tc-x', now())$$,
  '42501', null, 'authenticated cannot insert pings');
select throws_ok(
  $$insert into public.shared_fields (call_id, field_key, state)
    values ('00000000-0000-0000-0000-0000000000d1', 'x', 'approved')$$,
  '42501', null, 'authenticated cannot insert shared_fields');
select throws_ok(
  $$insert into public.outbox (call_id, lane, kind, payload)
    values ('00000000-0000-0000-0000-0000000000d1', 'control', 'say', '{}')$$,
  '42501', null, 'authenticated cannot insert outbox');

-- Backend-only tables: permission denied for authenticated.
select throws_ok('select 1 from public.call_events_failed', '42501', null, 'authenticated denied call_events_failed');
select throws_ok('select 1 from public.vapi_raw_events', '42501', null, 'authenticated denied vapi_raw_events');
select throws_ok('select 1 from public.call_secrets', '42501', null, 'authenticated denied call_secrets');
select throws_ok('select 1 from public.tool_calls', '42501', null, 'authenticated denied tool_calls');
select throws_ok('select 1 from public.agent_scores', '42501', null, 'authenticated denied agent_scores');
select throws_ok('select 1 from public.call_profile_snapshots', '42501', null, 'authenticated denied call_profile_snapshots');
select throws_ok('select 1 from public.outbox', '42501', null, 'authenticated denied outbox');
select throws_ok('select 1 from public.idempotency_keys', '42501', null, 'authenticated denied idempotency_keys');

-- Bob (other user): sees none of Alex's rows.
set local request.jwt.claims to '{"sub":"00000000-0000-0000-0000-0000000000b2","role":"authenticated"}';

select is((select count(*) from public.profiles where id = '00000000-0000-0000-0000-0000000000a1'), 0::bigint, 'bob cannot read alex profiles');
select is((select count(*) from public.profile_fields where user_id = '00000000-0000-0000-0000-0000000000a1'), 0::bigint, 'bob cannot read alex profile_fields');
select is((select count(*) from public.briefs), 0::bigint, 'bob cannot read alex briefs');
select is((select count(*) from public.calls), 0::bigint, 'bob cannot read alex calls');
select is((select count(*) from public.call_events), 0::bigint, 'bob cannot read alex call_events');
select is((select count(*) from public.pings), 0::bigint, 'bob cannot read alex pings');
select is((select count(*) from public.shared_fields), 0::bigint, 'bob cannot read alex shared_fields');
select is((select count(*) from public.push_subscriptions), 0::bigint, 'bob cannot read alex push_subscriptions');

-- Anon: permission denied on backend-only tables.
set local role anon;
select throws_ok('select 1 from public.call_events_failed', '42501', null, 'anon denied call_events_failed');
select throws_ok('select 1 from public.vapi_raw_events', '42501', null, 'anon denied vapi_raw_events');
select throws_ok('select 1 from public.call_secrets', '42501', null, 'anon denied call_secrets');
select throws_ok('select 1 from public.tool_calls', '42501', null, 'anon denied tool_calls');
select throws_ok('select 1 from public.agent_scores', '42501', null, 'anon denied agent_scores');
select throws_ok('select 1 from public.call_profile_snapshots', '42501', null, 'anon denied call_profile_snapshots');
select throws_ok('select 1 from public.outbox', '42501', null, 'anon denied outbox');
select throws_ok('select 1 from public.idempotency_keys', '42501', null, 'anon denied idempotency_keys');

-- Back to the owner role: outcome check, then at most one open ping per call.
reset role;
select lives_ok(
  $$update public.calls set outcome = 'no_answer' where id = '00000000-0000-0000-0000-0000000000d1'$$,
  'no_answer is an accepted call outcome');
select throws_ok(
  $$update public.calls set outcome = 'bogus' where id = '00000000-0000-0000-0000-0000000000d1'$$,
  '23514', null, 'unknown call outcome violates check');
select throws_ok(
  $$insert into public.pings (call_id, kind, question, vapi_tool_call_id, source_ts)
    values ('00000000-0000-0000-0000-0000000000d1', 'decision', 'Second?', 'tc-2', now())$$,
  '23505', null, 'second open ping for a call violates unique index');

select * from finish();
rollback;
