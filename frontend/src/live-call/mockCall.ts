/**
 * A scripted fake call, so the live-call screen can be built before the
 * backend is ready. It plays the PRD's Dr. Lee reschedule scenario as the same
 * `call_events` rows Supabase Realtime will deliver. It answers ping replies
 * the way `POST /calls/{call_id}/pings/{ping_id}/reply` will (including
 * `409 ping_closed` after the timeout), and Pause / Resume / End the way
 * `POST /calls/{call_id}/commands` will. All names and numbers are fake.
 *
 * To connect the real backend, replace `useMockCall` with a hook that loads the
 * call's events, subscribes to new ones and posts replies and commands; the
 * screen stays the same.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { PAUSE_CAP_MS, type CommandResult, type SendCallCommand } from './controls'
import { PING_WINDOW_MS, sanitizeReplyText, type ReplyResult, type SendPingReply } from './ping'
import type { CallEvent, LiveCallSummary, PingKind, PingReplyCommand } from './types'

export const MOCK_CALL_ID = '00000000-0000-4000-8000-000000000001'

/** Header data for the fake call, started at `createdAt` (ISO 8601). */
export function mockSummary(createdAt: string): LiveCallSummary {
  return {
    callId: MOCK_CALL_ID,
    businessName: 'Brookline Family Medicine',
    phoneE164: '+16175550142',
    goal: 'Move my Thursday appointment with Dr. Lee to before 3pm',
    createdAt,
    maxDurationS: 900,
  }
}

/** How a ping was settled: a suggested reply (by index), free text, or the timeout. */
export type MockAnswer =
  | { kind: 'choice'; index: number }
  | { kind: 'text'; text: string }
  | { kind: 'timeout' }

/** Wait `delayMs`, then emit an event. */
export interface EmitStep {
  delayMs: number
  type: string
  payload: Record<string, unknown>
}

/** Wait `delayMs`, then ask the user and pause the script until they answer or time runs out. */
export interface AskStep {
  delayMs: number
  ask: { kind: PingKind; field_key?: string; question: string; reason: string; replies: string[] }
  /** The rest of the call, depending on the answer. */
  then: (answer: MockAnswer) => ScriptStep[]
}

export type ScriptStep = EmitStep | AskStep

const status = (delayMs: number, value: string): EmitStep => ({
  delayMs,
  type: 'status.changed',
  payload: { status: value },
})

const line = (delayMs: number, role: 'agent' | 'business', text: string): EmitStep => ({
  delayMs,
  type: 'transcript.final',
  payload: { role, text, source_start_ts: null },
})

const decision = (
  delayMs: number,
  payload: { action: string; situation: string; reason: string; missed_preference?: string; field_key?: string },
): EmitStep => ({ delayMs, type: 'agent.decision', payload })

const ended = (delayMs: number, outcome: string): EmitStep[] => [
  status(delayMs, 'ended'),
  { delayMs: 100, type: 'call.ended', payload: { outcome, ended_reason: 'agent_hung_up' } },
]

function confirmSteps(when: string): ScriptStep[] {
  return [
    line(
      1700,
      'business',
      `Done. Thursday's appointment is cancelled and ${when} is confirmed. The confirmation number is 4821.`,
    ),
    line(900, 'agent', 'Thank you, Dana. Have a good day.'),
    ...ended(1200, 'rescheduled'),
  ]
}

function fridaySteps(opening: string): ScriptStep[] {
  return [
    line(800, 'agent', opening),
    line(1800, 'business', 'Let me look. Friday at 10am is open, as a video visit.'),
    // Medium (DC-4): the fallback matches, but the in-person preference is missed, so it is flagged.
    decision(400, {
      action: 'take_fallback',
      situation: 'They offered Fri 10am as a video visit',
      reason: 'Matched your fallback: Fri morning.',
      missed_preference: 'Video visit, not in person',
    }),
    line(300, 'agent', 'Friday at 10am works. Please book it.'),
    ...confirmSteps('Friday at 10am'),
  ]
}

