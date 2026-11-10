/**
 * A scripted fake call, so the live-call screen can be built before the
 * backend is ready. It plays the PRD's Dr. Lee reschedule scenario as the same
 * `call_events` rows Supabase Realtime will deliver, and answers ping replies
 * the way `POST /calls/{call_id}/pings/{ping_id}/reply` will (including
 * `409 ping_closed` after the timeout). All names and numbers are fake.
 *
 * To connect the real backend, replace `useMockCall` with a hook that loads the
 * call's events, subscribes to new ones and posts replies; the screen stays the same.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
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
    line(1800, 'business', 'Let me look. Friday at 10am is open.'),
    line(700, 'agent', 'Friday at 10am works. Please book it.'),
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
      return [line(600, 'agent', 'Thursday at 4pm works. Please book it.'), ...confirmSteps('Thursday at 4pm')]
    }
    if (answer.kind === 'choice' && answer.index === 2) {
      return [
        line(600, 'agent', "That time won't work for Alex. I'll confirm with Alex and call back. Thank you, Dana."),
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
    answer.kind === 'choice' && answer.index === 0
      ? line(600, 'agent', 'Thanks for holding. The date of birth is March 4th, 2004.')
      : line(600, 'agent', 'Thanks for holding. Alex will share that at the visit.'),
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

interface OpenAsk {
  pingId: string
  step: AskStep
  timer: unknown
}

/**
 * Plays a script, one step at a time, and acts as the backend for ping
 * replies. Events are numbered from 1 with no gaps, like `call_events.seq`.
 */
export class MockCallEngine {
  private seq = 0
  private pingCount = 0
  private queue: ScriptStep[] = []
  private timer: unknown = null
  private open: OpenAsk | null = null
  private stopped = false
  private readonly callId: string
  private readonly onEvent: (event: CallEvent) => void
  private readonly clock: MockClock
  private readonly pingWindowMs: number

  constructor(
    callId: string,
    onEvent: (event: CallEvent) => void,
    clock: MockClock = realClock,
    pingWindowMs: number = PING_WINDOW_MS,
  ) {
    this.callId = callId
    this.onEvent = onEvent
    this.clock = clock
    this.pingWindowMs = pingWindowMs
  }

  /** Starts playing `script` from the top. */
  start(script: readonly ScriptStep[]): void {
    this.queue = [...script]
    this.next()
  }

  /** Stops all timers; no more events are emitted. */
  stop(): void {
    this.stopped = true
    if (this.timer !== null) this.clock.clearTimeout(this.timer)
    if (this.open !== null) this.clock.clearTimeout(this.open.timer)
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

  private next(): void {
    if (this.stopped || this.open !== null) return
    const step = this.queue.shift()
    if (step === undefined) return
    this.timer = this.clock.setTimeout(() => {
      this.timer = null
      if ('ask' in step) this.ask(step)
      else {
        this.emit(step.type, step.payload)
        this.next()
      }
    }, step.delayMs)
  }

  private ask(step: AskStep): void {
    this.pingCount += 1
    const pingId = `${this.callId}-ping-${this.pingCount}`
    this.emit('status.changed', { status: 'waiting_on_you' })
    this.emit('ping.opened', {
      ping_id: pingId,
      call_id: this.callId,
      kind: step.ask.kind,
      ...(step.ask.field_key !== undefined ? { field_key: step.ask.field_key } : {}),
      question: step.ask.question,
      reason: step.ask.reason,
      suggested_replies: step.ask.replies,
      expires_at: new Date(this.clock.now() + this.pingWindowMs).toISOString(),
      remaining_ms: this.pingWindowMs,
      state: 'open',
    })
    const open: OpenAsk = { pingId, step, timer: null }
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
    this.emit('status.changed', { status: 'talking' })
    this.queue = [...open.step.then(answer), ...this.queue]
    this.next()
  }

  private emit(type: string, payload: Record<string, unknown>): void {
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
 * plus a reply sender. A new `startedAt` restarts the call from the top.
 */
export function useMockCall(
  script: readonly ScriptStep[],
  callId: string,
  startedAt: string,
): { events: CallEvent[]; sendReply: SendPingReply } {
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

  return { events: played.startedAt === startedAt ? played.events : [], sendReply }
}
