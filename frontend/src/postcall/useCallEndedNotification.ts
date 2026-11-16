import { useEffect, useRef, useState } from 'react'
import type { CallOutcome } from '../history/types'
import { browserNotifyEnv, callEndedNotice, notifyCallEnded, type CallEndedNotice } from './notification'

/** What the live-call page knows about the end of the call (`LiveCallView.ended`). */
export interface EndedInfo {
  outcome: CallOutcome
}

/**
 * Whether the `call.ended` event is from before the page opened.
 * - `live`: seen after the page opened (or the mock call, which has no stored history)
 * - `historical`: part of the first load of stored events, so the call had already ended
 * - `unknown`: the first load hasn't finished yet, so wait
 */
export type EndedHistory = 'live' | 'historical' | 'unknown'

/**
 * Classifies the end from the `call.ended` event's `seq` and the last `seq` of the first
 * event load (`historicalUpTo`: undefined for the mock call, null while loading).
 */
export function endedHistory(endedSeq: number | undefined, historicalUpTo: number | null | undefined): EndedHistory {
  if (historicalUpTo === undefined) return 'live'
  if (historicalUpTo === null) return 'unknown'
  return endedSeq !== undefined && endedSeq <= historicalUpTo ? 'historical' : 'live'
}

/** How long to wait for the call's name after it ends before using "Your call". */
export const NAME_WAIT_MS = 3_000

/**
 * Decides, render by render, whether this tab should show the call-ended notification
 * (PO-5). Pure apart from its own memory, so it is unit-tested.
 *
 * - Once per call: after one decision it never fires again, even when a re-render passes
 *   a new `ended` object.
 * - Only for an end seen live: an end from the first load of stored events is skipped,
 *   and nothing is decided until that load has finished.
 * - Not with a placeholder title: while the business name is still loading
 *   (`businessName === null`) it waits, unless `nameGaveUp`, then it uses "Your call".
 */
export function createCallEndedTracker() {
  let done = false
  return {
    /** Returns the notice to show now, or null. */
    check(
      callId: string,
      businessName: string | null,
      ended: EndedInfo | null,
      history: EndedHistory,
      nameGaveUp = false,
    ): CallEndedNotice | null {
      if (done || ended === null || history === 'unknown') return null
      if (history === 'historical') {
        done = true // a reload of a call that had already ended
        return null
      }
      if (businessName === null && !nameGaveUp) return null // wait for the real name
      done = true
      return callEndedNotice(callId, businessName ?? '', ended.outcome) // '' -> "Your call"
    },
  }
}

/**
 * Shows the call-ended notification (PO-5) once, when this open tab sees the call end.
 * `businessName` is null while the call's details are still loading; after
 * `NAME_WAIT_MS` past the end it gives up and says "Your call".
 */
export function useCallEndedNotification(
  callId: string,
  businessName: string | null,
  ended: EndedInfo | null,
  history: EndedHistory,
): void {
  const tracker = useRef<ReturnType<typeof createCallEndedTracker> | null>(null)
  const [nameGaveUp, setNameGaveUp] = useState(false)
  const waitingForName = ended !== null && history === 'live' && businessName === null

  useEffect(() => {
    if (!waitingForName) return
    const timer = window.setTimeout(() => setNameGaveUp(true), NAME_WAIT_MS)
    return () => window.clearTimeout(timer)
  }, [waitingForName])

  useEffect(() => {
    tracker.current ??= createCallEndedTracker()
    const notice = tracker.current.check(callId, businessName, ended, history, nameGaveUp)
    if (notice) notifyCallEnded(notice, browserNotifyEnv())
  }, [callId, businessName, ended, history, nameGaveUp])
}
