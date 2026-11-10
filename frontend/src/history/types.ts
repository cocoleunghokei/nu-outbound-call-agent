/**
 * Data shapes for the call history list (PO-6).
 *
 * These mirror the backend's tables on `feat/backend-foundation`
 * (`supabase/migrations/*_calls_and_events.sql` and `*_briefs.sql`): one history
 * item is a `calls` row joined with its `briefs` row. When the backend's history
 * endpoint lands, swap the fake data for it and keep these field names.
 */

/** Final result of a call (PO-1). Open enum: new values can appear, so never assume the list is complete. */
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

/** Lifecycle status of a call (DC-14). Open enum, like `CallOutcome`. */
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

/** One row of the call history list. */
export interface CallHistoryItem {
  /** `calls.id` */
  id: string
  /** `briefs.business_name` */
  businessName: string
  /** `briefs.phone_e164`, e.g. "+16175550133" */
  phoneE164: string
  /** `briefs.goal`, the user's goal in plain words */
  goal: string
  /** `briefs.template_key`, or null for a custom call */
  templateKey: string | null
  /** `calls.status` */
  status: CallStatus
  /** `calls.outcome`, null until the call has ended and been processed */
  outcome: CallOutcome
  /** `calls.created_at`, ISO 8601 */
  createdAt: string
  /** `calls.ended_at`, ISO 8601, or null while the call is still running */
  endedAt: string | null
}
