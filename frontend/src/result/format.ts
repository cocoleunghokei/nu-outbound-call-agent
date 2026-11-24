import type { DecisionAction, DisclosureState, OutcomeDetails, ResultItem } from './types'

/**
 * `labels[key]` if `key` is one of the map's own keys, else undefined. Keys come from
 * the server, so "__proto__" or "constructor" must not reach Object.prototype.
 */
function ownLabel(labels: Record<string, string>, key: string): string | undefined {
  return Object.prototype.hasOwnProperty.call(labels, key) ? labels[key] : undefined
}

const ACTION_LABELS: Record<string, string> = {
  accept: 'Accepted',
  counter: 'Countered',
  decline: 'Declined',
  share_field: 'Shared',
  take_fallback: 'Took a fallback',
  end_call: 'Ended the call',
}

/** Short label for a decision's action. Unknown actions show "Acted". */
export function actionLabel(action: DecisionAction): string {
  return ownLabel(ACTION_LABELS, action) ?? 'Acted'
}

/**
 * Profile and template field keys, as listed in docs/template-format.md (#35).
 * The profile key list and the insurance key names are still to be confirmed by Steve.
 */
const FIELD_LABELS: Record<string, string> = {
  full_name: 'Full name',
  calling_name: 'Calling name',
  phone: 'Phone',
  email: 'Email',
  date_of_birth: 'Date of birth',
  address: 'Home address',
  insurance_carrier: 'Insurance carrier',
  insurance_member_id: 'Insurance member ID',
  provider_name: 'Doctor',
  party_size: 'Party size',
}

/** Readable name for a profile field key. Unknown keys are shown as written, with underscores as spaces. */
export function fieldLabel(fieldKey: string): string {
  return ownLabel(FIELD_LABELS, fieldKey) ?? fieldKey.replace(/_/g, ' ')
}

const STATE_LABELS: Record<string, string> = {
  spoken: 'Said on the call',
  sent_to_agent: 'Given to the agent, not said',
  approved: 'Approved, not said',
}

/** What happened to a shared field, in plain words. */
export function disclosureLabel(state: DisclosureState): string {
  return ownLabel(STATE_LABELS, state) ?? 'Unknown'
}

const dateFormat = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
const timeFormat = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' })

/** "2026-11-12" to "Thu, Nov 12". Anything else is returned unchanged. */
export function formatAgreedDate(date: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  if (!match) return date
  const [, y, m, d] = match
  return dateFormat.format(new Date(Number(y), Number(m) - 1, Number(d)))
}

/** "19:00" to "7:00 PM". Anything else is returned unchanged. */
export function formatAgreedTime(time: string): string {
  const match = /^(\d{2}):(\d{2})$/.exec(time)
  if (!match) return time
  const [, h, min] = match
  return timeFormat.format(new Date(2000, 0, 1, Number(h), Number(min)))
}

/** The agreed details as label/value pairs, in display order, skipping empty ones. */
export function agreedRows(details: OutcomeDetails): Array<[label: string, value: string]> {
  const rows: Array<[string, string]> = []
  if (details.date) rows.push(['Date', formatAgreedDate(details.date)])
  if (details.time) rows.push(['Time', formatAgreedTime(details.time)])
  if (details.confirmationNumber) rows.push(['Confirmation', details.confirmationNumber])
  if (details.staffName) rows.push(['Staff', details.staffName])
  return rows
}

/** 300 cents as "$3.00". */
export function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

/** True if the row differs from what the user asked. A status this version doesn't know counts. */
export function isChange(item: Pick<ResultItem, 'status'>): boolean {
  return item.status !== 'as_asked'
}

/** The table's headline numbers: how many rows changed, and the fees and prices in all rows. */
export function changeSummary(items: readonly ResultItem[]): { changes: number; extraCents: number } {
  let extraCents = 0
  for (const item of items) extraCents += item.costCents ?? 0
  return { changes: items.filter(isChange).length, extraCents }
}

/** "3 changes · +$3", "1 change", or "As you asked" when nothing changed. */
export function summaryLabel(items: readonly ResultItem[]): string {
  const { changes, extraCents } = changeSummary(items)
  if (changes === 0) return 'As you asked'
  const parts = [`${changes} ${changes === 1 ? 'change' : 'changes'}`]
  if (extraCents > 0) parts.push(`+${formatCents(extraCents).replace('.00', '')}`)
  return parts.join(' · ')
}

/**
 * A short note on who allowed a changed row, or null when there is nothing to add. Only rows
 * that differ from the brief get one: "as asked" was allowed by definition.
 */
export function approvalNote(item: Pick<ResultItem, 'status' | 'approvedBy'>): string | null {
  if (!isChange(item)) return null
  switch (item.approvedBy) {
    case 'user':
      return 'You approved this on the call.'
    case 'brief':
      return 'Within your brief.'
    case 'none':
      return 'Not approved. Check it.'
    default:
      return null
  }
}
