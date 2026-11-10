/**
 * Missing-context detection (PC-4): work out what the chat still has to ask
 * before a call, and never ask for anything the profile already has.
 *
 * Pure functions, so the backend brief builder (plan Step 9) can be checked
 * against the same tests.
 */
import type { CallTemplate, ProfileField, Sharing, TemplateField } from './types'

/** Answers given in the chat for this call only (template fields with source "call"). */
export type CallAnswers = Record<string, string>

/** Profile keys that default to Ask me first (ON-5). Everything else defaults to Share freely. */
const ASK_FIRST_BY_DEFAULT = new Set(['date_of_birth', 'address', 'insurance_carrier', 'insurance_member_id'])

/** Friendly names for profile keys, used in "I already have your ..." messages. */
const PROFILE_LABELS: Record<string, string> = {
  full_name: 'name',
  calling_name: 'name',
  phone: 'phone number',
  email: 'email',
  date_of_birth: 'date of birth',
  address: 'address',
  insurance_carrier: 'insurance',
  insurance_member_id: 'insurance',
}

/** ON-5 default sharing for a new profile field. The calling name is always shared (DC-1). */
export function defaultSharing(key: string): Sharing {
  if (key === 'calling_name') return 'share_freely'
  return ASK_FIRST_BY_DEFAULT.has(key) ? 'ask_first' : 'share_freely'
}

/** True when the profile has a non-blank value for `key`. */
export function hasProfileValue(profile: ProfileField[], key: string): boolean {
  return profile.some((f) => f.key === key && f.value.trim() !== '')
}

/** True when the chat already has a non-blank answer for `key` for this call. */
function hasAnswer(answers: CallAnswers, key: string): boolean {
  return (answers[key] ?? '').trim() !== ''
}

/**
 * The template's required fields that are still missing, in template order.
 * A profile field is missing only if the profile has no value for it. Ask-me-first
 * fields that are already in the profile are not missing: the agent pings the
 * user mid-call before sharing them (DC-6).
 */
export function missingTemplateFields(
  template: CallTemplate,
  profile: ProfileField[],
  answers: CallAnswers,
): TemplateField[] {
  return template.required_fields.filter((field) =>
    field.source === 'profile' ? !hasProfileValue(profile, field.key) : !hasAnswer(answers, field.key),
  )
}

/** The template's profile fields the profile already has, so the chat can say it won't ask for them. */
export function knownProfileFields(template: CallTemplate, profile: ProfileField[]): string[] {
  return template.required_fields
    .filter((field) => field.source === 'profile' && hasProfileValue(profile, field.key))
    .map((field) => field.key)
}

/** "your name, phone number and date of birth" for a list of profile keys (duplicates merged). */
export function describeProfileKeys(keys: string[]): string {
  const labels = [...new Set(keys.map((k) => PROFILE_LABELS[k] ?? k.replace(/_/g, ' ')))]
  if (labels.length === 0) return ''
  const list =
    labels.length === 1 ? labels[0] : `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`
  return `your ${list}`
}

/** Add or replace a profile field, keeping the others in order. */
export function withProfileField(profile: ProfileField[], key: string, value: string): ProfileField[] {
  const field: ProfileField = { key, value, sharing: defaultSharing(key) }
  return profile.some((f) => f.key === key)
    ? profile.map((f) => (f.key === key ? field : f))
    : [...profile, field]
}
