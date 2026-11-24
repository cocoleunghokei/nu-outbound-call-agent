/**
 * The browser's Supabase session, created silently when there is none.
 *
 * There is no sign-in screen. API mode still needs a session: the backend verifies a
 * Supabase JWT on every request and row-level security needs `auth.uid()`. So the app signs
 * the browser in anonymously on first use. That identity lives in this browser's storage:
 * clearing site data, a private window or another device starts a new user with no profile
 * and no history. The profile itself (name, phone, email) is collected on the first visit
 * and stored under this user (`profile.ts`); there is no email verification for now.
 *
 * Only the `auth` part of the client is used, so tests pass a small fake. Screens reach it
 * through `ensureSignedIn()` in `accessToken.ts`, which adds the shared client and makes
 * concurrent callers share one sign-in.
 */

/** The parts of a Supabase user this app reads. */
export interface SessionUser {
  id: string
  /** Set by Supabase on anonymous sessions; absent on tokens from before the claim existed. */
  is_anonymous?: boolean
  email?: string | null
}

/** Who the session belongs to: anonymous (this browser only) or a permanent, emailed user. */
export interface Account {
  userId: string
  anonymous: boolean
  email: string | null
}

/** The two auth calls this module needs; `SupabaseClient['auth']` satisfies it. */
export interface SessionAuth {
  getSession(): Promise<{ data: { session: { user: SessionUser } | null } }>
  signInAnonymously(): Promise<{
    data: { session: { user: SessionUser } | null }
    error: { message: string } | null
  }>
}

export type SessionResult =
  | { kind: 'session'; userId: string; account: Account }
  | { kind: 'failed'; message: string }

/** Thrown by `loadSupabaseTokenSource` when the anonymous sign-in itself fails. */
export class SignInFailedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SignInFailedError'
  }
}

export function accountOf(user: SessionUser): Account {
  // Supabase gives anonymous users an empty email, not a missing one.
  return { userId: user.id, anonymous: user.is_anonymous === true, email: user.email || null }
}

/**
 * Returns the current session's user, signing in anonymously first when there is none.
 * Never throws: a failed sign-in (rate limit, auth outage, anonymous sign-ins disabled) or
 * a session read that fails comes back as `{ kind: 'failed' }` with the error's message.
 */
export async function ensureSession(auth: SessionAuth): Promise<SessionResult> {
  try {
    const existing = await auth.getSession()
    if (existing.data.session) {
      const { user } = existing.data.session
      return { kind: 'session', userId: user.id, account: accountOf(user) }
    }
    const { data, error } = await auth.signInAnonymously()
    if (error) return { kind: 'failed', message: error.message }
    if (!data.session) return { kind: 'failed', message: 'No session was returned' }
    const { user } = data.session
    return { kind: 'session', userId: user.id, account: { userId: user.id, anonymous: true, email: null } }
  } catch (err) {
    return { kind: 'failed', message: err instanceof Error ? err.message : String(err) }
  }
}