const OFFER_STEP: AskStep = {
  delayMs: 300,
  ask: {
    kind: 'decision',
    question: 'They offered Thu 4pm',
    reason: 'You said before 3pm.',
    replies: ['Ask for Fri morning', 'Accept 4pm', 'Decline'],
  },
  then: (answer) => {
    if (answer.kind === 'choice' && answer.index === 1) {
      return [
        decision(300, {
          action: 'accept',
          situation: 'They offered Thu 4pm',
          reason: 'You approved Thu 4pm, even though it’s after 3pm.',
        }),
        line(300, 'agent', 'Thursday at 4pm works. Please book it.'),
        ...confirmSteps('Thursday at 4pm'),
      ]
    }
    if (answer.kind === 'choice' && answer.index === 2) {
      return [
        decision(300, {
          action: 'end_call',
          situation: 'They only had Thu 4pm',
          reason: 'You declined, and nothing else matched.',
        }),
        line(300, 'agent', "That time won't work for Alex. I'll confirm with Alex and call back. Thank you, Dana."),
        ...ended(1200, 'needs_follow_up'),
      ]
    }
    if (answer.kind === 'timeout') {
      // No reply: the brief's fallback (Fri morning) matches, so the agent uses it (DC-9).
      return fridaySteps("Thanks for holding. 4pm won't work. Do you have anything Friday morning?")
    }
    return fridaySteps("Thanks for waiting. Alex can't make 4pm. Do you have anything Friday morning?")
  },
}

const DOB_STEP: AskStep = {
  delayMs: 300,
  ask: {
    kind: 'field_approval',
    field_key: 'date_of_birth',
    question: 'They’re asking for your date of birth',
    reason: 'You set date of birth to Ask me first.',
    replies: ['Share it', 'Don’t share'],
  },
  then: (answer) => [
    ...(answer.kind === 'choice' && answer.index === 0
      ? [
          decision(300, {
            action: 'share_field',
            field_key: 'date_of_birth',
            situation: 'They asked for your date of birth',
            reason: 'You approved it just now.',
          }),
          line(300, 'agent', 'Thanks for holding. The date of birth is March 4th, 2004.'),
        ]
      : [line(600, 'agent', 'Thanks for holding. Alex will share that at the visit.')]),
    line(1800, 'business', 'Thanks. The earliest I have with Dr. Lee is Thursday at 4pm.'),
    line(900, 'agent', 'Let me check with Alex, one moment.'),
    OFFER_STEP,
  ],
}

/** The Dr. Lee scenario: one Ask-me-first ping (DC-6), then an offer that breaks a constraint (DC-4). */
export const DR_LEE_SCRIPT: readonly ScriptStep[] = [
  status(0, 'dialing'),
  status(1500, 'talking'),
  line(700, 'business', 'Brookline Family Medicine, this is Dana.'),
  line(
    1300,
    'agent',
    "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. I'd like to move Alex's Thursday appointment with Dr. Lee to an earlier time.",
  ),
  line(2000, 'business', "Sure. Can I get the patient's date of birth?"),
  line(1000, 'agent', 'Let me check with Alex, one moment.'),
  DOB_STEP,
]

/** Timer functions, injectable so tests can use fake timers. */
export interface MockClock {
  now: () => number
  setTimeout: (fn: () => void, ms: number) => unknown
  clearTimeout: (handle: unknown) => void
}

const realClock: MockClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (handle) => window.clearTimeout(handle as number),
}

/** Timings for the fake call; tests shorten them. */
export interface MockCallOptions {
  clock?: MockClock
  /** Reply window for a ping (DC-7). */
  pingWindowMs?: number
  /** How often the agent reassures the business while paused (DC-11). */
  reassureMs?: number
  /** How long a pause may last before the agent offers a call-back and ends (DC-11). */
  pauseCapMs?: number
}

