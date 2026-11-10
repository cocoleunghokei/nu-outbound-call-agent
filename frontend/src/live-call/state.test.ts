import { describe, expect, it } from 'vitest'
import { deriveLiveCall, mergeEvents, statusStep } from './state'
import type { CallEvent } from './types'

const CALL_ID = 'call-1'

/** Builds a call event with fake data; only `seq`, `type` and `payload` matter here. */
function ev(seq: number, type: string, payload: Record<string, unknown>): CallEvent {
  return {
    event_id: `e${seq}`,
    call_id: CALL_ID,
    seq,
    type,
    occurred_at: new Date(Date.UTC(2026, 10, 10, 15, 0, seq)).toISOString(),
    schema_version: 1,
    payload,
  }
}

const status = (seq: number, value: unknown) => ev(seq, 'status.changed', { status: value })
const line = (seq: number, role: unknown, text: unknown) =>
  ev(seq, 'transcript.final', { role, text, source_start_ts: null })

describe('statusStep', () => {
  it('shows statuses before the call connects as Dialing', () => {
    expect(statusStep('creating')).toBe('dialing')
    expect(statusStep('creation_uncertain')).toBe('dialing')
    expect(statusStep('dialing')).toBe('dialing')
  })

  it('shows a failed call as Ended', () => {
    expect(statusStep('failed')).toBe('ended')
  })

  it('passes the in-call statuses through', () => {
    for (const s of ['in_phone_menu', 'on_hold', 'talking', 'waiting_on_you', 'ended']) {
      expect(statusStep(s)).toBe(s)
    }
  })

  it('shows a status it does not know as unknown instead of crashing', () => {
    expect(statusStep('transferring')).toBe('unknown')
    expect(statusStep('')).toBe('unknown')
  })
})

describe('deriveLiveCall', () => {
  it('starts as creating with no lines before any event', () => {
    expect(deriveLiveCall([])).toEqual({ status: 'creating', lines: [], ping: null, ended: null })
  })

  it('tracks the latest status and the transcript in order', () => {
    const view = deriveLiveCall([
      status(1, 'dialing'),
      status(2, 'talking'),
      line(3, 'business', 'Brookline Family Medicine, this is Dana.'),
      line(4, 'agent', "Hi, I'm an AI assistant calling on behalf of Alex."),
      status(5, 'waiting_on_you'),
    ])
    expect(view.status).toBe('waiting_on_you')
    expect(view.lines.map((l) => [l.role, l.text])).toEqual([
      ['business', 'Brookline Family Medicine, this is Dana.'],
      ['agent', "Hi, I'm an AI assistant calling on behalf of Alex."],
    ])
    expect(view.lines[0].key).toBe('e3')
  })

  it('keeps a status value it does not know, so the page can show Unknown', () => {
    expect(deriveLiveCall([status(1, 'transferring')]).status).toBe('transferring')
  })

  it('skips malformed transcript lines instead of crashing', () => {
    const view = deriveLiveCall([
      line(1, 'robot', 'wrong role'),
      line(2, 'agent', 42),
      line(3, 'business', '   '),
      ev(4, 'transcript.final', {}),
      line(5, 'business', 'Hello?'),
    ])
    expect(view.lines.map((l) => l.text)).toEqual(['Hello?'])
  })

  it('skips malformed status events', () => {
    expect(deriveLiveCall([status(1, 'talking'), status(2, 7), status(3, '')]).status).toBe(
      'talking',
    )
  })

  it('ignores event types it does not handle yet', () => {
    const view = deriveLiveCall([
      status(1, 'talking'),
      ev(2, 'agent.decision', { situation: 'x', action: 'accept', reason: 'y' }),
      ev(3, 'some.future.event', { anything: true }),
    ])
    expect(view).toEqual({ status: 'talking', lines: [], ping: null, ended: null })
  })

  it('marks the call ended with its outcome on call.ended', () => {
    const view = deriveLiveCall([
      status(1, 'talking'),
      ev(2, 'call.ended', { outcome: 'rescheduled', ended_reason: 'agent_hung_up' }),
    ])
    expect(view.status).toBe('ended')
    expect(view.ended).toEqual({
      outcome: 'rescheduled',
      endedReason: 'agent_hung_up',
      endedAt: '2026-11-10T15:00:02.000Z',
    })
  })

  it('keeps a failed status when the call ends', () => {
    const view = deriveLiveCall([
      status(1, 'failed'),
      ev(2, 'call.ended', { outcome: 'failed', ended_reason: 'dial_error' }),
    ])
    expect(view.status).toBe('failed')
  })

  it('never reopens an ended call', () => {
    const view = deriveLiveCall([
      ev(1, 'call.ended', { outcome: null, ended_reason: 'user_ended' }),
      status(2, 'talking'),
      ev(3, 'call.ended', { outcome: 'booked', ended_reason: 'late duplicate' }),
    ])
    expect(view.status).toBe('ended')
    expect(view.ended?.outcome).toBeNull()
    expect(view.ended?.endedReason).toBe('user_ended')
  })

  it('does not leave a terminal status once reached', () => {
    expect(deriveLiveCall([status(1, 'ended'), status(2, 'talking')]).status).toBe('ended')
  })

  it('still shows lines that arrive after the call ended', () => {
    const view = deriveLiveCall([
      ev(1, 'call.ended', { outcome: 'booked', ended_reason: 'agent_hung_up' }),
      line(2, 'agent', 'Goodbye.'),
    ])
    expect(view.lines).toHaveLength(1)
  })
})

