import type { CallHistoryItem } from '../history/types'
import type { TranscriptLine } from './transcript'

/**
 * Data shapes for the end-of-call result page (PO-1 outcome, PO-2 summary, PO-3 what was shared).
 *
 * These mirror the backend contracts on `feat/backend-foundation`:
 * - `OutcomeDetails`, `businessConfirmed`, `openItems`: `contracts/tools/report_outcome.args.v1.json`
 * - `AgentDecision`: `contracts/payloads/agent.decision.v1.json` (the browser-visible part, no confidence score)
 * - `SharedField`: the `shared_fields` table in `*_snapshots_and_disclosure.sql`
 */

/** What the agent did on its own (DC-5). Open enum. */
export type DecisionAction =
  | 'accept'
  | 'counter'
  | 'decline'
  | 'share_field'
  | 'take_fallback'
  | 'end_call'
  | (string & {})

/** One decision the agent made, with its reason. */
export interface AgentDecision {
  /** What the other side offered or asked */
  situation: string
  action: DecisionAction
  reason: string
  /** For `share_field`: which profile field was shared */
  fieldKey?: string
  /** A preference the agent could not meet, in plain words */
  missedPreference?: string
}

/** What was agreed on the call. Every part is optional. */
export interface OutcomeDetails {
  /** e.g. "2026-11-12" */
  date?: string
  /** e.g. "19:00" */
  time?: string
  confirmationNumber?: string
  staffName?: string
}

/** How far a disclosed field got: approved by the user, handed to the agent, or said out loud. */
export type DisclosureState = 'approved' | 'sent_to_agent' | 'spoken' | (string & {})

/** One profile field disclosed on the call (PO-3). */
export interface SharedField {
  /** Profile field key, e.g. "phone" or "dob" */
  fieldKey: string
  state: DisclosureState
}

/** How a result item compares with what the user asked. Open: the backend may add more. */
export type ResultItemStatus = 'as_asked' | 'changed' | 'not_met' | 'extra' | (string & {})

/**
 * One asked-vs-got row (PO-2), already checked by the backend against the transcript
 * (`backend/app/postcall/result_items.py`, `docs/call-result-items.md`).
 */
export interface ResultItem {
  /** One to three words, e.g. "Pickup" */
  label: string
  /** The user's own words from the brief. Null for something the business added. */
  asked: string | null
  /** What was agreed */
  got: string
  status: ResultItemStatus
  reason: string | null
  /** "brief": the brief allowed it. "user": the user said yes on the call. "none": nobody did. */
  approvedBy: string | null
  /** Whole cents, for a fee or price the business said */
  costCents: number | null
  /** `seq` of the transcript lines that back this row, for "show in transcript" */
  evidence: number[]
  /** False when no transcript line backs the row (only an `as_asked` row is shown then) */
  verified: boolean
}

/** Everything the result page shows for one ended call. */
export interface CallResult {
  call: CallHistoryItem
  /** True only if the business explicitly confirmed the outcome in words */
  businessConfirmed: boolean
  details: OutcomeDetails
  openItems: string[]
  /** Asked-vs-got rows. Empty for calls whose report has none: the page then skips the table. */
  items: ResultItem[]
  /** One sentence on what the user must do next, or null */
  followUp: string | null
  decisions: AgentDecision[]
  sharedFields: SharedField[]
  /** Final transcript lines (PO-4), from the call's `transcript.final` events */
  transcript: TranscriptLine[]
}
