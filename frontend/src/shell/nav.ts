import type { Route } from '../router'

/** One entry in the app's sidebar. */
export interface NavItem {
  key: 'chat' | 'calls'
  label: string
  /** The address the entry opens (see `router.ts`). */
  path: string
}

/**
 * The sidebar entries, in order. Only screens that exist are listed; the prototype's
 * Calendar and Privacy & profile get an entry when their screens are built.
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { key: 'chat', label: 'Chat', path: '/' },
  { key: 'calls', label: 'Calls', path: '/history' },
]

/** The sidebar entry to highlight for a route. A live call belongs to Calls. */
export function activeNav(route: Route): NavItem['key'] {
  return route.name === 'home' ? 'chat' : 'calls'
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
