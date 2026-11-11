import { useSyncExternalStore } from 'react'

/**
 * A small router on the browser's History API (no routing library: four screens don't
 * need one). Screens never touch `window.history` themselves: they call `navigate`, and
 * `App.tsx` turns the current address into a screen with `routeOf`.
 *
 * Addresses:
 * - `/`                    chat home (pre-call)
 * - `/live?call=<call_id>` the live call; without `call`, the scripted demo call
 * - `/history`             past calls
 *
 * Vercel serves `index.html` for every path (`vercel.json`), so a refresh or a shared link
 * on any of these opens the right screen.
 */

export type Route =
  | { name: 'home' }
  | { name: 'live'; callId: string | null }
  | { name: 'history' }

/** The address of a call's live screen. */
export function livePath(callId: string | null): string {
  return callId ? `/live?call=${encodeURIComponent(callId)}` : '/live'
}

/** The screen for an address. Unknown paths go to the chat home. */
export function routeOf(pathname: string, search: string): Route {
  const path = pathname.replace(/\/+$/, '') || '/'
  if (path === '/live') {
    const callId = new URLSearchParams(search).get('call')?.trim()
    return { name: 'live', callId: callId || null }
  }
  if (path === '/history') return { name: 'history' }
  return { name: 'home' }
}

// pushState doesn't fire `popstate`, so `navigate` tells subscribers itself.
const NAVIGATED = 'app:navigated'

/** Go to `to` (a path with an optional query). `replace` swaps the current history entry. */
export function navigate(to: string, { replace = false }: { replace?: boolean } = {}): void {
  if (to === window.location.pathname + window.location.search) return
  if (replace) window.history.replaceState(null, '', to)
  else window.history.pushState(null, '', to)
  window.dispatchEvent(new Event(NAVIGATED))
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange) // Back and Forward
  window.addEventListener(NAVIGATED, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(NAVIGATED, onChange)
  }
}

function currentAddress(): string {
  return window.location.pathname + window.location.search
}

/** The current route; re-renders on `navigate` and on Back / Forward. */
export function useRoute(): Route {
  const address = useSyncExternalStore(subscribe, currentAddress)
  const queryAt = address.indexOf('?')
  return queryAt < 0 ? routeOf(address, '') : routeOf(address.slice(0, queryAt), address.slice(queryAt))
}
