/**
 * Turns the call's event log into what the live-call screen shows.
 *
 * The screen never keeps its own copy of the call state. It folds the ordered
 * `call_events` rows into a view on every update, so a refresh, a reconnect or
 * a late event always lands in the same place.
 */
import { parsePing } from './ping'
import type {
  AgentDecisionPayload,
  CallCommandType,
  CallEndedPayload,
  Ping,
  PingState,
  CallEvent,
  CallOutcome,
  CallStatus,
  TranscriptRole,
} from './types'

/** One finished transcript line (DC-3). */
export interface TranscriptLine {
  /** `event_id`, stable across re-renders. */
  key: string
  seq: number
  role: TranscriptRole
  text: string
}

/**
 * Something the agent did on its own (DC-4, DC-5): the plain-language reason,
 * never a score. `missedPreference` is set for a Medium decision: the
 * constraints were met but a preference was not, so the feed flags it.
 */
export interface DecisionItem {
  key: string
  seq: number
  action: AgentDecisionPayload['action'] | (string & {})
  situation: string
  reason: string
  missedPreference?: string
}

/** One entry of the live feed, in call order. */
export type FeedItem =
  | ({ kind: 'line' } & TranscriptLine)
  | ({ kind: 'decision' } & DecisionItem)
  | { kind: 'asked'; key: string; seq: number; question: string; reason: string }
  | { kind: 'command'; key: string; seq: number; command: CallCommandType | (string & {}) }

/** The latest ping and where it stands. */
export interface PingCardView {
  ping: Ping
  /** `open` until a `ping.resolved` (or the call ending) closes it. */
  state: PingState
  /** The reply the agent received, from `ping.resolved`. */
  replyText?: string
  /** True when this question replaced an open one (the offer changed, DC-7). */
  replacesEarlier: boolean
}

/** Everything the screen renders, derived from the events. */
export interface LiveCallView {
  /** Latest server status. `creating` until the first status event arrives. */
  status: CallStatus
  /** Transcript lines only. */
  lines: TranscriptLine[]
  /** Transcript lines plus decisions, questions asked and your commands, in order. */
  feed: FeedItem[]
  /** When the current pause started (`occurred_at` of the move to `on_hold`), else null. */
  pausedAt: string | null
  /** The most recent ping, open or closed. Null until the agent asks something. */
  ping: PingCardView | null
  /** Set once `call.ended` arrives. Final: later events cannot reopen the call. */
  ended: { outcome: CallOutcome; endedReason: string; endedAt: string } | null
}

/** The status pills shown on the page, in order (DC-14). */
export const STATUS_STEPS = [
  { step: 'dialing', label: 'Dialing' },
  { step: 'in_phone_menu', label: 'In phone menu' },
  { step: 'on_hold', label: 'On hold' },
  { step: 'talking', label: 'Talking' },
  { step: 'waiting_on_you', label: 'Waiting on you' },
  { step: 'ended', label: 'Ended' },
] as const

export type StatusStep = (typeof STATUS_STEPS)[number]['step'] | 'unknown'

const TERMINAL_STATUSES: ReadonlySet<string> = new Set(['ended', 'failed'])
const TRANSCRIPT_ROLES: ReadonlySet<string> = new Set(['agent', 'business', 'user'])
const COMMANDS: ReadonlySet<string> = new Set(['pause', 'resume', 'end'])
const MAX_MISSED_PREFERENCE = 200

/**
 * Maps a server status to the pill that lights up.
 * Statuses before the call connects count as Dialing, `failed` counts as Ended,
 * and anything this client doesn't know is `unknown` (shown as "Unknown").
 */
export function statusStep(status: CallStatus): StatusStep {
  switch (status) {
    case 'creating':
    case 'creation_uncertain':
    case 'dialing':
      return 'dialing'
    case 'failed':
    case 'ended':
      return 'ended'
    case 'in_phone_menu':
      return 'in_phone_menu'
    case 'on_hold':
      return 'on_hold'
    case 'talking':
      return 'talking'
    case 'waiting_on_you':
      return 'waiting_on_you'
    default:
      return 'unknown'
  }
}

/**
 * Adds new events to the ones already held, dropping duplicates and keeping
 * them ordered by `seq`. Realtime can redeliver a row or deliver rows out of
 * order after a reconnect; this makes both harmless.
 */
export function mergeEvents(
  held: readonly CallEvent[],
  incoming: readonly CallEvent[],
): CallEvent[] {
  const bySeq = new Map<number, CallEvent>()
  for (const event of held) bySeq.set(event.seq, event)
  for (const event of incoming) {
    if (!bySeq.has(event.seq)) bySeq.set(event.seq, event)
  }
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
}

