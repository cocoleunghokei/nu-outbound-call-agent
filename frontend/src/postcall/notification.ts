import { outcomeDisplay } from '../history/format'
import type { CallOutcome } from '../history/types'

/**
 * The call-ended notification (PO-5).
 *
 * The same `{ title, body }` is meant for both paths:
 * - tab open: the live-call page shows it itself when `call.ended` arrives (this file);
 * - tab closed: `app/postcall/` puts it in a `notify` outbox row for Steve's notifier
 *   (format still to be agreed with Steve).
 */
export interface CallEndedNotice {
  /** Used to open the right result page on click. Never shown. */
  callId: string
  /** e.g. "Juniper Ramen" */
  title: string
  /** e.g. "Booked. Tap to see the summary." */
  body: string
}

/** Builds the notification text from the business name and the call's outcome. */
export function callEndedNotice(callId: string, businessName: string, outcome: CallOutcome): CallEndedNotice {
  const title = businessName.trim() || 'Your call'
  if (outcome === null) {
    return { callId, title, body: 'The call ended. The summary will be ready soon.' }
  }
  const { label } = outcomeDisplay({ outcome, endedAt: 'ended' })
  return { callId, title, body: `${label}. Tap to see the summary.` }
}

/** Where a click on the notification takes the user: that call's result page. */
export function resultPageUrl(callId: string): string {
  return `/?screen=history&open=${encodeURIComponent(callId)}`
}

/** The parts of the browser this needs, so tests can pass fakes. */
export interface NotifyEnv {
  /** `Notification.permission`, or 'unsupported' if the browser has no Notification API */
  permission: NotificationPermission | 'unsupported'
  /** Shows a notification; `onClick` runs when the user clicks it */
  show: (title: string, options: NotificationOptions, onClick: () => void) => void
  /** Brings this tab to the front and goes to `url` */
  openResult: (url: string) => void
}

/**
 * Shows the call-ended notification if the user has allowed notifications.
 * Never asks for permission: that's the onboarding step (ON-6). Returns
 * whether a notification was shown.
 */
export function notifyCallEnded(notice: CallEndedNotice, env: NotifyEnv): boolean {
  if (env.permission !== 'granted') return false
  env.show(
    notice.title,
    {
      body: notice.body,
      // One notification per call, even if this runs twice.
      tag: `call-ended-${notice.callId}`,
    },
    () => env.openResult(resultPageUrl(notice.callId)),
  )
  return true
}

/** The real browser environment. */
export function browserNotifyEnv(): NotifyEnv {
  const supported = typeof window !== 'undefined' && 'Notification' in window
  return {
    permission: supported ? Notification.permission : 'unsupported',
    show: (title, options, onClick) => {
      const notification = new Notification(title, options)
      notification.onclick = () => {
        notification.close()
        onClick()
      }
    },
    openResult: (url) => {
      window.focus()
      window.location.assign(url)
    },
  }
}
