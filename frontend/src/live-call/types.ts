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
