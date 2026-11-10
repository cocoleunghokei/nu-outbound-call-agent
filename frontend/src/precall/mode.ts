/**
 * Demo mode or API mode (plan Step 11).
 *
 * API mode needs both a FastAPI URL (`VITE_API_URL`) and a signed-in Supabase
 * session (sign-in is ON-1, P1, not built yet). Without either, the app runs
 * fully on fake data, as before, and Start only logs the brief.
 *
 * The Supabase client is loaded lazily, only when `VITE_API_URL` is set, so
 * demo mode works even without the Supabase variables (`lib/supabase.ts`
 * throws at import when they are missing).
 */
import { createApiClient, normaliseBaseUrl } from './api'
import { apiStarter, demoStarter, type Starter } from './start'

export type DemoReason = 'no_api_url' | 'no_supabase' | 'no_session'

export type DataMode = { kind: 'demo'; reason: DemoReason; starter: Starter } | { kind: 'api'; starter: Starter }

/** Returns the current access token, or null when signed out. */
export type TokenSource = () => Promise<string | null>

/** Loads the shared Supabase client's token source. Rejects when Supabase isn't configured. */
export type LoadTokenSource = () => Promise<TokenSource>

/** The token source backed by the shared client in `lib/supabase.ts` (never a second client). */
export const loadSupabaseTokenSource: LoadTokenSource = async () => {
  const { supabase } = await import('../lib/supabase')
  return async () => {
    const { data } = await supabase.auth.getSession()
    return data.session?.access_token ?? null
  }
}

/** Short note for the top bar in demo mode; null in API mode. */
export function modeNote(mode: DataMode | null): { text: string; title: string } | null {
  if (!mode || mode.kind === 'api') return null
  const why: Record<DemoReason, string> = {
    no_api_url: 'VITE_API_URL is not set, so nothing is saved and no call is placed.',
    no_supabase: 'Supabase is not configured, so nothing is saved and no call is placed.',
    no_session: 'Not signed in, so nothing is saved and no call is placed.',
  }
  return { text: 'Demo mode (fake data)', title: why[mode.reason] }
}

/** Pick the mode from the API URL and whether someone is signed in right now. */
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
  } catch {
    return { kind: 'demo', reason: 'no_supabase', starter: demoStarter }
  }
  const token = await getToken().catch(() => null)
  if (!token) return { kind: 'demo', reason: 'no_session', starter: demoStarter }

  const client = createApiClient({ baseUrl, getToken, fetch: fetchImpl })
  return { kind: 'api', starter: apiStarter(client) }
}
