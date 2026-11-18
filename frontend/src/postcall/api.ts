/**
 * The post-call screens' backend API (PO-1 to PO-6), without React:
 * `GET /calls` for the history list and `GET /calls/{call_id}/result` for one call.
 *
 * Everything that comes back is a boundary: nothing is trusted until checked,
 * and a malformed row or field is dropped instead of crashing the screen.
 * The backend's shapes are in backend/app/postcall/history.py.
 */
import { apiBase, isCallId, realSourceAllowed } from '../live-call/backend'
import type { CallHistoryItem } from '../history/types'
import type { TranscriptLine } from '../result/transcript'
import type { AgentDecision, CallResult, OutcomeDetails, SharedField } from '../result/types'

/** Which data the history screen uses. */
export type HistorySource = { kind: 'fake' } | { kind: 'real'; apiBase: string }

/**
 * Real data when `VITE_API_URL` is a valid origin and `VITE_HISTORY_SOURCE` isn't set to
 * something other than `real` (see `realSourceAllowed`). Anything else shows the fake
 * calls, so the live site keeps working while the backend isn't deployed.
 */
export function historySource(source: string | undefined, apiUrl: string | undefined): HistorySource {
  if (!realSourceAllowed(source)) return { kind: 'fake' }
  const base = apiBase(apiUrl)
  return base ? { kind: 'real', apiBase: base } : { kind: 'fake' }
}

type Raw = Record<string, unknown>

