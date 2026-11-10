/**
 * What happens when the user taps Start on the review card (PC-6, plan Step 11).
 *
 * Demo mode: log the brief and say the call started, exactly as before.
 * API mode:
 *   1. Save the brief: `POST /briefs` the first time, `PATCH /briefs/{id}` if the
 *      user edited it after an earlier save in this draft, nothing if unchanged.
 *   2. The server's `missing_fields` win: if it lists anything, stop there.
 *   3. `POST /calls {brief_id}` (Coco's route). If that route isn't deployed
 *      yet (404/405 or no answer), the brief is still saved: say so calmly.
 *
 * Every mutation sends an Idempotency-Key. Keys live in the `SaveSession` and
 * are keyed by the request (method, brief id, exact body), so retrying the
 * same action after a failure reuses the key, while a changed brief gets a new
 * one (the server would otherwise replay the old answer for the new body).
 *
 * No React here: the component only keeps a `SaveSession` per draft and
 * renders the `StartResult`.
 */
import { ApiError, NETWORK_ERROR, NO_SESSION, type ApiClient, type BriefResponse } from './api'
import { profileLabel } from './missing'
import type { Brief } from './types'

/** What the browser remembers about saving the current draft. Reset on "new call". */
export interface SaveSession {
  /** Set once `POST /briefs` succeeded; later saves are `PATCH`es to this id. */
  briefId: string | null
  /** The exact brief JSON the server last accepted, and its answer. */
  savedJson: string | null
  saved: BriefResponse | null
  /** Idempotency-Key per request signature, so a retry of the same request reuses it. */
  keys: Record<string, string>
}

export const emptySession: SaveSession = { briefId: null, savedJson: null, saved: null, keys: {} }

/** The server's view of the brief, shown on the review card while the brief is unchanged. */
export interface ServerCheck {
  /** The brief JSON this check is for; ignored once the local brief differs. */
  briefJson: string
  missing_fields: string[]
  opening_line: string | null
}

export type StartResult =
  /** The call controller accepted the brief (or demo mode). */
  | { kind: 'started'; server: ServerCheck | null; callId: string | null }
  /** Saved, but `POST /calls` isn't deployed yet. */
  | { kind: 'saved_not_connected'; server: ServerCheck; briefId: string }
  /** Saved, but the server says something is still missing. Start stays blocked. */
  | { kind: 'blocked'; server: ServerCheck; message: string }
  /** Something went wrong; `message` is plain words for the chat. */
  | { kind: 'error'; server: ServerCheck | null; message: string; error: ApiError }

/** Runs Start for one brief. Returns the result and the updated session; never throws. */
export type Starter = (brief: Brief, session: SaveSession) => Promise<{ result: StartResult; session: SaveSession }>

/** Demo mode: no network. Logs the brief, as the app did before Step 11. */
export const demoStarter: Starter = async (brief, session) => {
  console.info('start call with brief (demo mode, nothing sent)', brief)
  return { result: { kind: 'started', server: null, callId: null }, session }
}

/** The key for this request signature, creating one if this request was never tried. */
export function keyFor(session: SaveSession, signature: string, newKey: () => string): [string, SaveSession] {
  const existing = session.keys[signature]
  if (existing) return [existing, session]
  const key = newKey()
  return [key, { ...session, keys: { ...session.keys, [signature]: key } }]
}

/** True when `POST /calls` failed because Coco's route isn't there yet, not because of the brief. */
export function isCallRouteMissing(error: ApiError): boolean {
  if (error.code === NETWORK_ERROR) return true
  // FastAPI's own 404/405 for an unknown route; a real "brief_not_found" is a different code.
  if (error.code === 'not_found' || error.code === 'method_not_allowed' || error.status === 405) return true
  // A 404 without the shared error shape (e.g. from a proxy in front of the API).
  return error.status === 404 && error.code.startsWith('http_')
}

