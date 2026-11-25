/**
 * Google sign-in for the browser's Supabase account.
 *
 * Everyone starts as an anonymous guest (`session.ts`). Connecting Google upgrades that same
 * user in place (`linkIdentity`): the user id stays, so the profile, saved briefs and call
 * history carry over, and signing in later from another browser finds them again.
 *
 * Both calls send the browser to Google and back, so they never resolve to a signed-in
 * state here: they return `redirecting`, and the page that loads after the redirect sees
 * the new session. A problem that happens on the way back (for example the Google account
 * already belongs to another user) arrives in the address, and `authRedirectProblem` reads it.
 *
 * Only the `auth` part of the client is used, so tests pass a small fake.
 */

/** Where the sign-in leaves the user once Google is done, and which sign-in Google shows. */
interface OAuthRequest {
  provider: 'google'
  options: { redirectTo: string }
}

/** The auth calls this module needs; `SupabaseClient['auth']` satisfies it. */
export interface GoogleAuth {
  linkIdentity(request: OAuthRequest): Promise<{ error: { message: string } | null }>
  signInWithOAuth(request: OAuthRequest): Promise<{ error: { message: string } | null }>
  signOut(): Promise<{ error: { message: string } | null }>
}

export type GoogleResult = { kind: 'redirecting' } | { kind: 'failed'; message: string }

async function run(call: () => Promise<{ error: { message: string } | null }>): Promise<GoogleResult> {
  try {
    const { error } = await call()
    return error ? { kind: 'failed', message: error.message } : { kind: 'redirecting' }
  } catch (err) {
    return { kind: 'failed', message: err instanceof Error ? err.message : String(err) }
  }
}

/** Upgrades the current account (a guest, or a user without Google) by adding a Google sign-in to it. */
export function connectGoogle(auth: GoogleAuth, redirectTo: string): Promise<GoogleResult> {
  return run(() => auth.linkIdentity({ provider: 'google', options: { redirectTo } }))
}

/**
 * Signs in to a Google account that already has an account here, for example on a new
 * device. The guest account in this browser is left behind, with its data.
 */
export function signInWithGoogle(auth: GoogleAuth, redirectTo: string): Promise<GoogleResult> {
  return run(() => auth.signInWithOAuth({ provider: 'google', options: { redirectTo } }))
}

/** Signs out. The next page load starts a fresh guest account. */
export async function signOutOfAccount(auth: Pick<GoogleAuth, 'signOut'>): Promise<GoogleResult> {
  try {
    const { error } = await auth.signOut()
    return error ? { kind: 'failed', message: error.message } : { kind: 'redirecting' }
  } catch (err) {
    return { kind: 'failed', message: err instanceof Error ? err.message : String(err) }
  }
}

/**
 * The problem Supabase put in the address after a sign-in redirect, in words for the user,
 * or null when there is none. Supabase writes `error`, `error_code` and `error_description`
 * into the hash or the query string, depending on the flow.
 */
export function authRedirectProblem(url: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  const fromHash = new URLSearchParams(parsed.hash.replace(/^#/, ''))
  const fromQuery = parsed.searchParams
  const read = (key: string) => fromHash.get(key) ?? fromQuery.get(key)
  const code = read('error_code')
  const error = read('error')
  if (!code && !error) return null
  if (code === 'identity_already_exists') {
    return 'That Google account already has an account here. Choose "Sign in to an existing account" instead.'
  }
  if (error === 'access_denied') return 'Google sign-in was cancelled.'
  return read('error_description')?.replace(/\+/g, ' ') || 'Google sign-in did not work. Try again.'
}
