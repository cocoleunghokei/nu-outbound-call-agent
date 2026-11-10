import { useEffect, useState, type FormEvent } from 'react'
import { formatClock } from './format'
import {
  MAX_REPLY_LENGTH,
  PING_WINDOW_MS,
  allowsFreeText,
  closedMessage,
  sanitizeReplyText,
  timeLeft,
  type ReplyResult,
  type SendPingReply,
} from './ping'
import type { PingCardView } from './state'
import type { PingReplyCommand } from './types'

interface PingCardProps {
  view: PingCardView
  sendReply: SendPingReply
}

/** What happened to this tab's reply, before the server's `ping.resolved` arrives. */
type LocalReply = { kind: 'idle' } | { kind: 'sending'; label: string } | ReplyResult

/**
 * The agent's question (DC-8): the question, the plain-language reason (DC-5),
 * 2–4 one-tap replies, a free-text box for decisions, and the reply clock.
 * The server picks the winner of the reply/timeout race; this card only shows it.
 */
export function PingCard({ view, sendReply }: PingCardProps) {
  // Remount per ping so a new question never inherits the old one's draft or result.
  return <PingCardInner key={view.ping.ping_id} view={view} sendReply={sendReply} />
}

function PingCardInner({ view, sendReply }: PingCardProps) {
  const { ping } = view
  const [local, setLocal] = useState<LocalReply>({ kind: 'idle' })
  const [draft, setDraft] = useState('')
  const isOpen = view.state === 'open'
  const busy = local.kind === 'sending' || local.kind === 'accepted'
  const disabled = !isOpen || busy || local.kind === 'closed'

  async function send(reply: PingReplyCommand, label: string) {
    if (disabled) return
    setLocal({ kind: 'sending', label })
    try {
      setLocal(await sendReply(ping.call_id, ping.ping_id, reply))
    } catch {
      setLocal({ kind: 'error', message: 'Could not send your reply. Try again.' })
    }
  }

  function onSubmitText(e: FormEvent) {
    e.preventDefault()
    const text = sanitizeReplyText(draft)
    if (text === '') return
    void send({ text }, text)
  }

  return (
    <section
      className={`lc-ping ${isOpen ? '' : 'closed'}`}
      aria-labelledby={`q-${ping.ping_id}`}
    >
      <div className="lc-ping-top">
        <span>
          {!isOpen
            ? 'Earlier question'
            : ping.kind === 'field_approval'
              ? 'Ask me first'
              : 'Your agent needs you'}
        </span>
      </div>
      {view.replacesEarlier && isOpen && (
        <p className="lc-ping-changed">They changed the offer. This replaces the last question.</p>
      )}
      <h2 className="lc-ping-q" id={`q-${ping.ping_id}`} aria-live="assertive">
        {ping.question}
      </h2>
      {ping.reason && <p className="lc-ping-why">{ping.reason}</p>}
      {isOpen && local.kind !== 'accepted' && <Countdown view={view} />}

      {isOpen && local.kind !== 'accepted' && local.kind !== 'closed' && (
        <>
          <div className="lc-ping-opts">
            {ping.suggested_replies.map((label, index) => (
              <button
                key={`${index}-${label}`}
                type="button"
                disabled={disabled}
                onClick={() => void send({ choice: index }, label)}
              >
                {label}
              </button>
            ))}
          </div>
          {allowsFreeText(ping) && (
            <form className="lc-ping-free" onSubmit={onSubmitText}>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={MAX_REPLY_LENGTH}
                placeholder="Or type your answer"
                aria-label="Type your answer"
                disabled={disabled}
              />
              <button type="submit" disabled={disabled || sanitizeReplyText(draft) === ''}>
                Send
              </button>
            </form>
          )}
          {ping.kind === 'field_approval' && (
            <p className="lc-ping-fb">No reply means nothing is shared.</p>
          )}
        </>
      )}

      <Feedback view={view} local={local} />
    </section>
  )
}

/** Status line under the card: sending, sent, too late, error, or the final outcome. */
function Feedback({ view, local }: { view: PingCardView; local: LocalReply }) {
  if (view.state !== 'open') {
    return (
      <p className="lc-ping-fb" role="status">
        {closedMessage(view.ping.kind, view.state, view.replyText)}
      </p>
    )
  }
  switch (local.kind) {
    case 'sending':
      return (
        <p className="lc-ping-fb" role="status">
          Sending “{local.label}”…
        </p>
      )
    case 'accepted':
      return (
        <p className="lc-ping-fb" role="status">
          Sent. Your agent is telling them now.
        </p>
      )
    case 'closed':
      return (
        <p className="lc-ping-fb warn" role="status">
          Too late: the agent already moved on, so this reply wasn't used.
        </p>
      )
    case 'error':
      return (
        <p className="lc-ping-fb warn" role="alert">
          {local.message}
        </p>
      )
    default:
      return null
  }
}

/** Seconds left and a shrinking bar. Frozen with "Paused" while the call is paused. */
function Countdown({ view }: { view: PingCardView }) {
  const [now, setNow] = useState(() => Date.now())
  const { ms, paused } = timeLeft(view.ping, now)

  useEffect(() => {
    if (paused) return
    const timer = window.setInterval(() => setNow(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [paused])

  const fraction = Math.min(1, ms / PING_WINDOW_MS)
  return (
    <div className="lc-ping-clock">
      <span className="lc-ping-sec">
        {paused ? `Paused · ${formatClock(ms)} left` : `${formatClock(ms)} left`}
      </span>
      <div
        className="lc-ping-track"
        role="progressbar"
        aria-label="Time left to reply"
        aria-valuemin={0}
        aria-valuemax={Math.round(PING_WINDOW_MS / 1000)}
        aria-valuenow={Math.ceil(ms / 1000)}
      >
        <div className="lc-ping-bar" style={{ width: `${fraction * 100}%` }} />
      </div>
    </div>
  )
}
