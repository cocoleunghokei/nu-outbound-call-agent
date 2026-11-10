/**
 * Ping card logic (DC-6, DC-7, DC-8, DC-9): reading pings from events,
 * the reply clock, cleaning free-text replies and the reply result.
 */
import type { Ping, PingKind, PingReplyCommand, PingState } from './types'

/** Free-text replies are capped at this many characters (DC-8). */
export const MAX_REPLY_LENGTH = 200

/** Default reply window (DC-7). Only used to size the countdown bar. */
export const PING_WINDOW_MS = 45_000

const PING_KINDS: ReadonlySet<string> = new Set(['decision', 'field_approval'])

/**
 * Reads a `ping.opened` payload. Returns null if a required field is missing
 * or has the wrong type, so a malformed ping is dropped instead of shown.
 */
export function parsePing(payload: Record<string, unknown>): Ping | null {
  const p = payload
  if (typeof p.ping_id !== 'string' || p.ping_id === '') return null
  if (typeof p.call_id !== 'string') return null
  if (typeof p.kind !== 'string' || !PING_KINDS.has(p.kind)) return null
  if (typeof p.question !== 'string' || p.question.trim() === '') return null
  if (typeof p.reason !== 'string') return null
  if (!Array.isArray(p.suggested_replies)) return null
  if (!p.suggested_replies.every((r) => typeof r === 'string')) return null
  if (p.expires_at !== null && typeof p.expires_at !== 'string') return null
  if (p.remaining_ms !== null && typeof p.remaining_ms !== 'number') return null
  if (typeof p.state !== 'string') return null
  if (p.field_key !== undefined && typeof p.field_key !== 'string') return null

  return {
    ping_id: p.ping_id,
    call_id: p.call_id,
    kind: p.kind as PingKind,
    ...(p.field_key !== undefined ? { field_key: p.field_key } : {}),
    question: p.question,
    reason: p.reason,
    suggested_replies: p.suggested_replies as string[],
    expires_at: p.expires_at,
    remaining_ms: p.remaining_ms,
    state: p.state as PingState,
  }
}

/** Free text is never allowed on a field approval: only an explicit Share releases a field (DC-6). */
export function allowsFreeText(ping: Ping): boolean {
  return ping.kind === 'decision'
}

/**
 * Time left to reply, in ms, and whether the clock is frozen (call paused).
 * While paused the server sends `expires_at: null` and the frozen `remaining_ms`.
 */
export function timeLeft(ping: Ping, nowMs: number): { ms: number; paused: boolean } {
  if (ping.expires_at === null) {
    return { ms: Math.max(0, ping.remaining_ms ?? 0), paused: true }
  }
  const expires = Date.parse(ping.expires_at)
  if (Number.isNaN(expires)) return { ms: 0, paused: false }
  return { ms: Math.max(0, expires - nowMs), paused: false }
}

/**
 * Cleans a free-text reply before it is sent (DC-8): strips control
 * characters, collapses runs of whitespace, trims, and caps the length.
 * Returns an empty string when nothing is left.
 */
export function sanitizeReplyText(raw: string): string {
  return (
    raw
      // eslint-disable-next-line no-control-regex
      .replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, MAX_REPLY_LENGTH)
      .trim()
  )
}

/**
 * What happened to a reply. `closed` is the server's `409 ping_closed`: the
 * 45 s timeout or a newer question won the race, so the reply was not used.
 */
export type ReplyResult =
  | { kind: 'accepted' }
  | { kind: 'closed' }
  | { kind: 'error'; message: string }

/** Sends the user's reply to a ping. The mock call and, later, the backend API implement this. */
export type SendPingReply = (
  callId: string,
  pingId: string,
  reply: PingReplyCommand,
) => Promise<ReplyResult>

/** What the card says after a ping closes, by its kind and final state. */
export function closedMessage(
  kind: PingKind,
  state: PingState,
  replyText: string | undefined,
): string {
  switch (state) {
    case 'answered':
      return replyText ? `Sent: “${replyText}”` : 'Your answer was sent.'
    case 'expired':
      return kind === 'field_approval'
        ? 'No reply in time, so nothing was shared.'
        : 'No reply in time. The agent will use your fallback or end politely.'
    case 'superseded':
      return 'They changed the offer, so this question was replaced.'
    case 'cancelled':
      return 'The call ended before you replied.'
    default:
      return 'This question is closed.'
  }
}
