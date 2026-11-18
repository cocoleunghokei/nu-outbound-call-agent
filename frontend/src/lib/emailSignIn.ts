/**
 * Adding an email to the session, verified by a 6-digit code (ON-1).
 *
 * Placing a call needs a permanent user (the backend answers `403 sign_in_required` to an
 * anonymous session). Two Supabase flows, chosen by who is signed in right now:
 *
 * - Anonymous session: `updateUser({ email })` sends a code to the new address, and
 *   `verifyOtp({ type: 'email_change' })` links it. The user id stays the same, so the
 *   profile and the briefs saved so far stay with the user.
 * - That email already belongs to an account (a returning user on a new device), or nobody
 *   is signed in: `signInWithOtp({ email })` then `verifyOtp({ type: 'email' })`. The browser
 *   switches to that account; what the anonymous session saved does not follow.
 *
 * The email templates must print `{{ .Token }}` for a code to arrive (see docs/devops.md).
 * No React here: `EmailStep.tsx` renders these results. Only the `auth` part of the client
 * is used, so tests pass a small fake.
 */
import type { Account } from './session'

interface AuthError {
  message: string
  code?: string
}

/** The three auth calls this module needs; `SupabaseClient['auth']` satisfies it. */
export interface EmailAuth {
  updateUser(attributes: { email: string }): Promise<{ error: AuthError | null }>
  signInWithOtp(credentials: {
    email: string
    options?: { shouldCreateUser?: boolean }
  }): Promise<{ error: AuthError | null }>
  verifyOtp(params: { email: string; token: string; type: VerifyAs }): Promise<{ error: AuthError | null }>
}

/** Which Supabase verification the code completes. */
export type VerifyAs = 'email_change' | 'email'

export interface CodeSent {
  kind: 'code_sent'
  email: string
  verify: VerifyAs
  /** True when verifying signs this browser in as another, existing account. */
  switchesUser: boolean
}

export type RequestResult = CodeSent | { kind: 'failed'; message: string }
export type VerifyResult = { kind: 'verified' } | { kind: 'failed'; message: string }

export const CODE_LENGTH = 6

/** Good enough to catch a typo before Supabase validates for real. */
export function isPlausibleEmail(text: string): boolean {
  const trimmed = text.trim()
  const at = trimmed.indexOf('@')
  return at > 0 && at === trimmed.lastIndexOf('@') && at < trimmed.length - 1 && !/\s/.test(trimmed)
}

function emailAlreadyRegistered(error: AuthError): boolean {
  return error.code === 'email_exists' || /already (been )?registered|already exists/i.test(error.message)
}

function sendFailure(error: AuthError): { kind: 'failed'; message: string } {
  if (error.code === 'over_email_send_rate_limit' || /rate limit/i.test(error.message)) {
    return { kind: 'failed', message: 'Too many emails just now. Try again in a minute.' }
  }
  if (error.code === 'validation_failed' || /invalid/i.test(error.message)) {
    return { kind: 'failed', message: "That doesn't look like an email address. Check it and try again." }
  }
  return { kind: 'failed', message: `Couldn't send the code: ${error.message}` }
}

/**
 * Sends a code to `email`. `account` is the current session's user, or null when nobody is
 * signed in. Never throws.
 */
export async function requestCode(auth: EmailAuth, email: string, account: Account | null): Promise<RequestResult> {
  const address = email.trim()
  if (!isPlausibleEmail(address)) {
    return { kind: 'failed', message: "That doesn't look like an email address. Check it and try again." }
  }
  try {
    if (account?.anonymous) {
      const linked = await auth.updateUser({ email: address })
      if (!linked.error) return { kind: 'code_sent', email: address, verify: 'email_change', switchesUser: false }
      if (!emailAlreadyRegistered(linked.error)) return sendFailure(linked.error)
      // A returning user: sign in to that account instead of linking.
      const existing = await auth.signInWithOtp({ email: address, options: { shouldCreateUser: false } })
      if (existing.error) return sendFailure(existing.error)
      return { kind: 'code_sent', email: address, verify: 'email', switchesUser: true }
    }
    const sent = await auth.signInWithOtp({ email: address, options: { shouldCreateUser: true } })
    if (sent.error) return sendFailure(sent.error)
    return { kind: 'code_sent', email: address, verify: 'email', switchesUser: account !== null }
  } catch (err) {
    return sendFailure({ message: err instanceof Error ? err.message : String(err) })
  }
}

/** Verifies the code the user typed for the email `sent` went to. Never throws. */
export async function verifyCode(auth: EmailAuth, sent: CodeSent, code: string): Promise<VerifyResult> {
  const token = code.replace(/\D/g, '')
  if (token.length !== CODE_LENGTH) {
    return { kind: 'failed', message: `The code is ${CODE_LENGTH} digits.` }
  }
  try {
    const { error } = await auth.verifyOtp({ email: sent.email, token, type: sent.verify })
    if (!error) return { kind: 'verified' }
    if (error.code === 'otp_expired' || /expired|invalid/i.test(error.message)) {
      return { kind: 'failed', message: "That code didn't match or has expired. Request a new one." }
    }
    return { kind: 'failed', message: `Couldn't verify the code: ${error.message}` }
  } catch (err) {
    return { kind: 'failed', message: `Couldn't verify the code: ${err instanceof Error ? err.message : String(err)}` }
  }
}
