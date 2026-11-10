import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DR_LEE_SCRIPT, MOCK_CALL_ID, MockCallEngine, type MockClock } from './mockCall'
import { deriveLiveCall } from './state'
import type { CallEvent } from './types'

const WINDOW_MS = 45_000

/** Runs the Dr. Lee script on fake timers and collects every event. */
function startCall() {
  const events: CallEvent[] = []
  const clock: MockClock = {
    now: () => Date.now(),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  }
  const engine = new MockCallEngine(MOCK_CALL_ID, (e) => events.push(e), clock, WINDOW_MS)
  engine.start(DR_LEE_SCRIPT)
  const view = () => deriveLiveCall(events)
  /** Advances time until a ping is open, and returns its id. */
  const nextPing = async () => {
    for (let i = 0; i < 100; i += 1) {
      const ping = view().ping
      if (ping !== null && ping.state === 'open') return ping.ping.ping_id
      await vi.advanceTimersByTimeAsync(500)
    }
    throw new Error('no ping opened')
  }
  const runToEnd = async () => {
    for (let i = 0; i < 200 && view().ended === null; i += 1) {
      await vi.advanceTimersByTimeAsync(500)
    }
  }
  return { engine, events, view, nextPing, runToEnd }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-11-10T15:00:00.000Z'))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('MockCallEngine', () => {
  it('numbers events from 1 with no gaps, like the backend', async () => {
    const call = startCall()
    const first = await call.nextPing()
    await call.engine.reply(MOCK_CALL_ID, first, { choice: 0 })
    const second = await call.nextPing()
    await call.engine.reply(MOCK_CALL_ID, second, { choice: 0 })
    await call.runToEnd()
    expect(call.events.map((e) => e.seq)).toEqual(call.events.map((_, i) => i + 1))
  })

  it('opens with the DC-1 disclosure: AI assistant, on behalf of, transcribed', async () => {
    const call = startCall()
    await call.nextPing()
    const firstAgentLine = call.view().lines.find((l) => l.role === 'agent')
    expect(firstAgentLine?.text).toMatch(
      /^Hi, I'm an AI assistant calling on behalf of \w+\. This call is transcribed\. /,
    )
  })

  it('waits on the user with an Ask-me-first ping that has no free text option', async () => {
    const call = startCall()
    await call.nextPing()
    const view = call.view()
    expect(view.status).toBe('waiting_on_you')
    expect(view.ping?.ping.kind).toBe('field_approval')
    expect(view.ping?.ping.suggested_replies).toEqual(['Share it', 'Don’t share'])
    expect(view.ping?.ping.remaining_ms).toBe(WINDOW_MS)
  })

  it('accepts a reply while the ping is open and carries on the call', async () => {
    const call = startCall()
    const pingId = await call.nextPing()
    await expect(call.engine.reply(MOCK_CALL_ID, pingId, { choice: 0 })).resolves.toEqual({
      kind: 'accepted',
    })
    expect(call.view().ping).toMatchObject({ state: 'answered', replyText: 'Share it' })
    expect(call.view().status).toBe('talking')
    await vi.advanceTimersByTimeAsync(1000)
    const lines = call.view().lines
    expect(lines[lines.length - 1]?.text).toContain('date of birth is')
  })

  it('times out after the reply window and answers a late reply with closed (409)', async () => {
    const call = startCall()
    const pingId = await call.nextPing()
    await vi.advanceTimersByTimeAsync(WINDOW_MS)
    expect(call.view().ping?.state).toBe('expired')
    await expect(call.engine.reply(MOCK_CALL_ID, pingId, { choice: 0 })).resolves.toEqual({
      kind: 'closed',
    })
  })

  it('takes the fallback when the offer ping times out, ending rescheduled (DC-9)', async () => {
    const call = startCall()
    const dob = await call.nextPing()
    await call.engine.reply(MOCK_CALL_ID, dob, { choice: 1 })
    await call.nextPing()
    await vi.advanceTimersByTimeAsync(WINDOW_MS)
    await call.runToEnd()
    expect(call.view().ended?.outcome).toBe('rescheduled')
    expect(call.view().lines.some((l) => l.text.includes('Friday morning'))).toBe(true)
  })

  it('ends as needs follow-up when the user declines the offer', async () => {
    const call = startCall()
    await call.engine.reply(MOCK_CALL_ID, await call.nextPing(), { choice: 1 })
    await call.engine.reply(MOCK_CALL_ID, await call.nextPing(), { choice: 2 })
    await call.runToEnd()
    expect(call.view().ended?.outcome).toBe('needs_follow_up')
  })

  it('accepts a cleaned free-text reply on a decision ping', async () => {
    const call = startCall()
    await call.engine.reply(MOCK_CALL_ID, await call.nextPing(), { choice: 1 })
    const offer = await call.nextPing()
    await expect(
      call.engine.reply(MOCK_CALL_ID, offer, { text: '  ask for\nFriday  ' }),
    ).resolves.toEqual({ kind: 'accepted' })
    expect(call.view().ping?.replyText).toBe('ask for Friday')
  })

  it('rejects a choice outside the suggested replies and an empty text reply', async () => {
    const call = startCall()
    const pingId = await call.nextPing()
    expect((await call.engine.reply(MOCK_CALL_ID, pingId, { choice: 5 })).kind).toBe('error')
    expect((await call.engine.reply(MOCK_CALL_ID, pingId, { text: ' \u0007 ' })).kind).toBe('error')
    expect(call.view().ping?.state).toBe('open')
  })

  it('answers closed for an unknown ping or after stop', async () => {
    const call = startCall()
    const pingId = await call.nextPing()
    expect((await call.engine.reply(MOCK_CALL_ID, 'other', { choice: 0 })).kind).toBe('closed')
    call.engine.stop()
    expect((await call.engine.reply(MOCK_CALL_ID, pingId, { choice: 0 })).kind).toBe('closed')
  })
})
