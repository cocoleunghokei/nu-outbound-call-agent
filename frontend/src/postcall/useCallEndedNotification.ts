import { useEffect, useRef } from 'react'
import type { CallOutcome } from '../history/types'
import { browserNotifyEnv, callEndedNotice, notifyCallEnded, type CallEndedNotice } from './notification'

/** What the live-call page knows about the end of the call (`LiveCallView.ended`). */
export interface EndedInfo {
  outcome: CallOutcome
  /** `occurred_at` of the `call.ended` event, ISO 8601 */
  endedAt: string
}

/**
 * How long before the page opened a `call.ended` may be and still count as live.
 * Covers clock differences between the server and this browser.
 */
export const CLOCK_SKEW_MS = 5_000

/**
 * Decides, render by render, whether this tab should show the call-ended
 * notification (PO-5). Pure apart from its own memory, so it is unit-tested.
 *
 * - Once per call: after one notification it never fires again, even when a
 *   re-render passes a new `ended` object.
 * - Only for an end seen live: a call whose `call.ended` happened before the
 *   page opened stays quiet, even when the events load after the first render
 *   (real mode loads them after the Realtime subscription).
 * - Not with a placeholder title: while the business name is still loading
 *   (`businessName === null`), it waits and decides on a later render.
 */
export function createCallEndedTracker(mountedAt: number) {
  let done = false
  return {
    /** Returns the notice to show now, or null. */
    check(callId: string, businessName: string | null, ended: EndedInfo | null): CallEndedNotice | null {
      if (done || ended === null) return null
      const endedAt = Date.parse(ended.endedAt)
      if (!Number.isNaN(endedAt) && endedAt < mountedAt - CLOCK_SKEW_MS) {
        done = true // ended before this page opened: a reload, not a live end
        return null
      }
      if (businessName === null) return null // wait for the real name
      done = true
      return callEndedNotice(callId, businessName, ended.outcome)
    },
  }
}

/**
 * Shows the call-ended notification (PO-5) once, when this open tab sees the
 * call end. `businessName` is null while the call's details are still loading.
 */
export function useCallEndedNotification(
  callId: string,
  businessName: string | null,
  ended: EndedInfo | null,
): void {
  const tracker = useRef<ReturnType<typeof createCallEndedTracker> | null>(null)

  useEffect(() => {
    tracker.current ??= createCallEndedTracker(Date.now())
    const notice = tracker.current.check(callId, businessName, ended)
    if (notice) notifyCallEnded(notice, browserNotifyEnv())
  }, [callId, businessName, ended])
}