function isObject(value: unknown): value is Raw {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const str = (value: unknown): string | null => (typeof value === 'string' ? value : null)
const optStr = (value: unknown): string | undefined => (typeof value === 'string' && value !== '' ? value : undefined)

/** Checks one call from `GET /calls` (or the `call` of a result). Null if malformed. */
export function toHistoryItem(raw: unknown): CallHistoryItem | null {
  if (!isObject(raw)) return null
  const id = str(raw.id)
  const createdAt = str(raw.created_at)
  const status = str(raw.status)
  if (!id || !createdAt || !status) return null
  return {
    id,
    businessName: str(raw.business_name) ?? '',
    phoneE164: str(raw.phone_e164) ?? '',
    goal: str(raw.goal) ?? '',
    templateKey: str(raw.template_key),
    status,
    outcome: str(raw.outcome),
    createdAt,
    endedAt: str(raw.ended_at),
  }
}

/** Checks the body of `GET /calls`. Bad rows are left out. */
export function toHistory(body: unknown): CallHistoryItem[] | null {
  if (!isObject(body) || !Array.isArray(body.calls)) return null
  return body.calls.map(toHistoryItem).filter((item): item is CallHistoryItem => item !== null)
}

function toDetails(raw: unknown): OutcomeDetails {
  if (!isObject(raw)) return {}
  const details: OutcomeDetails = {}
  const date = optStr(raw.date)
  const time = optStr(raw.time)
  const confirmationNumber = optStr(raw.confirmation_number)
  const staffName = optStr(raw.staff_name)
  if (date) details.date = date
  if (time) details.time = time
  if (confirmationNumber) details.confirmationNumber = confirmationNumber
  if (staffName) details.staffName = staffName
  return details
}

function toDecision(raw: unknown): AgentDecision | null {
  if (!isObject(raw)) return null
  const situation = str(raw.situation)
  const action = str(raw.action)
  const reason = str(raw.reason)
  if (situation === null || action === null || reason === null) return null
  const decision: AgentDecision = { situation, action, reason }
  const fieldKey = optStr(raw.field_key)
  const missedPreference = optStr(raw.missed_preference)
  if (fieldKey) decision.fieldKey = fieldKey
  if (missedPreference) decision.missedPreference = missedPreference
  return decision
}

function toSharedField(raw: unknown): SharedField | null {
  if (!isObject(raw)) return null
  const fieldKey = str(raw.field_key)
  const state = str(raw.state)
  return fieldKey && state ? { fieldKey, state } : null
}

const ROLES: ReadonlySet<string> = new Set(['agent', 'business', 'user'])

/** A role this version doesn't know is kept as "unknown" (shown as "Unknown"), not dropped. */
function toRole(raw: string): TranscriptLine['role'] {
  return ROLES.has(raw) ? (raw as TranscriptLine['role']) : 'unknown'
}

function toTranscriptLine(raw: unknown): TranscriptLine | null {
  if (!isObject(raw)) return null
  const { seq } = raw
  const role = str(raw.role)
  const text = str(raw.text)
  if (typeof seq !== 'number' || !Number.isInteger(seq) || !role || text === null) {
    return null
  }
  return { seq, role: toRole(role), text, startedAt: str(raw.started_at) }
}

function listOf<T>(raw: unknown, check: (item: unknown) => T | null): T[] {
  return Array.isArray(raw) ? raw.map(check).filter((item): item is T => item !== null) : []
}

/** Checks the body of `GET /calls/{call_id}/result`. Null if the call itself is malformed. */
export function toCallResult(body: unknown): CallResult | null {
  if (!isObject(body)) return null
  const call = toHistoryItem(body.call)
  if (!call) return null
  return {
    call,
    businessConfirmed: body.business_confirmed === true,
    details: toDetails(body.details),
    openItems: listOf(body.open_items, (item) => (typeof item === 'string' && item.trim() ? item : null)),
    decisions: listOf(body.decisions, toDecision),
    sharedFields: listOf(body.shared_fields, toSharedField),
    transcript: listOf(body.transcript, toTranscriptLine),
  }
}

/** What a load gives the screen: the data, or a plain-language problem. */
export type Loaded<T> = { ok: true; data: T } | { ok: false; message: string }

/** Shown when the call doesn't exist or isn't the user's; trying again won't help. */
export const NOT_FOUND_MESSAGE = 'That call wasn’t found.'

const NOT_FOUND: Loaded<never> = { ok: false, message: NOT_FOUND_MESSAGE }

/** Turns an HTTP answer into `Loaded`, using `check` on a 200 body. `failed` is the message for
 * any other error, so it can name what didn't load. */
export function loadedFrom<T>(
  status: number,
  body: unknown,
  check: (body: unknown) => T | null,
  failed = 'Could not load your calls. Try again.',
): Loaded<T> {
  if (status === 200) {
    const data = check(body)
    return data === null ? { ok: false, message: 'The server sent something unexpected. Try again.' } : { ok: true, data }
  }
  if (status === 401) return { ok: false, message: 'You were signed out. Sign in and try again.' }
  if (status === 404) return NOT_FOUND
  return { ok: false, message: failed }
}

async function get(base: string, path: string, token: string): Promise<{ status: number; body: unknown }> {
  const response = await fetch(base + path, { headers: { Authorization: `Bearer ${token}` } })
  const body: unknown = await response.json().catch(() => null)
  return { status: response.status, body }
}

/** `GET /calls`. Network failures become a problem message. */
export async function fetchHistory(base: string, token: string): Promise<Loaded<CallHistoryItem[]>> {
  try {
    const { status, body } = await get(base, '/calls', token)
    return loadedFrom(status, body, toHistory)
  } catch {
    return { ok: false, message: 'No connection. Check your internet and try again.' }
  }
}

/**
 * `GET /calls/{call_id}/result`. Network failures become a problem message. The id comes
 * from the address (`?open=`), so anything that isn't a UUID is "not found" without a request.
 */
export async function fetchCallResult(base: string, token: string, callId: string): Promise<Loaded<CallResult>> {
  if (!isCallId(callId)) return NOT_FOUND
  try {
    const { status, body } = await get(base, `/calls/${encodeURIComponent(callId)}/result`, token)
    return loadedFrom(status, body, toCallResult, 'Could not load this call. Try again.')
  } catch {
    return { ok: false, message: 'No connection. Check your internet and try again.' }
  }
}
