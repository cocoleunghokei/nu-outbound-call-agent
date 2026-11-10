import type { CallHistoryItem, CallOutcome } from './types'

/** Colour family for an outcome badge. */
export type OutcomeTone = 'ok' | 'warn' | 'bad' | 'neutral'

export interface OutcomeDisplay {
  label: string
  tone: OutcomeTone
}

const OUTCOMES: Record<string, OutcomeDisplay> = {
  booked: { label: 'Booked', tone: 'ok' },
  rescheduled: { label: 'Rescheduled', tone: 'ok' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
  needs_follow_up: { label: 'Needs follow-up', tone: 'warn' },
  refused_ai: { label: 'Failed: Refused AI', tone: 'bad' },
  wrong_number: { label: 'Failed: Wrong number', tone: 'bad' },
  no_answer: { label: 'Failed: No answer', tone: 'bad' },
  failed: { label: 'Failed', tone: 'bad' },
}

/**
 * Label and colour for a call's badge in the history list.
 *
 * A call with no outcome yet shows "In progress" or "Processing". An outcome
 * this version doesn't know shows "Unknown" instead of crashing, because the
 * outcome enum is open (architecture doc, rule 3).
 */
export function outcomeDisplay(item: Pick<CallHistoryItem, 'outcome' | 'endedAt'>): OutcomeDisplay {
  const outcome: CallOutcome = item.outcome
  if (outcome === null) {
    return item.endedAt === null
      ? { label: 'In progress', tone: 'neutral' }
      : { label: 'Processing', tone: 'neutral' }
  }
  return OUTCOMES[outcome] ?? { label: 'Unknown', tone: 'neutral' }
}

/** Milliseconds since 1970 for sorting; an unreadable date counts as oldest. */
function sortTime(iso: string): number {
  const time = Date.parse(iso)
  return Number.isNaN(time) ? 0 : time
}

/** Newest call first; a call with an unreadable date goes last. Returns a new array. */
export function sortNewestFirst(items: readonly CallHistoryItem[]): CallHistoryItem[] {
  return [...items].sort((a, b) => sortTime(b.createdAt) - sortTime(a.createdAt))
}

const dateFormat = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric' })

/** Short date for a history row, e.g. "Mon, Oct 26". */
export function formatCallDate(iso: string): string {
  const time = Date.parse(iso)
  return Number.isNaN(time) ? '' : dateFormat.format(time)
}