/** A `ping.opened` event with fake data. */
const opened = (seq: number, pingId: string, overrides: Record<string, unknown> = {}) =>
  ev(seq, 'ping.opened', {
    ping_id: pingId,
    call_id: CALL_ID,
    kind: 'decision',
    question: `Question ${pingId}`,
    reason: 'You said before 3pm.',
    suggested_replies: ['Yes', 'No'],
    expires_at: '2026-11-10T15:00:45.000Z',
    remaining_ms: 45_000,
    state: 'open',
    ...overrides,
  })
const resolved = (seq: number, pingId: string, state: string, replyText?: string) =>
  ev(seq, 'ping.resolved', { ping_id: pingId, state, ...(replyText ? { reply_text: replyText } : {}) })

describe('deriveLiveCall pings', () => {
  it('shows an opened ping as open', () => {
    const view = deriveLiveCall([opened(1, 'p1')])
    expect(view.ping).toMatchObject({ state: 'open', replacesEarlier: false })
    expect(view.ping?.ping.question).toBe('Question p1')
  })

  it('closes the ping with its final state and reply', () => {
    const view = deriveLiveCall([opened(1, 'p1'), resolved(2, 'p1', 'answered', 'Yes')])
    expect(view.ping).toMatchObject({ state: 'answered', replyText: 'Yes' })
  })

  it('ignores a resolution for another ping', () => {
    const view = deriveLiveCall([opened(1, 'p1'), resolved(2, 'p0', 'expired')])
    expect(view.ping?.state).toBe('open')
  })

  it('never reopens a closed ping, and keeps the first resolution', () => {
    const view = deriveLiveCall([
      opened(1, 'p1'),
      resolved(2, 'p1', 'expired'),
      resolved(3, 'p1', 'answered', 'late'),
      opened(4, 'p1'),
    ])
    expect(view.ping?.state).toBe('expired')
    expect(view.ping?.replyText).toBeUndefined()
  })

  it('shows only the newest question when the offer changes (DC-7)', () => {
    const view = deriveLiveCall([opened(1, 'p1'), opened(2, 'p2'), resolved(3, 'p1', 'superseded')])
    expect(view.ping?.ping.ping_id).toBe('p2')
    expect(view.ping).toMatchObject({ state: 'open', replacesEarlier: true })
  })

  it('flags the replacement when the superseded event arrives first', () => {
    const view = deriveLiveCall([opened(1, 'p1'), resolved(2, 'p1', 'superseded'), opened(3, 'p2')])
    expect(view.ping).toMatchObject({ state: 'open', replacesEarlier: true })
  })

  it('does not flag a replacement after an answered ping', () => {
    const view = deriveLiveCall([opened(1, 'p1'), resolved(2, 'p1', 'answered'), opened(3, 'p2')])
    expect(view.ping?.replacesEarlier).toBe(false)
  })

  it('refreshes the same open ping, e.g. when its clock freezes', () => {
    const view = deriveLiveCall([
      opened(1, 'p1'),
      opened(2, 'p1', { expires_at: null, remaining_ms: 20_000 }),
    ])
    expect(view.ping?.ping.expires_at).toBeNull()
    expect(view.ping?.ping.remaining_ms).toBe(20_000)
    expect(view.ping?.replacesEarlier).toBe(false)
  })

  it('never shows a ping stored as already closed', () => {
    expect(deriveLiveCall([opened(1, 'p1', { state: 'superseded' })]).ping).toBeNull()
  })

  it('drops a malformed ping', () => {
    expect(deriveLiveCall([opened(1, 'p1', { question: 7 })]).ping).toBeNull()
  })

  it('cancels an open ping when the call ends, and opens none after', () => {
    const view = deriveLiveCall([
      opened(1, 'p1'),
      ev(2, 'call.ended', { outcome: 'needs_follow_up', ended_reason: 'user_ended' }),
      opened(3, 'p2'),
    ])
    expect(view.ping?.ping.ping_id).toBe('p1')
    expect(view.ping?.state).toBe('cancelled')
  })
})

describe('mergeEvents', () => {
  it('orders events by seq when they arrive out of order', () => {
    const merged = mergeEvents([status(1, 'dialing')], [line(3, 'agent', 'b'), line(2, 'agent', 'a')])
    expect(merged.map((e) => e.seq)).toEqual([1, 2, 3])
  })

  it('drops a redelivered event and keeps the first copy', () => {
    const first = line(2, 'agent', 'first')
    const redelivered = { ...line(2, 'agent', 'second copy'), event_id: 'other' }
    const merged = mergeEvents([status(1, 'dialing'), first], [redelivered])
    expect(merged).toHaveLength(2)
    expect(merged[1]).toBe(first)
  })

  it('does not change the arrays it is given', () => {
    const held = [status(2, 'talking')]
    const incoming = [status(1, 'dialing')]
    mergeEvents(held, incoming)
    expect(held.map((e) => e.seq)).toEqual([2])
    expect(incoming.map((e) => e.seq)).toEqual([1])
  })
})
