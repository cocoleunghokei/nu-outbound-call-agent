// Realtime smoke check: the call's owner receives a call_events INSERT over Realtime and
// another user receives nothing (row-level security applies to Realtime).
//
// Usage (run by `make -C backend realtime-smoke`, which seeds the users and starts the backend):
//   node scripts/realtime-smoke.mjs --url <supabase api url> --anon-key <key> --call-id <uuid> \
//     --owner-token <jwt> --other-token <jwt> --trigger "<shell command that causes one event>"
//
// Needs Node.js 22+: @supabase/realtime-js uses Node's native WebSocket.
//
// Exit codes: 0 pass, 1 an assertion failed, 2 bad arguments, Node too old or Realtime
// unreachable.
import { spawn } from 'node:child_process'
import { parseArgs } from 'node:util'

const MIN_NODE_MAJOR = 22
const nodeMajor = Number(process.versions.node.split('.')[0])
if (nodeMajor < MIN_NODE_MAJOR) {
  console.error(
    `realtime-smoke: needs Node.js ${MIN_NODE_MAJOR}+ (native WebSocket for Realtime);` +
      ` this is Node ${process.versions.node}`,
  )
  process.exit(2)
}
// Imported after the version check, so an old Node gets the message above instead.
const { createClient } = await import('@supabase/supabase-js')

const OWNER_WITHIN_MS = 2000
const OTHER_QUIET_MS = 3000
const SUBSCRIBE_TIMEOUT_MS = 10000

function fail(code, message) {
  console.error(`realtime-smoke: ${message}`)
  process.exit(code)
}

const { values } = parseArgs({
  options: {
    url: { type: 'string' },
    'anon-key': { type: 'string' },
    'call-id': { type: 'string' },
    'owner-token': { type: 'string' },
    'other-token': { type: 'string' },
    trigger: { type: 'string' },
  },
})
for (const name of ['url', 'anon-key', 'call-id', 'owner-token', 'other-token', 'trigger']) {
  if (!values[name]) fail(2, `missing --${name}`)
}

/** Subscribe to this call's call_events as the token's user; resolves once SUBSCRIBED. */
function listen(label, token) {
  const received = []
  const client = createClient(values.url, values['anon-key'], {
    auth: { persistSession: false, autoRefreshToken: false },
  })
  client.realtime.setAuth(token)
  const subscribed = new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`${label}: Realtime did not confirm the subscription in time (is Realtime reachable at ${values.url}?)`)),
      SUBSCRIBE_TIMEOUT_MS,
    )
    client
      .channel(`smoke-${label}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'call_events', filter: `call_id=eq.${values['call-id']}` },
        (payload) => received.push(payload.new),
      )
      .subscribe((status, err) => {
        if (status === 'SUBSCRIBED') {
          clearTimeout(timer)
          resolve()
        } else if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT' || status === 'CLOSED') {
          clearTimeout(timer)
          reject(new Error(`${label}: channel ${status}${err ? `: ${err.message}` : ''}`))
        }
      })
  })
  return { client, received, subscribed }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const owner = listen('owner', values['owner-token'])
const other = listen('other', values['other-token'])
try {
  await Promise.all([owner.subscribed, other.subscribed])
} catch (error) {
  fail(2, error.message)
}
await sleep(500) // let the server finish registering both subscriptions

// Async, so the event loop keeps the sockets alive while the command boots and runs.
const triggerStatus = await new Promise((resolve) => {
  spawn(values.trigger, { shell: true, stdio: 'inherit' }).on('exit', resolve)
})
if (triggerStatus !== 0) fail(1, `trigger command failed with status ${triggerStatus}`)

// The clock starts once the webhook has acknowledged (the trigger returned).
const started = Date.now()
while (owner.received.length === 0 && Date.now() - started < OWNER_WITHIN_MS) await sleep(50)
if (owner.received.length === 0) fail(1, `owner got no call_events INSERT within ${OWNER_WITHIN_MS} ms`)
const event = owner.received[0]
if (event.type !== 'transcript.final') fail(1, `owner got ${event.type}, expected transcript.final`)

await sleep(Math.max(0, OTHER_QUIET_MS - (Date.now() - started)))
if (other.received.length !== 0) fail(1, `other user received ${other.received.length} event(s); RLS leak`)

console.log(`realtime-smoke: PASS (owner got seq ${event.seq} ${event.type}; other user got nothing)`)
await Promise.all([owner.client.removeAllChannels(), other.client.removeAllChannels()])
process.exit(0)
