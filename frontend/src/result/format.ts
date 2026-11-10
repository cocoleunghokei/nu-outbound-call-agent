import type { DecisionAction, DisclosureState, OutcomeDetails } from './types'

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
  return ACTION_LABELS[action] ?? 'Acted'
}

const FIELD_LABELS: Record<string, string> = {
  name: 'Full name',
  calling_name: 'Calling name',
  phone: 'Phone',
  email: 'Email',
  dob: 'Date of birth',
  address: 'Home address',
  insurance: 'Insurance',
  provider: 'Doctor',
  party_size: 'Party size',
  dietary: 'Dietary needs',
}

/** Readable name for a profile field key. Unknown keys are shown as written, with underscores as spaces. */
export function fieldLabel(fieldKey: string): string {
  return FIELD_LABELS[fieldKey] ?? fieldKey.replace(/_/g, ' ')
}

const STATE_LABELS: Record<string, string> = {
  spoken: 'Said on the call',
  sent_to_agent: 'Given to the agent, not said',
  approved: 'Approved, not said',
}

/** What happened to a shared field, in plain words. */
export function disclosureLabel(state: DisclosureState): string {
  return STATE_LABELS[state] ?? 'Unknown'
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
