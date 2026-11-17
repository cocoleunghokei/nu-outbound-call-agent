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
 * Calls `onChange` when the signed-in user changes (sign-in, sign-out or a different
 * account), so a screen loaded while signed out can load again. Resolves to the
 * unsubscribe function, or to a no-op when Supabase isn't configured.
 *
 * It compares the session's user id, not the event name: Supabase also reports
 * `SIGNED_IN` for the same user, for example each time the tab becomes visible again,
 * and that must not reload the screen.
 */
export async function onSignInChange(onChange: () => void): Promise<() => void> {
  try {
    const { supabase } = await import('./supabase')
    // undefined until INITIAL_SESSION (fired right away on subscribing) gives the user
    // the first load already saw.
    let userId: string | null | undefined
    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      const next = session?.user.id ?? null
      if (event === 'INITIAL_SESSION') {
        userId = next
        return
      }
      if (next === userId) return
      userId = next
      onChange()
    })
    return () => data.subscription.unsubscribe()
  } catch {
    return () => {}
  }
}
