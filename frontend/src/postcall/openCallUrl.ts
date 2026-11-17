/**
 * The open call lives in the address (`?open=<call id>`), so the browser's Back
 * button closes a call's result and a result can be linked to (#49).
 *
 * Temporary: once the app has routing, this becomes a path like `/calls/:id`.
 */
import { useEffect, useRef, useState } from 'react'

const PARAM = 'open'

/** Marks a history entry this screen added when opening a call. */
const OPENED_HERE = 'postcall-open-call'

/** The call id in `?open=`, or null. */
export function openCallFrom(search: string): string | null {
  const id = new URLSearchParams(search).get(PARAM)?.trim()
  return id ? id : null
}

/** `href` with `?open=` set to `callId`, or removed when `callId` is null. Other params are kept. */
export function withOpenCall(href: string, callId: string | null): string {
  const url = new URL(href)
  if (callId === null) url.searchParams.delete(PARAM)
  else url.searchParams.set(PARAM, callId)
  return url.pathname + url.search + url.hash
}

/** Whether the current history entry was added by `open` (so closing can go Back). */
export function openedHere(state: unknown): boolean {
  return typeof state === 'object' && state !== null && (state as { from?: unknown }).from === OPENED_HERE
}

/** The parts of `window.history` and `window.location` the navigator uses, so tests can pass fakes. */
export interface BrowserHistory {
  readonly state: unknown
  back(): void
  pushState(state: unknown, unused: string, url: string): void
  replaceState(state: unknown, unused: string, url: string): void
}

export interface OpenCallNavigator {
  /** Open a call: adds a history entry. */
  open(callId: string): void
  /** Close the call: Back if this screen opened it, else remove `?open=`. Repeats while Back is pending are ignored. */
  close(): void
  /** Call on `popstate` with the new `location.search`. */
  popped(search: string): void
}

/**
 * Keeps the open call in step with the address. `onChange` receives the open
 * call id (or null) whenever it changes.
 */
export function createOpenCallNavigator(
  history: BrowserHistory,
  currentHref: () => string,
  onChange: (callId: string | null) => void,
): OpenCallNavigator {
  let backPending = false
  return {
    open(callId) {
      history.pushState({ from: OPENED_HERE }, '', withOpenCall(currentHref(), callId))
      onChange(callId)
    },
    close() {
      if (backPending) return // a double click on "All calls" must not go Back twice
      if (openedHere(history.state)) {
        backPending = true
        onChange(null)
        history.back()
      } else {
        history.replaceState(null, '', withOpenCall(currentHref(), null))
        onChange(null)
      }
    },
    popped(search) {
      backPending = false
      onChange(openCallFrom(search))
    },
  }
}

/**
 * The open call id, kept in step with the address and the Back / Forward buttons.
 * Returns the id and a setter: a call id opens it, null closes it.
 */
export function useOpenCall(): [string | null, (callId: string | null) => void] {
  const [openCallId, setOpenCallId] = useState(() => openCallFrom(window.location.search))
  const navigator = useRef<OpenCallNavigator | null>(null)
  if (navigator.current === null) {
    navigator.current = createOpenCallNavigator(window.history, () => window.location.href, setOpenCallId)
  }

  useEffect(() => {
    const onPop = () => navigator.current?.popped(window.location.search)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const setOpenCall = (callId: string | null) => {
    if (callId === null) navigator.current?.close()
    else navigator.current?.open(callId)
  }
  return [openCallId, setOpenCall]
}
