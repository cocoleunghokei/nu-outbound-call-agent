/**
 * Talking to the real backend (B4), without React: checking rows that arrive
 * from Supabase, and calling the reply and command endpoints.
 *
 * Everything here is a boundary: rows come from Realtime and the database,
 * answers from the HTTP API. Nothing is trusted until checked.
 */
import type { CommandResult, ControlCommand } from './controls'
import type { ReplyResult } from './ping'
import type { CallEvent, LiveCallSummary, PingReplyCommand } from './types'

/** The columns of `call_events` the screen reads. */
export const CALL_EVENT_COLUMNS =
  'event_id, call_id, seq, type, occurred_at, source, dedup_key, schema_version, payload'

const SOURCES: ReadonlySet<string> = new Set(['vapi', 'backend', 'user'])

/**
 * Checks one `call_events` row (from a query or a Realtime insert). Returns
 * null for anything malformed, so a bad row is skipped instead of crashing the
 * screen. Unknown `type` values pass: they are an open enum.
 */
export function toCallEvent(raw: unknown): CallEvent | null {
  if (typeof raw !== 'object' || raw === null) return null
  const r = raw as Record<string, unknown>
  if (typeof r.event_id !== 'string' || typeof r.call_id !== 'string') return null
  if (typeof r.seq !== 'number' || !Number.isInteger(r.seq) || r.seq < 1) return null
  if (typeof r.type !== 'string' || r.type === '') return null
  if (typeof r.occurred_at !== 'string') return null
  if (r.schema_version !== 1) return null
  if (typeof r.payload !== 'object' || r.payload === null || Array.isArray(r.payload)) return null
  return {
    event_id: r.event_id,
    call_id: r.call_id,
    seq: r.seq,
    type: r.type,
    occurred_at: r.occurred_at,
    ...(typeof r.source === 'string' && SOURCES.has(r.source)
      ? { source: r.source as CallEvent['source'] }
      : {}),
    ...(typeof r.dedup_key === 'string' ? { dedup_key: r.dedup_key } : {}),
    schema_version: 1,
    payload: r.payload as Record<string, unknown>,
  }
}

/** Checks the header row: a `calls` row with its `briefs` row embedded. */
export function toCallSummary(raw: unknown): LiveCallSummary | null {
  if (typeof raw !== 'object' || raw === null) return null
  const r = raw as Record<string, unknown>
  const brief = (Array.isArray(r.briefs) ? r.briefs[0] : r.briefs) as Record<string, unknown> | null
  if (typeof r.id !== 'string' || typeof r.created_at !== 'string') return null
  if (typeof brief !== 'object' || brief === null) return null
  if (typeof brief.business_name !== 'string' || typeof brief.goal !== 'string') return null
  return {
    callId: r.id,
    businessName: brief.business_name,
    phoneE164: typeof brief.phone_e164 === 'string' ? brief.phone_e164 : '',
    goal: brief.goal,
    createdAt: r.created_at,
    maxDurationS: typeof r.max_duration_s === 'number' ? r.max_duration_s : 900,
  }
}

/** The error body every backend error uses: `{ error: { code, message, retryable } }`. */
function errorOf(body: unknown): { code: string; message: string } | null {
  if (typeof body !== 'object' || body === null) return null
  const error = (body as { error?: unknown }).error
  if (typeof error !== 'object' || error === null) return null
  const { code, message } = error as { code?: unknown; message?: unknown }
  return {
    code: typeof code === 'string' ? code : 'error',
    message: typeof message === 'string' ? message : '',
  }
}

/** Turns the reply endpoint's answer into what the ping card shows. */
export function replyResultFrom(status: number, body: unknown): ReplyResult {
  if (status === 200) return { kind: 'accepted' }
  const error = errorOf(body)
  if (status === 409 && error?.code === 'ping_closed') return { kind: 'closed' }
  if (status === 401) return { kind: 'error', message: 'You were signed out. Sign in and try again.' }
  return {
    kind: 'error',
    message: error?.message || 'Could not send your reply. Try again.',
  }
}

/** Turns the commands endpoint's answer into what the controls show. */
export function commandResultFrom(status: number, body: unknown): CommandResult {
  if (status === 200) return { kind: 'accepted' }
  if (status === 401) return { kind: 'error', message: 'You were signed out. Sign in and try again.' }
  return {
    kind: 'error',
    message: errorOf(body)?.message || 'Could not reach the call. Try again.',
  }
}

/** Where the backend lives, from `VITE_API_URL`, without a trailing slash. */
export function apiBase(raw: string | undefined): string | null {
  const value = raw?.trim().replace(/\/+$/, '')
  if (!value) return null
  return /^https?:\/\//.test(value) ? value : null
}

/** A POST to the backend with the user's token and a fresh Idempotency-Key. */
async function post(
  base: string,
  path: string,
  token: string,
  body: unknown,
): Promise<{ status: number; body: unknown }> {
  const response = await fetch(base + path, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify(body),
  })
  // A non-JSON body (a proxy error page, an empty 204) reads as null.
  const parsed: unknown = await response.json().catch(() => null)
  return { status: response.status, body: parsed }
}

/** `POST /calls/{call_id}/pings/{ping_id}/reply`. Network failures become an error result. */
export async function postReply(
  base: string,
  token: string,
  callId: string,
  pingId: string,
  reply: PingReplyCommand,
): Promise<ReplyResult> {
  try {
    const path = `/calls/${encodeURIComponent(callId)}/pings/${encodeURIComponent(pingId)}/reply`
    const { status, body } = await post(base, path, token, reply)
    return replyResultFrom(status, body)
  } catch {
    return { kind: 'error', message: 'No connection. Check your internet and try again.' }
  }
}

/** `POST /calls/{call_id}/commands`. Network failures become an error result. */
export async function postCommand(
  base: string,
  token: string,
  callId: string,
  command: ControlCommand,
): Promise<CommandResult> {
  try {
    const path = `/calls/${encodeURIComponent(callId)}/commands`
    const { status, body } = await post(base, path, token, { type: command })
    return commandResultFrom(status, body)
  } catch {
    return { kind: 'error', message: 'No connection. Check your internet and try again.' }
  }
}

/** Which data the live-call page uses. */
export type LiveCallMode = { kind: 'mock' } | { kind: 'real'; callId: string; apiBase: string }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * Real data only when all three are set: `VITE_LIVE_CALL_SOURCE=real`, a valid
 * `VITE_API_URL`, and `?call=<uuid>` in the address. Anything else plays the
 * fake call, so the live site keeps working while the backend isn't deployed.
 */
export function liveCallMode(
  source: string | undefined,
  apiUrl: string | undefined,
  search: string,
): LiveCallMode {
  if (source?.trim() !== 'real') return { kind: 'mock' }
  const base = apiBase(apiUrl)
  const callId = new URLSearchParams(search).get('call')?.trim() ?? ''
  if (!base || !UUID.test(callId)) return { kind: 'mock' }
  return { kind: 'real', callId, apiBase: base }
}
