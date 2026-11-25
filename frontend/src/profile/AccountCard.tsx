import { useEffect, useState } from 'react'
import {
  authRedirectProblem,
  connectGoogle,
  signInWithGoogle,
  signOutOfAccount,
  type GoogleAuth,
  type GoogleResult,
} from '../lib/googleSignIn'
import type { Account } from '../lib/session'

interface AccountCardProps {
  account: Account
  /** Where Google sends the user back to. Defaults to this screen. */
  redirectTo?: string
  /** Injected in tests. */
  loadAuth?: () => Promise<GoogleAuth>
}

async function loadSupabaseAuth(): Promise<GoogleAuth> {
  const { supabase } = await import('../lib/supabase')
  return supabase.auth
}

/**
 * The account on the profile screen. A guest sees why to sign in (keep the profile and
 * history across browsers) and a Google button that upgrades the guest in place. A signed-in
 * user sees their email and can sign out. All the sign-in logic is in `lib/googleSignIn.ts`.
 */
export function AccountCard({
  account,
  redirectTo = `${window.location.origin}/profile`,
  loadAuth = loadSupabaseAuth,
}: AccountCardProps) {
  const [busy, setBusy] = useState(false)
  // A problem from the trip to Google and back arrives in the address; read it once.
  const [problem, setProblem] = useState<string | null>(() => authRedirectProblem(window.location.href))

  useEffect(() => {
    // Drop the error from the address so a reload doesn't show it again.
    if (authRedirectProblem(window.location.href)) {
      window.history.replaceState(null, '', window.location.pathname)
    }
  }, [])

  async function start(action: (auth: GoogleAuth, to: string) => Promise<GoogleResult>) {
    setBusy(true)
    setProblem(null)
    let result: GoogleResult
    try {
      result = await action(await loadAuth(), redirectTo)
    } catch (err) {
      result = { kind: 'failed', message: err instanceof Error ? err.message : String(err) }
    }
    // On success the browser is already on its way to Google, so stay busy.
    if (result.kind === 'failed') {
      setProblem(result.message)
      setBusy(false)
    }
  }

  async function signOut() {
    setBusy(true)
    setProblem(null)
    let result: GoogleResult
    try {
      result = await signOutOfAccount(await loadAuth())
    } catch (err) {
      result = { kind: 'failed', message: err instanceof Error ? err.message : String(err) }
    }
    if (result.kind === 'failed') {
      setProblem(result.message)
      setBusy(false)
      return
    }
    // The next load starts a fresh guest account.
    window.location.assign('/')
  }

  return (
    <section className="profile-account" aria-label="Account">
      {account.anonymous ? (
        <>
          <h2>Guest account</h2>
          <p className="profile-muted">
            Your profile and calls are saved in this browser only. Sign in with Google to keep them, and to get them back on another device or after clearing your browser data.
          </p>
          <div className="profile-actions">
            <button type="button" className="profile-btn" disabled={busy} onClick={() => void start(connectGoogle)}>
              {busy ? 'Opening Google…' : 'Continue with Google'}
            </button>
            <button type="button" className="profile-link" disabled={busy} onClick={() => void start(signInWithGoogle)}>
              Sign in to an existing account
            </button>
          </div>
          <small className="profile-muted">Signing in to an existing account leaves this guest's profile and calls behind.</small>
        </>
      ) : (
        <>
          <h2>Account</h2>
          <p className="profile-muted">{account.email ? `Signed in as ${account.email}.` : 'You are signed in.'}</p>
          <div className="profile-actions">
            <button type="button" className="profile-link" disabled={busy} onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </>
      )}
      {problem && <p className="profile-warn" role="alert">{problem}</p>}
    </section>
  )
}
