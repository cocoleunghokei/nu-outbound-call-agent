import { useEffect, useMemo, useRef, useState } from 'react'
import { formatClock, formatPhone, initials } from './format'
import { STATUS_STEPS, deriveLiveCall, statusStep, type TranscriptLine } from './state'
import type { CallEvent, LiveCallSummary } from './types'
import './LiveCall.css'

interface LiveCallProps {
  summary: LiveCallSummary
  /** The call's events ordered by `seq` (see `mergeEvents`). */
  events: readonly CallEvent[]
}

/**
 * The live-call page: header with the call clock, status pills (DC-14) and the
 * live transcript (DC-3). Pings, Pause/End and the decision feed come later.
 */
export function LiveCall({ summary, events }: LiveCallProps) {
  const view = useMemo(() => deriveLiveCall(events), [events])

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
            {summary.businessName} on the left, your agent on the right. Transcribed, audio not
            kept.
          </p>
          <Transcript
            lines={view.lines}
            businessName={summary.businessName}
            dialing={statusStep(view.status) === 'dialing'}
            ended={view.ended !== null}
          />
        </section>

        <aside className="lc-side">
          {view.ended === null ? (
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

interface TranscriptProps {
  lines: readonly TranscriptLine[]
  businessName: string
  dialing: boolean
  ended: boolean
}

/** Chat-style transcript that keeps the newest line in view. */
function Transcript({ lines, businessName, dialing, ended }: TranscriptProps) {
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines.length, ended])

  return (
    <div className="lc-convo" ref={scroller} aria-live="polite" aria-relevant="additions">
      {lines.length === 0 && dialing && <div className="lc-note">Dialing…</div>}
      {lines.map((line) =>
        line.role === 'user' ? (
          <div key={line.key} className="lc-note lc-note-you">
            You: <b>{line.text}</b>
          </div>
        ) : (
          <div key={line.key} className={`lc-line ${line.role}`}>
            <span className="lc-name">{line.role === 'agent' ? 'Agent' : businessName}</span>
            <div className="lc-bubble">{line.text}</div>
          </div>
        ),
      )}
      {ended && <div className="lc-note">Call ended</div>}
    </div>
  )
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
