import { useEffect, useState } from 'react'
import {
  MAX_INSTRUCTION_LENGTH,
  PAUSE_CAP_MS,
  availableControls,
  type CommandResult,
  type ControlCommand,
  type SendCallCommand,
} from './controls'
import { formatClock } from './format'
import { sanitizeReplyText } from './ping'
import type { CallStatus } from './types'

interface CallControlsProps {
  callId: string
  status: CallStatus
  ended: boolean
  /** When the current pause started, or null. */
  pausedAt: string | null
  sendCommand: SendCallCommand
}

type Sending = { kind: 'idle' } | { kind: 'sending'; command: ControlCommand } | CommandResult

/**
 * Pause / Resume and End call (DC-11). End asks for a second tap so a stray
 * click can't hang up on the business.
 */
export function CallControls({ callId, status, ended, pausedAt, sendCommand }: CallControlsProps) {
  const [sending, setSending] = useState<Sending>({ kind: 'idle' })
  const [confirmEnd, setConfirmEnd] = useState(false)
  const [draft, setDraft] = useState('')
  const allowed = availableControls(status, ended)
  const busy = sending.kind === 'sending'

  if (ended) return null

  async function send(command: ControlCommand, text?: string) {
    if (busy) return
    setSending({ kind: 'sending', command })
    try {
      const result = await sendCommand(callId, command, text)
      setSending(result)
      // Keep the typed text when it didn't go through, so it can be sent again.
      if (command === 'instruction' && result.kind === 'accepted') setDraft('')
    } catch {
      setSending({ kind: 'error', message: 'Could not reach the call. Try again.' })
    }
    if (command === 'end') setConfirmEnd(false)
  }

  const instruction = sanitizeReplyText(draft)

  return (
    <section className="lc-controls" aria-label="Call controls">
      {pausedAt !== null && <PausedBanner pausedAt={pausedAt} />}

      <form
        className="lc-instruct"
        onSubmit={(event) => {
          event.preventDefault()
          if (instruction !== '' && allowed.instruct) void send('instruction', instruction)
        }}
      >
        <label htmlFor="lc-instruction">Tell your agent something</label>
        <div className="lc-instruct-row">
          <input
            id="lc-instruction"
            type="text"
            value={draft}
            maxLength={MAX_INSTRUCTION_LENGTH}
            placeholder="e.g. ask if they have Saturday"
            autoComplete="off"
            disabled={!allowed.instruct}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button
            type="submit"
            className="lc-btn primary"
            disabled={busy || !allowed.instruct || instruction === ''}
            title={allowed.instruct ? undefined : 'You can send an instruction once they pick up'}
          >
            Send
          </button>
        </div>
        <p className="lc-instruct-hint">
          The agent works it in at its next natural turn. It never overrides your rules.
        </p>
      </form>

      {confirmEnd ? (
        <div className="lc-confirm" role="group" aria-label="Confirm ending the call">
          <p>End the call? The agent will say a polite goodbye and hang up.</p>
          <div className="lc-buttons">
            <button type="button" className="lc-btn danger" disabled={busy} onClick={() => void send('end')}>
              Yes, end call
            </button>
            <button type="button" className="lc-btn" disabled={busy} onClick={() => setConfirmEnd(false)}>
              Keep going
            </button>
          </div>
        </div>
      ) : (
        <div className="lc-buttons">
          {allowed.resume ? (
            <button type="button" className="lc-btn primary" disabled={busy} onClick={() => void send('resume')}>
              Resume
            </button>
          ) : (
            <button
              type="button"
              className="lc-btn"
              disabled={busy || !allowed.pause}
              title={allowed.pause ? undefined : 'You can pause once they pick up'}
              onClick={() => void send('pause')}
            >
              Pause
            </button>
          )}
          <button
            type="button"
            className="lc-btn danger"
            disabled={busy || !allowed.end}
            onClick={() => setConfirmEnd(true)}
          >
            End call
          </button>
        </div>
      )}

      {sending.kind === 'sending' && (
        <p className="lc-controls-fb" role="status">
          {sending.command === 'instruction'
            ? 'Sending…'
            : sending.command === 'pause'
              ? 'Pausing…'
              : sending.command === 'resume'
                ? 'Resuming…'
                : 'Ending…'}
        </p>
      )}
      {sending.kind === 'error' && (
        <p className="lc-controls-fb warn" role="alert">
          {sending.message}
        </p>
      )}
    </section>
  )
}

/** "Paused 0:32": how long the business has been holding, and what happens at the cap. */
function PausedBanner({ pausedAt }: { pausedAt: string }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const start = Date.parse(pausedAt)
  const elapsed = Number.isNaN(start) ? 0 : now - start
  return (
    <div className="lc-paused" role="status">
      <b>Paused · {formatClock(elapsed)}</b>
      <span>
        The business is on hold and hears a short reassurance every 15 seconds. After{' '}
        {Math.round(PAUSE_CAP_MS / 60_000)} minutes the agent offers a call-back and ends the call.
      </span>
    </div>
  )
}