/** Plain-words chat message for an API error. Uses the server's own message where it is already plain. */
export function describeError(error: ApiError, step: 'save' | 'start'): string {
  if (error.code === NO_SESSION || error.code === 'unauthenticated' || error.code === 'token_expired') {
    return "You're signed out, so I couldn't save this. Sign in again, then tap Start."
  }
  if (error.code === NETWORK_ERROR) {
    return "I couldn't reach the server. Check your connection, then tap Start again."
  }
  if (error.code === 'brief_in_use') {
    return 'A call already started from this plan, so I can’t change it. Tap "Start a new call" to set up another.'
  }
  if (error.code === 'invalid_phone') return `${error.message} Tap Edit next to the number to fix it.`
  const what = step === 'save' ? "I couldn't save your plan" : "I couldn't start the call"
  if (error.code === 'invalid_brief') return `${what}: something in it didn't pass the server's check (${error.message}).`
  return `${what}: ${error.message}${error.retryable ? ' Tap Start to try again.' : ''}`
}

/** Message for a brief the server says isn't ready. */
export function blockedMessage(missing: string[]): string {
  return `I can't call yet. Still needed: ${missing.map(profileLabel).join(', ')}.`
}

function checkOf(json: string, saved: BriefResponse): ServerCheck {
  return { briefJson: json, missing_fields: saved.missing_fields, opening_line: saved.opening_line }
}

function asApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  return new ApiError('unexpected', 'Something went wrong on my side.', 0, true)
}

/** API mode Start. `newKey` is injected in tests; the browser uses `crypto.randomUUID()`. */
export function apiStarter(client: ApiClient, newKey: () => string = () => crypto.randomUUID()): Starter {
  return async (brief, start) => {
    const json = JSON.stringify(brief)
    let session = start

    // 1. Save, unless the server already has exactly this brief.
    let saved = session.savedJson === json ? session.saved : null
    if (!saved) {
      const briefId = session.briefId
      const signature = briefId ? `PATCH /briefs/${briefId} ${json}` : `POST /briefs ${json}`
      const [key, withKey] = keyFor(session, signature, newKey)
      session = withKey
      try {
        saved = briefId ? await client.replaceBrief(briefId, brief, key) : await client.createBrief(brief, key)
      } catch (err) {
        const error = asApiError(err)
        return { result: { kind: 'error', server: null, message: describeError(error, 'save'), error }, session }
      }
      session = { ...session, briefId: saved.brief_id, savedJson: json, saved }
    }
    const server = checkOf(json, saved)

    // 2. The server has the final say on what's missing.
    if (saved.missing_fields.length > 0) {
      return { result: { kind: 'blocked', server, message: blockedMessage(saved.missing_fields) }, session }
    }

    // 3. Hand the brief to Coco's call controller.
    const [key, withKey] = keyFor(session, `POST /calls ${saved.brief_id}`, newKey)
    session = withKey
    try {
      const call = await client.startCall(saved.brief_id, key)
      return { result: { kind: 'started', server, callId: typeof call.call_id === 'string' ? call.call_id : null }, session }
    } catch (err) {
      const error = asApiError(err)
      if (isCallRouteMissing(error)) {
        console.info('brief saved; POST /calls is not available yet', saved.brief_id)
        return { result: { kind: 'saved_not_connected', server, briefId: saved.brief_id }, session }
      }
      return { result: { kind: 'error', server, message: describeError(error, 'start'), error }, session }
    }
  }
}

/**
 * What the review card shows: the server's `missing_fields` and opening line
 * win while they are for this exact brief; otherwise the local preview.
 */
export function reviewView(
  brief: Brief,
  local: { missing_fields: string[]; opening_line: string | null },
  server: ServerCheck | null,
): { missing_fields: string[]; opening_line: string | null } {
  if (server && server.briefJson === JSON.stringify(brief)) {
    return { missing_fields: server.missing_fields, opening_line: server.opening_line }
  }
  return local
}

/**
 * Wrap an async function so a second call while the first is still running is
 * ignored (returns null). Stops a double tap on Start from sending twice.
 */
export function singleFlight<A extends unknown[], R>(fn: (...args: A) => Promise<R>): (...args: A) => Promise<R | null> {
  let running = false
  return async (...args: A) => {
    if (running) return null
    running = true
    try {
      return await fn(...args)
    } finally {
      running = false
    }
  }
}
