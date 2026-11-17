/**
 * The signed-in user's Supabase access token, from the shared client in `supabase.ts`
 * (never a second client). The client is imported lazily, so screens on fake data never
 * load it.
 *
 * Resolves to null when nobody is signed in. Rejects when Supabase isn't configured
 * (`supabase.ts` throws at import when its variables are missing); callers that treat
 * that as "signed out" catch it.
 */
export async function accessToken(): Promise<string | null> {
  const { supabase } = await import('./supabase')
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}

/**
 * Calls `onChange` when the user signs in or out, so a screen loaded while signed out can
 * load again. Resolves to the unsubscribe function, or to a no-op when Supabase isn't
 * configured.
 */
export async function onSignInChange(onChange: () => void): Promise<() => void> {
  try {
    const { supabase } = await import('./supabase')
    const { data } = supabase.auth.onAuthStateChange((event) => {
      // INITIAL_SESSION fires right away on subscribing; the first load already covers it.
      if (event === 'SIGNED_IN' || event === 'SIGNED_OUT') onChange()
    })
    return () => data.subscription.unsubscribe()
  } catch {
    return () => {}
  }
}
