/**
 * The user's profile (ON-3), read and written by the browser.
 *
 * There is no profile API yet. The browser reads and writes `profiles` and `profile_fields`
 * itself through the Supabase client; row-level security limits it to its own rows, and the
 * anonymous session (`session.ts`) carries the `authenticated` role those policies need.
 * Onboarding collects the required fields once (full name, calling name, phone) plus the
 * email, all "share freely" (ON-5 defaults; the calling name can't be anything else), and
 * stamps `profiles.onboarded_at` (ON-8). The email is stored as typed, not verified.
 *
 * The behaviour here is pure over a two-method `ProfileStore`, so tests pass a fake;
 * `supabaseProfileStore` is the real one.
 */
import type { SupabaseClient } from '@supabase/supabase-js'
import { checkPhone } from '../precall/phone'
import type { ProfileField } from '../precall/types'

/** What the store holds for one user. `onboardedAt` is null until onboarding is saved. */
export interface LoadedProfile {
  onboardedAt: string | null
  fields: ProfileField[]
}

/** The two operations this module needs; `supabaseProfileStore` implements them. */
export interface ProfileStore {
  load(userId: string): Promise<LoadedProfile>
  /** Upserts the profile row (with `onboardedAt`) first, then every field. */
  save(userId: string, fields: ProfileField[], onboardedAt: string): Promise<void>
}

export interface OnboardingInput {
  fullName: string
  /** Empty means "use the default", the first word of the full name. */
  callingName: string
  phone: string
  email: string
}

export type OnboardingCheck = { ok: true; fields: ProfileField[] } | { ok: false; problem: string }

export type SaveOutcome = { kind: 'saved'; profile: LoadedProfile } | { kind: 'failed'; message: string }

export const EMPTY_PROFILE: LoadedProfile = { onboardedAt: null, fields: [] }

export function isOnboarded(profile: LoadedProfile): boolean {
  return profile.onboardedAt !== null
}

/** The non-blank value of one field, or null. */
export function fieldValue(fields: ProfileField[], key: string): string | null {
  const value = fields.find((f) => f.key === key)?.value.trim()
  return value ? value : null
}

/** The calling name the form suggests: the first word of the full name (ON-3). */
export function defaultCallingName(fullName: string): string {
  return fullName.trim().split(/\s+/)[0] ?? ''
}

/** A cheap shape check so a typo is caught before it is stored; nothing is sent to it. */
export function isPlausibleEmail(text: string): boolean {
  const trimmed = text.trim()
  const at = trimmed.indexOf('@')
  return at > 0 && at === trimmed.lastIndexOf('@') && at < trimmed.length - 1 && !/\s/.test(trimmed)
}

/** Checks the form and turns it into the rows to store. */
export function validateOnboarding(input: OnboardingInput): OnboardingCheck {
  const fullName = input.fullName.trim()
  if (fullName === '') return { ok: false, problem: 'Enter your name.' }
  const callingName = input.callingName.trim() || defaultCallingName(fullName)
  if (callingName === '') return { ok: false, problem: 'Enter the name the agent should use for you.' }

  const phone = checkPhone(input.phone)
  if (!phone.ok) {
    const problem =
      phone.reason === 'empty' || phone.reason === 'invalid' ? 'Enter your 10-digit US phone number.' : phone.message
    return { ok: false, problem }
  }

  const email = input.email.trim()
  if (!isPlausibleEmail(email)) return { ok: false, problem: "That doesn't look like an email address. Check it and try again." }

  return {
    ok: true,
    fields: [
      { key: 'full_name', value: fullName, sharing: 'share_freely' },
      { key: 'calling_name', value: callingName, sharing: 'share_freely' },
      { key: 'phone', value: phone.e164, sharing: 'share_freely' },
      { key: 'email', value: email, sharing: 'share_freely' },
    ],
  }
}

/** Loads the profile; a failed read counts as "not onboarded", so the form shows (saving again is harmless). */
export async function loadProfile(store: ProfileStore, userId: string): Promise<LoadedProfile> {
  try {
    return await store.load(userId)
  } catch (err) {
    console.warn('profile could not be loaded; asking for it again', err)
    return EMPTY_PROFILE
  }
}

/** Validates and stores the onboarding form. Never throws. */
export async function saveOnboarding(
  store: ProfileStore,
  userId: string,
  input: OnboardingInput,
  now: () => Date = () => new Date(),
): Promise<SaveOutcome> {
  const checked = validateOnboarding(input)
  if (!checked.ok) return { kind: 'failed', message: checked.problem }
  const onboardedAt = now().toISOString()
  try {
    await store.save(userId, checked.fields, onboardedAt)
  } catch (err) {
    const why = err instanceof Error ? err.message : String(err)
    return { kind: 'failed', message: `I couldn't save your profile (${why}). Try again.` }
  }
  return { kind: 'saved', profile: { onboardedAt, fields: checked.fields } }
}

const SHARING = new Set(['share_freely', 'ask_first'])

function toField(row: unknown): ProfileField | null {
  if (typeof row !== 'object' || row === null) return null
  const { key, value, sharing } = row as Record<string, unknown>
  if (typeof key !== 'string' || typeof value !== 'string' || typeof sharing !== 'string' || !SHARING.has(sharing)) {
    return null
  }
  return { key, value, sharing: sharing as ProfileField['sharing'] }
}

/** The store over the shared client's `profiles` and `profile_fields` tables (RLS: own rows only). */
export function supabaseProfileStore(client: SupabaseClient): ProfileStore {
  return {
    async load(userId) {
      const profile = await client.from('profiles').select('onboarded_at').eq('id', userId).maybeSingle()
      if (profile.error) throw new Error(profile.error.message)
      const rows = await client.from('profile_fields').select('key, value, sharing').eq('user_id', userId)
      if (rows.error) throw new Error(rows.error.message)
      const onboardedAt: unknown = profile.data?.onboarded_at
      return {
        onboardedAt: typeof onboardedAt === 'string' ? onboardedAt : null,
        fields: (rows.data ?? []).map(toField).filter((f): f is ProfileField => f !== null),
      }
    },
    async save(userId, fields, onboardedAt) {
      // The profile row first: `profile_fields` references it.
      const profile = await client.from('profiles').upsert({ id: userId, onboarded_at: onboardedAt })
      if (profile.error) throw new Error(profile.error.message)
      const written = await client
        .from('profile_fields')
        .upsert(
          fields.map((f) => ({ user_id: userId, key: f.key, value: f.value, sharing: f.sharing })),
          { onConflict: 'user_id,key' },
        )
      if (written.error) throw new Error(written.error.message)
    },
  }
}

/** The real store, loaded lazily so demo mode never touches Supabase. */
export async function loadSupabaseProfileStore(): Promise<ProfileStore> {
  const { supabase } = await import('./supabase')
  return supabaseProfileStore(supabase)
}
