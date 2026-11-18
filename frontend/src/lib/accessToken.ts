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
 * Who a session belongs to, for change detection: the user id plus whether the session is
 * still anonymous. Converting an anonymous user to a permanent one (a later email sign-in)
 * keeps the id, and the screen must still notice.
 */
function identityOf(session: { user: { id: string; is_anonymous?: boolean } } | null): string | null {
  if (!session) return null
  return `${session.user.id}:${session.user.is_anonymous ? 'anonymous' : 'permanent'}`
}

/**
 * Calls `onChange` when the signed-in user changes (sign-in, sign-out, a different account,
 * or an anonymous user becoming permanent), so a screen loaded while signed out can load
 * again. Resolves to the unsubscribe function, or to a no-op when Supabase isn't configured.
 *
 * It compares the session's identity, not the event name: Supabase also reports
 * `SIGNED_IN` for the same user, for example each time the tab becomes visible again,
 * and that must not reload the screen.
 */
export async function onSignInChange(onChange: () => void): Promise<() => void> {
  try {
    const { supabase } = await import('./supabase')
    // undefined until INITIAL_SESSION (fired right away on subscribing) gives the identity
    // the first load already saw.
    let identity: string | null | undefined
    const { data } = supabase.auth.onAuthStateChange((event, session) => {
      const next = identityOf(session)
      if (event === 'INITIAL_SESSION') {
        identity = next
        return
      }
      if (next === identity) return
      identity = next
      onChange()
    })
    return () => data.subscription.unsubscribe()
  } catch {
    return () => {}
  }
}
