import type { Route } from '../router'

/** One entry in the app's sidebar. */
export interface NavItem {
  key: 'chat' | 'calls' | 'profile'
  label: string
  /** The address the entry opens (see `router.ts`). */
  path: string
}

/**
 * The sidebar entries, in order. Only screens that exist are listed; the prototype's
 * Calendar gets an entry when its screen is built.
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { key: 'chat', label: 'Chat', path: '/' },
  { key: 'calls', label: 'Calls', path: '/history' },
  { key: 'profile', label: 'Profile', path: '/profile' },
]

/** The sidebar entry to highlight for a route. A live call belongs to Calls. */
export function activeNav(route: Route): NavItem['key'] {
  if (route.name === 'home') return 'chat'
  return route.name === 'profile' ? 'profile' : 'calls'
}

/**
 * Whether a click on a sidebar link should be handled in the page. A click with a
 * modifier key or a middle click is left to the browser, so "open in new tab" still works.
 */
export function isPlainClick(event: {
  button: number
  metaKey: boolean
  ctrlKey: boolean
  shiftKey: boolean
  altKey: boolean
}): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}
