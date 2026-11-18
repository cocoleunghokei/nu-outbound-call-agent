/**
 * The browser's Supabase session, created silently when there is none.
 *
 * There is no sign-in screen yet (ON-1 is P1). API mode still needs a session: the backend
 * verifies a Supabase JWT on every request and row-level security needs `auth.uid()`. So the
 * app signs the browser in anonymously on first use. The identity lives in this browser's
 * storage: clearing site data, a private window or another device starts a new user with
 * no profile and no history. Linking an email to it comes later.
 *
 * Only the `auth` part of the client is used, so tests pass a small fake.
 */

/** The two auth calls this module needs; `SupabaseClient['auth']` satisfies it. */
export interface SessionAuth {
  getSession(): Promise<{ data: { session: { user: { id: string } } | null } }>
  signInAnonymously(): Promise<{
    data: { session: { user: { id: string } } | null }
    error: { message: string } | null
  }>
}

export type SessionResult = { kind: 'session'; userId: string } | { kind: 'failed'; message: string }

/** Thrown by `loadSupabaseTokenSource` when the anonymous sign-in itself fails. */
export class SignInFailedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SignInFailedError'
  }
}

/**
 * Returns the current session's user, signing in anonymously first when there is none.
 * Never throws: a failed sign-in (rate limit, auth outage, anonymous sign-ins disabled)
 * comes back as `{ kind: 'failed' }` with the server's message.
 */
export async function ensureSession(auth: SessionAuth): Promise<SessionResult> {
  const existing = await auth.getSession()
  if (existing.data.session) return { kind: 'session', userId: existing.data.session.user.id }
  try {
    const { data, error } = await auth.signInAnonymously()
    if (error) return { kind: 'failed', message: error.message }
    if (!data.session) return { kind: 'failed', message: 'No session was returned' }
    return { kind: 'session', userId: data.session.user.id }
  } catch (err) {
    return { kind: 'failed', message: err instanceof Error ? err.message : String(err) }
  }
}
