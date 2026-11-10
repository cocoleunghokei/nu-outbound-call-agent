import { useEffect, useMemo, useRef, useState } from 'react'
import { CallControls } from './CallControls'
import { commandText, decisionLabel, type SendCallCommand } from './controls'
import { formatClock, formatPhone, initials } from './format'
import { PingCard } from './PingCard'
import type { SendPingReply } from './ping'
import { STATUS_STEPS, deriveLiveCall, statusStep, type FeedItem } from './state'
import type { CallEvent, LiveCallSummary } from './types'
import './LiveCall.css'

interface LiveCallProps {
  summary: LiveCallSummary
  /** The call's events ordered by `seq` (see `mergeEvents`). */
  events: readonly CallEvent[]
  /** Sends the user's answer to a ping (mock now, backend API later). */
  sendReply: SendPingReply
  /** Sends Pause / Resume / End (mock now, backend API later). */
  sendCommand: SendCallCommand
}

/**
 * The live-call page: header with the call clock, status pills (DC-14), the
 * live feed of transcript and decisions (DC-3, DC-4, DC-5), the ping card
 * (DC-8) and Pause / Resume / End (DC-11).
 */
export function LiveCall({ summary, events, sendReply, sendCommand }: LiveCallProps) {
  const view = useMemo(() => deriveLiveCall(events), [events])
  const side = useRef<HTMLElement>(null)
  const openPingId = view.ping?.state === 'open' ? view.ping.ping.ping_id : null

  // On a phone the card sits below the transcript, so bring a new question into view.
  useEffect(() => {
    if (openPingId === null || !window.matchMedia('(max-width: 760px)').matches) return
    side.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [openPingId])

  return (
    <main className="live-call">
      <header className="lc-head">
        <span className="lc-avatar" aria-hidden="true">
          {initials(summary.businessName)}
        </span>
        <div className="lc-title">
          <h1>{summary.businessName}</h1>
          <p className="lc-meta">
            {formatPhone(summary.phoneE164)} · {summary.goal}
          </p>
        </div>
        <CallClock
          startedAt={summary.createdAt}
          endedAt={view.ended?.endedAt ?? null}
          maxDurationS={summary.maxDurationS}
        />
      </header>

      <StatusPills status={view.status} />

      <div className="lc-grid">
        <section className="lc-card lc-conversation" aria-labelledby="lc-conv-title">
          <h2 id="lc-conv-title">Live conversation</h2>
          <p className="lc-hint">
            {summary.businessName} on the left, your agent on the right, with what the agent
            decided in between. Transcribed, audio not kept.
          </p>
          <Feed
            items={view.feed}
            businessName={summary.businessName}
            dialing={statusStep(view.status) === 'dialing'}
            ended={view.ended !== null}
          />
        </section>

        <aside className="lc-side" ref={side}>
          {view.ping !== null && <PingCard view={view.ping} sendReply={sendReply} />}
          {view.ping?.state === 'open' ? null : view.ended === null ? (
            <div className="lc-idle">
              <b>Your agent is handling it</b>
              <span>
                If it needs you, the question shows up right here. The line stays open while you
                answer.
              </span>
            </div>
          ) : (
            <div className="lc-idle">
              <b>Call ended</b>
              <span>The summary will show up here once it's ready.</span>
            </div>
          )}
          <CallControls
            callId={summary.callId}
            status={view.status}
            ended={view.ended !== null}
            pausedAt={view.pausedAt}
            sendCommand={sendCommand}
          />
        </aside>
      </div>
    </main>
  )
}

/** The row of status pills; the current one is highlighted. */
function StatusPills({ status }: { status: string }) {
  const current = statusStep(status)
  return (
    <ol className="lc-states" aria-label="Call status">
      {STATUS_STEPS.map(({ step, label }) => (
        <li
          key={step}
          className={[
            current === step ? 'on' : '',
            current === step && step === 'waiting_on_you' ? 'wait' : '',
            // IVR navigation is P2 (DC-2), so this state is dimmed for now.
            step === 'in_phone_menu' ? 'later' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          aria-current={current === step ? 'step' : undefined}
        >
          {label}
        </li>
      ))}
      {current === 'unknown' && (
        <li className="on" aria-current="step">
          Unknown
        </li>
      )}
    </ol>
  )
}

interface FeedProps {
  items: readonly FeedItem[]
  businessName: string
  dialing: boolean
  ended: boolean
}

/** Chat-style feed: transcript bubbles, with decisions and your actions as notes. Keeps the newest in view. */
function Feed({ items, businessName, dialing, ended }: FeedProps) {
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items.length, ended])

  return (
    <div className="lc-convo" ref={scroller} aria-live="polite" aria-relevant="additions">
      {items.length === 0 && dialing && <div className="lc-note">Dialing…</div>}
      {items.map((item) => (
        <FeedEntry key={item.key} item={item} businessName={businessName} />
      ))}
      {ended && <div className="lc-note">Call ended</div>}
    </div>
  )
}

function FeedEntry({ item, businessName }: { item: FeedItem; businessName: string }) {
  switch (item.kind) {
    case 'line':
      return item.role === 'user' ? (
        <div className="lc-note lc-note-you">
          You: <b>{item.text}</b>
        </div>
      ) : (
        <div className={`lc-line ${item.role}`}>
          <span className="lc-name">{item.role === 'agent' ? 'Agent' : businessName}</span>
          <div className="lc-bubble">{item.text}</div>
        </div>
      )
    case 'decision':
      // The reason is the main content; the score is never shown (DC-5).
      return (
        <div className="lc-note lc-decision">
          <span className="lc-badge acted">{decisionLabel(item.action)}</span>
          {item.situation && <b>{item.situation}. </b>}
          {item.reason}
          {item.missedPreference && (
            <span className="lc-flag">
              <span className="lc-badge flag">Missed preference</span>
              {item.missedPreference}
            </span>
          )}
        </div>
      )
    case 'asked':
      return (
        <div className="lc-note">
          <span className="lc-badge asked">Asked you</span>
          <b>{item.question}. </b>
          {item.reason}
        </div>
      )
    case 'command':
      return <div className="lc-note lc-note-you">{commandText(item.command)}</div>
  }
}

interface CallClockProps {
  startedAt: string
  endedAt: string | null
  maxDurationS: number
}

/** Elapsed call time, ticking every second until the call ends. */
function CallClock({ startedAt, endedAt, maxDurationS }: CallClockProps) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (endedAt !== null) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [endedAt])

  const start = Date.parse(startedAt)
  const end = endedAt !== null ? Date.parse(endedAt) : now
  const elapsed = Number.isNaN(start) || Number.isNaN(end) ? 0 : end - start

  return (
    <div className="lc-clock">
      <b>{formatClock(elapsed)}</b>
      <small>of {formatClock(maxDurationS * 1000)} max</small>
    </div>
  )
}
