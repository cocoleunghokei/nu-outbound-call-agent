/**
 * Data shapes for the live-call screen.
 *
 * These are copied from the generated contract types in PR #19
 * (`frontend/src/contracts/generated.ts` on `feat/backend-foundation`), with the
 * same names and fields. Once #19 merges, delete the copies below and import
 * from `../contracts/generated` instead. Do not add fields to the copied
 * types that the contracts do not have.
 */

/** Who produced an event. */
export type EventSource = 'vapi' | 'backend' | 'user'

/**
 * One row of `call_events`, as Supabase Realtime delivers it.
 * `type` is an open enum: an unknown type must be ignored, never crash the UI.
 */
export interface CallEvent {
  event_id: string
  call_id: string
  /** Gap-free per call, assigned on insert. */
  seq: number
  type: string
  occurred_at: string
  source?: EventSource
  dedup_key?: string
  schema_version: 1
  payload: Record<string, unknown>
}

/** Server call status (DC-14). Open enum: unknown values show as "Unknown". */
export type CallStatus =
  | 'creating'
  | 'creation_uncertain'
  | 'dialing'
  | 'in_phone_menu'
  | 'talking'
  | 'waiting_on_you'
  | 'on_hold'
  | 'ended'
  | 'failed'
  | (string & {})

export interface StatusChangedPayload {
  status: CallStatus
}

/** Speaker of a transcript line (DC-3). */
export type TranscriptRole = 'agent' | 'business' | 'user'

export interface TranscriptFinalPayload {
  role: TranscriptRole
  text: string
  source_start_ts: string | null
}

export type CallOutcome =
  | 'booked'
  | 'cancelled'
  | 'rescheduled'
  | 'needs_follow_up'
  | 'refused_ai'
  | 'wrong_number'
  | 'failed'
  | 'no_answer'
  | (string & {})
  | null

export interface CallEndedPayload {
  outcome: CallOutcome
  ended_reason: string
}

/**
 * What the page header shows. Not a contract type: it is a `calls` row joined
 * with its `briefs` row (migrations on `feat/backend-foundation`).
 */
export interface LiveCallSummary {
  /** `calls.id` */
  callId: string
  /** `briefs.business_name` */
  businessName: string
  /** `briefs.phone_e164`, e.g. "+16175550142" */
  phoneE164: string
  /** `briefs.goal` */
  goal: string
  /** `calls.created_at`, ISO 8601. Starts the call clock. */
  createdAt: string
  /** `calls.max_duration_s`, the hard cap shown next to the clock (DC-15). */
  maxDurationS: number
}

/** `decision`: the agent needs a call from you. `field_approval`: it wants an Ask-me-first field (DC-6). */
export type PingKind = 'decision' | 'field_approval'

/** Open enum: an unknown state is treated as closed. */
export type PingState = 'open' | 'answered' | 'expired' | 'superseded' | 'cancelled' | (string & {})

/** A question from the agent to the user (DC-7, DC-8). Payload of `ping.opened`. */
export interface Ping {
  ping_id: string
  call_id: string
  kind: PingKind
  /** Only for field_approval. */
  field_key?: string
  question: string
  reason: string
  suggested_replies: string[]
  /** Null while the call is paused (the clock is frozen). */
  expires_at: string | null
  /** Time left on the reply clock, in milliseconds. */
  remaining_ms: number | null
  state: PingState
}

/** Payload of `ping.resolved`. */
export interface PingResolvedPayload {
  ping_id: string
  state: PingState
  reply_text?: string
}

/** Body of `POST /calls/{call_id}/pings/{ping_id}/reply`: a suggested reply by index, or free text. */
export type PingReplyCommand = PingReplyChoice | PingReplyText

export interface PingReplyChoice {
  choice: number
}

export interface PingReplyText {
  text: string
}

/** Commands the browser sends with `POST /calls/{call_id}/commands`. */
export type CallCommandType = 'instruction' | 'pause' | 'resume' | 'end'

/** Body of `POST /calls/{call_id}/commands`. `text` is only for `instruction`. */
export interface CallCommand {
  type: CallCommandType
  text?: string
}

export type DecisionAction =
  | 'accept'
  | 'counter'
  | 'decline'
  | 'share_field'
  | 'take_fallback'
  | 'end_call'

/** Payload of `agent.decision`. Deliberately has no confidence and no dimensions (DC-5). */
export interface AgentDecisionPayload {
  situation: string
  action: DecisionAction
  reason: string
  field_key?: string
  /** The preference the agent could not meet, flagged in the live feed (DC-4 Medium). */
  missed_preference?: string
}