interface OpenAsk {
  pingId: string
  step: AskStep
  timer: unknown
  /** Set while the clock runs. */
  expiresAtMs: number | null
  /** Set while paused (the clock is frozen). */
  remainingMs: number | null
}

interface Pause {
  reassureTimer: unknown
  capTimer: unknown
}

/**
 * Plays a script, one step at a time, and acts as the backend for ping
 * replies and Pause / Resume / End. Events are numbered from 1 with no gaps,
 * like `call_events.seq`.
 */
export class MockCallEngine {
  private seq = 0
  private pingCount = 0
  private queue: ScriptStep[] = []
  private pending: { step: ScriptStep; timer: unknown } | null = null
  private open: OpenAsk | null = null
  private pause: Pause | null = null
  private status = 'creating'
  private ended = false
  private stopped = false
  private readonly callId: string
  private readonly onEvent: (event: CallEvent) => void
  private readonly clock: MockClock
  private readonly pingWindowMs: number
  private readonly reassureMs: number
  private readonly pauseCapMs: number

  constructor(callId: string, onEvent: (event: CallEvent) => void, options: MockCallOptions = {}) {
    this.callId = callId
    this.onEvent = onEvent
    this.clock = options.clock ?? realClock
    this.pingWindowMs = options.pingWindowMs ?? PING_WINDOW_MS
    this.reassureMs = options.reassureMs ?? 15_000
    this.pauseCapMs = options.pauseCapMs ?? PAUSE_CAP_MS
  }

  /** Starts playing `script` from the top. */
  start(script: readonly ScriptStep[]): void {
    this.queue = [...script]
    this.next()
  }

  /** Stops all timers; no more events are emitted. */
  stop(): void {
    this.stopped = true
    this.clearTimers()
  }

  /** Answers a reply like the backend: accepted only while that ping is still open. */
  reply: SendPingReply = async (callId, pingId, reply) => {
    const open = this.open
    if (this.stopped || callId !== this.callId || open === null || open.pingId !== pingId) {
      return { kind: 'closed' }
    }
    const answer = toAnswer(reply, open.step.ask.replies)
    if (answer === null) return { kind: 'error', message: 'That reply is not valid.' }
    const replyText =
      answer.kind === 'choice'
        ? open.step.ask.replies[answer.index]
        : answer.kind === 'text'
          ? answer.text
          : undefined
    this.settle(open, 'answered', answer, replyText)
    return { kind: 'accepted' } satisfies ReplyResult
  }

  /** Handles Pause / Resume / End like `POST /calls/{call_id}/commands`. */
  command: SendCallCommand = async (callId, command) => {
    if (this.stopped || callId !== this.callId) {
      return { kind: 'error', message: 'This call is not running.' }
    }
    if (command === 'end') {
      this.endByUser()
      return { kind: 'accepted' }
    }
    if (this.ended) return { kind: 'error', message: 'The call has already ended.' }
    if (command === 'pause') return this.pauseCall()
    return this.resumeCall()
  }

  private pauseCall(): CommandResult {
    if (this.pause !== null) return { kind: 'accepted' }
    if (this.status !== 'talking' && this.status !== 'waiting_on_you') {
      return { kind: 'error', message: 'You can pause once they pick up.' }
    }
    // Hold the next scripted line until Resume.
    if (this.pending !== null) {
      this.clock.clearTimeout(this.pending.timer)
      this.queue.unshift(this.pending.step)
      this.pending = null
    }
    // Freeze an open ping's clock (expires_at null, remaining_ms kept).
    const open = this.open
    if (open !== null && open.expiresAtMs !== null) {
      this.clock.clearTimeout(open.timer)
      open.remainingMs = Math.max(0, open.expiresAtMs - this.clock.now())
      open.expiresAtMs = null
      this.emitPing(open)
    }
    this.emit('command.applied', { command: 'pause' })
    this.emit('status.changed', { status: 'on_hold' })
    this.emit('transcript.final', { role: 'agent', text: 'One moment please.', source_start_ts: null })
    const reassure = () => {
      this.emit('transcript.final', {
        role: 'agent',
        text: 'Thanks for holding, just a moment longer.',
        source_start_ts: null,
      })
      if (this.pause !== null) this.pause.reassureTimer = this.clock.setTimeout(reassure, this.reassureMs)
    }
    this.pause = {
      reassureTimer: this.clock.setTimeout(reassure, this.reassureMs),
      capTimer: this.clock.setTimeout(() => this.endAfterPauseCap(), this.pauseCapMs),
    }
    return { kind: 'accepted' }
  }