/** Folds events (ordered by `seq`) into the view. Unknown or malformed events are skipped. */
export function deriveLiveCall(events: readonly CallEvent[]): LiveCallView {
  const view: LiveCallView = {
    status: 'creating',
    lines: [],
    feed: [],
    pausedAt: null,
    ping: null,
    ended: null,
  }

  for (const event of events) {
    switch (event.type) {
      case 'status.changed': {
        const status = event.payload.status
        if (typeof status !== 'string' || status === '') break
        if (view.ended !== null || TERMINAL_STATUSES.has(view.status)) break
        if (status === 'on_hold' && view.status !== 'on_hold') view.pausedAt = event.occurred_at
        if (status !== 'on_hold') view.pausedAt = null
        view.status = status
        break
      }
      case 'transcript.final': {
        const { role, text } = event.payload
        if (typeof role !== 'string' || !TRANSCRIPT_ROLES.has(role)) break
        if (typeof text !== 'string' || text.trim() === '') break
        const line: TranscriptLine = {
          key: event.event_id,
          seq: event.seq,
          role: role as TranscriptRole,
          text,
        }
        view.lines.push(line)
        view.feed.push({ kind: 'line', ...line })
        break
      }
      case 'agent.decision': {
        const { situation, action, reason, missed_preference: missed } = event.payload
        if (typeof action !== 'string' || action === '') break
        if (typeof reason !== 'string' || reason.trim() === '') break
        const missedPreference =
          typeof missed === 'string' && missed.trim() !== ''
            ? missed.trim().slice(0, MAX_MISSED_PREFERENCE)
            : undefined
        view.feed.push({
          kind: 'decision',
          key: event.event_id,
          seq: event.seq,
          action,
          situation: typeof situation === 'string' ? situation : '',
          reason,
          ...(missedPreference !== undefined ? { missedPreference } : {}),
        })
        break
      }
      case 'command.applied': {
        const command = event.payload.command
        // `instruction` is DC-10 (pre-call owner); it gets its own feed entry there.
        if (typeof command !== 'string' || !COMMANDS.has(command)) break
        view.feed.push({ kind: 'command', key: event.event_id, seq: event.seq, command })
        break
      }
      case 'ping.opened': {
        if (view.ended !== null) break
        const ping = parsePing(event.payload)
        // A question stored as already closed (e.g. superseded on arrival) is never shown.
        if (ping === null || ping.state !== 'open') break
        const previous = view.ping
        if (previous !== null && previous.ping.ping_id === ping.ping_id) {
          // The same ping again (e.g. its clock was frozen or restored): refresh
          // its data, but never reopen it once closed.
          if (previous.state === 'open') view.ping = { ...previous, ping }
          break
        }
        view.ping = {
          ping,
          state: 'open',
          // The old ping's `superseded` event can arrive before or after this one.
          replacesEarlier:
            previous !== null && (previous.state === 'open' || previous.state === 'superseded'),
        }
        view.feed.push({
          kind: 'asked',
          key: event.event_id,
          seq: event.seq,
          question: ping.question,
          reason: ping.reason,
        })
        break
      }
      case 'ping.resolved': {
        const { ping_id: pingId, state, reply_text: replyText } = event.payload
        if (view.ping === null || view.ping.state !== 'open') break
        if (pingId !== view.ping.ping.ping_id) break
        if (typeof state !== 'string' || state === '' || state === 'open') break
        view.ping = {
          ...view.ping,
          state,
          ...(typeof replyText === 'string' ? { replyText } : {}),
        }
        break
      }
      case 'call.ended': {
        if (view.ended !== null) break
        if (view.ping !== null && view.ping.state === 'open') {
          view.ping = { ...view.ping, state: 'cancelled' }
        }
        const payload = event.payload as Partial<CallEndedPayload>
        view.ended = {
          outcome: typeof payload.outcome === 'string' ? payload.outcome : null,
          endedReason: typeof payload.ended_reason === 'string' ? payload.ended_reason : '',
          endedAt: event.occurred_at,
        }
        if (!TERMINAL_STATUSES.has(view.status)) view.status = 'ended'
        view.pausedAt = null
        break
      }
      default:
        // `field.shared` belongs to the post-call "what was shared" log. Unknown
        // types are expected (open enum) and are ignored on purpose.
        break
    }
  }

  return view
}
