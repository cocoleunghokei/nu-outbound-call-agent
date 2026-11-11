/**
 * The open call lives in the address (`?open=<call id>`), so the browser's Back
 * button closes a call's result and a result can be linked to (#49).
 *
 * Temporary: once the app has routing, this becomes a path like `/calls/:id`.
 */
import { useCallback, useEffect, useState } from 'react'

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

/** Whether the current history entry was added by `openCall` (so closing can go Back). */
export function openedHere(state: unknown): boolean {
  return typeof state === 'object' && state !== null && (state as { from?: unknown }).from === OPENED_HERE
}

/**
 * The open call id, kept in step with the address and the Back / Forward buttons.
 * `openCall(id)` adds a history entry; `openCall(null)` goes Back if this screen
 * opened the call, else just removes `?open=` (for a call opened from a link).
 */
export function useOpenCall(): [string | null, (callId: string | null) => void] {
  const [openCallId, setOpenCallId] = useState(() => openCallFrom(window.location.search))

  useEffect(() => {
    const onPop = () => setOpenCallId(openCallFrom(window.location.search))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const openCall = useCallback((callId: string | null) => {
    if (callId !== null) {
      window.history.pushState({ from: OPENED_HERE }, '', withOpenCall(window.location.href, callId))
      setOpenCallId(callId)
    } else if (openedHere(window.history.state)) {
      window.history.back() // popstate then clears the open call
    } else {
      window.history.replaceState(null, '', withOpenCall(window.location.href, null))
      setOpenCallId(null)
    }
  }, [])

  return [openCallId, openCall]
}