  private resumeCall(): CommandResult {
    if (this.pause === null) return { kind: 'accepted' }
    this.clearPause()
    this.emit('command.applied', { command: 'resume' })
    const open = this.open
    if (open !== null) {
      // Restart the clock where it stopped.
      open.expiresAtMs = this.clock.now() + (open.remainingMs ?? 0)
      open.remainingMs = null
      open.timer = this.clock.setTimeout(
        () => this.settle(open, 'expired', { kind: 'timeout' }, undefined),
        open.expiresAtMs - this.clock.now(),
      )
      this.emitPing(open)
      this.emit('status.changed', { status: 'waiting_on_you' })
    } else {
      this.emit('status.changed', { status: 'talking' })
      this.next()
    }
    return { kind: 'accepted' }
  }

  private endByUser(): void {
    if (this.ended) return
    this.emit('command.applied', { command: 'end' })
    this.finish(
      "I'm sorry, I have to go. Alex will follow up. Thank you for your time. Goodbye.",
      'user_ended',
    )
  }

  private endAfterPauseCap(): void {
    this.finish(
      "I'm sorry for the wait. Alex will call you back. Thank you for your time.",
      'pause_cap',
    )
  }

  /** Says a last line and ends the call as Needs follow-up, cancelling an open ping. */
  private finish(goodbye: string, endedReason: string): void {
    this.clearTimers()
    this.queue = []
    if (this.open !== null) {
      this.emit('ping.resolved', { ping_id: this.open.pingId, state: 'cancelled' })
      this.open = null
    }
    this.emit('transcript.final', { role: 'agent', text: goodbye, source_start_ts: null })
    this.emit('status.changed', { status: 'ended' })
    this.emit('call.ended', { outcome: 'needs_follow_up', ended_reason: endedReason })
  }

  private next(): void {
    if (this.stopped || this.ended || this.open !== null || this.pause !== null) return
    const step = this.queue.shift()
    if (step === undefined) return
    const timer = this.clock.setTimeout(() => {
      this.pending = null
      if ('ask' in step) this.ask(step)
      else {
        this.emit(step.type, step.payload)
        this.next()
      }
    }, step.delayMs)
    this.pending = { step, timer }
  }

  private ask(step: AskStep): void {
    this.pingCount += 1
    const open: OpenAsk = {
      pingId: `${this.callId}-ping-${this.pingCount}`,
      step,
      timer: null,
      expiresAtMs: this.clock.now() + this.pingWindowMs,
      remainingMs: null,
    }
    this.emit('status.changed', { status: 'waiting_on_you' })
    this.emitPing(open)
    open.timer = this.clock.setTimeout(
      () => this.settle(open, 'expired', { kind: 'timeout' }, undefined),
      this.pingWindowMs,
    )
    this.open = open
  }

  private settle(open: OpenAsk, state: string, answer: MockAnswer, replyText: string | undefined) {
    if (this.open !== open) return
    this.clock.clearTimeout(open.timer)
    this.open = null
    this.emit('ping.resolved', {
      ping_id: open.pingId,
      state,
      ...(replyText !== undefined ? { reply_text: replyText } : {}),
    })
    this.queue = [...open.step.then(answer), ...this.queue]
    // A reply while paused is queued; the call carries on after Resume.
    if (this.pause !== null) return
    this.emit('status.changed', { status: 'talking' })
    this.next()
  }

