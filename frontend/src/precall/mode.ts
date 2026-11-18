/**
 * Demo mode or API mode (plan Step 11).
 *
 * API mode needs both a FastAPI URL (`VITE_API_URL`) and a Supabase session. There is no
 * sign-in screen yet (ON-1, P1), so the app signs the browser in anonymously on first use
 * (`lib/session.ts`): the profile and calls then belong to this browser until an email is
 * linked later. Without a URL, without Supabase, or when that sign-in fails, the app runs
 * fully on fake data, as before, and Start only logs the brief.
 *
 * The Supabase client is loaded lazily, only when `VITE_API_URL` is set, so demo mode works
 * even without the Supabase variables (`lib/supabase.ts` throws at import when they are
 * missing).
 */
import { accessToken } from '../lib/accessToken'
import { ensureSession, SignInFailedError } from '../lib/session'
import { createApiClient, normaliseBaseUrl } from './api'
import { apiStarter, demoStarter, type Starter } from './start'

export type DemoReason = 'no_api_url' | 'no_supabase' | 'sign_in_failed' | 'no_session'

export type DataMode = { kind: 'demo'; reason: DemoReason; starter: Starter } | { kind: 'api'; starter: Starter }

/** Returns the current access token, or null when signed out. */
export type TokenSource = () => Promise<string | null>

/**
 * Makes sure a session exists, then returns its token source. Rejects with
 * `SignInFailedError` when the anonymous sign-in fails, and with anything else when
 * Supabase isn't configured.
 */
export type LoadTokenSource = () => Promise<TokenSource>

/** The token source backed by the shared client in `lib/supabase.ts` (never a second client). */
export const loadSupabaseTokenSource: LoadTokenSource = async () => {
  const { supabase } = await import('../lib/supabase') // rejects here when Supabase isn't configured
  const result = await ensureSession(supabase.auth)
  if (result.kind === 'failed') throw new SignInFailedError(result.message)
  return accessToken
}

/** Small top-bar note: why the app is in demo mode, or, in API mode, where the data lives. */
export function modeNote(mode: DataMode | null): { text: string; title: string } | null {
  if (!mode) return null
  if (mode.kind === 'api') {
    return {
      text: 'This browser only',
      title: 'Your profile and calls stay in this browser until sign-in exists.',
    }
  }
  const why: Record<DemoReason, string> = {
    no_api_url: 'VITE_API_URL is not set, so nothing is saved and no call is placed.',
    no_supabase: 'Supabase is not configured, so nothing is saved and no call is placed.',
    sign_in_failed: "Couldn't start a session, so nothing is saved and no call is placed. Reload to try again.",
    no_session: 'Not signed in, so nothing is saved and no call is placed.',
  }
  const text = mode.reason === 'sign_in_failed' ? "Demo mode (couldn't sign in)" : 'Demo mode (fake data)'
  return { text, title: why[mode.reason] }
}

/** Pick the mode from the API URL and whether a session could be had right now. */
export async function resolveDataMode(
  apiUrl: string | undefined,
  loadTokenSource: LoadTokenSource = loadSupabaseTokenSource,
  fetchImpl?: typeof fetch,
): Promise<DataMode> {
  const baseUrl = normaliseBaseUrl(apiUrl)
  if (!baseUrl) return { kind: 'demo', reason: 'no_api_url', starter: demoStarter }

  let getToken: TokenSource
  try {
    getToken = await loadTokenSource()
  } catch (err) {
    const reason: DemoReason = err instanceof SignInFailedError ? 'sign_in_failed' : 'no_supabase'
    return { kind: 'demo', reason, starter: demoStarter }
  }
  const token = await getToken().catch(() => null)
  if (!token) return { kind: 'demo', reason: 'no_session', starter: demoStarter }

  const client = createApiClient({ baseUrl, getToken, fetch: fetchImpl })
  return { kind: 'api', starter: apiStarter(client) }
}
