import { useEffect, useRef } from 'react'
import type { CallOutcome } from '../history/types'
import { browserNotifyEnv, callEndedNotice, notifyCallEnded } from './notification'

/**
 * Shows the call-ended notification (PO-5) once, when this open tab sees the
 * call end. A call that had already ended when the page loaded (for example,
 * after a reload) does not notify again.
 */
export function useCallEndedNotification(
  callId: string,
  businessName: string,
  ended: { outcome: CallOutcome } | null,
): void {
  const endedOnLoad = useRef(ended !== null)
  const notified = useRef(false)

  useEffect(() => {
    if (ended === null || endedOnLoad.current || notified.current) return
    notified.current = true
    notifyCallEnded(callEndedNotice(callId, businessName, ended.outcome), browserNotifyEnv())
  }, [callId, businessName, ended])
}