  /** Emits `ping.opened` for a new ping, or again for the same ping when its clock freezes or restarts. */
  private emitPing(open: OpenAsk): void {
    const { ask } = open.step
    this.emit('ping.opened', {
      ping_id: open.pingId,
      call_id: this.callId,
      kind: ask.kind,
      ...(ask.field_key !== undefined ? { field_key: ask.field_key } : {}),
      question: ask.question,
      reason: ask.reason,
      suggested_replies: ask.replies,
      expires_at: open.expiresAtMs === null ? null : new Date(open.expiresAtMs).toISOString(),
      remaining_ms:
        open.expiresAtMs === null
          ? (open.remainingMs ?? 0)
          : Math.max(0, open.expiresAtMs - this.clock.now()),
      state: 'open',
    })
  }

  private clearPause(): void {
    if (this.pause === null) return
    this.clock.clearTimeout(this.pause.reassureTimer)
    this.clock.clearTimeout(this.pause.capTimer)
    this.pause = null
  }

  private clearTimers(): void {
    if (this.pending !== null) this.clock.clearTimeout(this.pending.timer)
    this.pending = null
    if (this.open !== null) this.clock.clearTimeout(this.open.timer)
    this.clearPause()
  }

  private emit(type: string, payload: Record<string, unknown>): void {
    if (this.ended) return
    if (type === 'status.changed' && typeof payload.status === 'string') this.status = payload.status
    this.seq += 1
    this.onEvent({
      event_id: `${this.callId}-${this.seq}`,
      call_id: this.callId,
      seq: this.seq,
      type,
      occurred_at: new Date(this.clock.now()).toISOString(),
      source: type === 'transcript.final' ? 'vapi' : 'backend',
      schema_version: 1,
      payload,
    })
    if (type === 'call.ended') {
      this.ended = true
      this.clearTimers()
    }
  }
}

/** Turns a reply body into an answer, or null if it doesn't fit the ping. */
function toAnswer(reply: PingReplyCommand, replies: readonly string[]): MockAnswer | null {
  if ('choice' in reply) {
    const ok = Number.isInteger(reply.choice) && reply.choice >= 0 && reply.choice < replies.length
    return ok ? { kind: 'choice', index: reply.choice } : null
  }
  const text = sanitizeReplyText(reply.text)
  return text === '' ? null : { kind: 'text', text }
}

/**
 * Plays `script` from `startedAt` (ISO 8601) and returns the events so far
 * plus senders for ping replies and commands. A new `startedAt` restarts the call from the top.
 */
export function useMockCall(
  script: readonly ScriptStep[],
  callId: string,
  startedAt: string,
): { events: CallEvent[]; sendReply: SendPingReply; sendCommand: SendCallCommand } {
  const [played, setPlayed] = useState<{ startedAt: string; events: CallEvent[] }>({
    startedAt,
    events: [],
  })
  const engine = useRef<MockCallEngine | null>(null)

  useEffect(() => {
    const current = new MockCallEngine(callId, (event) =>
      setPlayed((prev) =>
        prev.startedAt === startedAt
          ? { startedAt, events: [...prev.events, event] }
          : { startedAt, events: [event] },
      ),
    )
    engine.current = current
    current.start(script)
    return () => {
      current.stop()
      if (engine.current === current) engine.current = null
    }
  }, [script, callId, startedAt])

  const sendReply = useCallback<SendPingReply>(
    (id, pingId, reply) =>
      engine.current?.reply(id, pingId, reply) ?? Promise.resolve({ kind: 'closed' }),
    [],
  )

  const sendCommand = useCallback<SendCallCommand>(
    (id, command) =>
      engine.current?.command(id, command) ??
      Promise.resolve({ kind: 'error', message: 'This call is not running.' }),
    [],
  )

  return { events: played.startedAt === startedAt ? played.events : [], sendReply, sendCommand }
}
