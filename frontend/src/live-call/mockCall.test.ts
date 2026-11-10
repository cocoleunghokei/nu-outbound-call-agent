import { describe, expect, it } from 'vitest'
import { DR_LEE_SCRIPT, MOCK_CALL_ID, scriptToEvents } from './mockCall'
import { deriveLiveCall } from './state'

describe('scriptToEvents', () => {
  const start = new Date('2026-11-10T15:00:00.000Z')
  const events = scriptToEvents(DR_LEE_SCRIPT, MOCK_CALL_ID, start)

  it('numbers events from 1 with no gaps, like the backend', () => {
    expect(events.map((e) => e.seq)).toEqual(DR_LEE_SCRIPT.map((_, i) => i + 1))
    expect(new Set(events.map((e) => e.event_id)).size).toBe(events.length)
  })

  it('timestamps each event after the delays before it', () => {
    expect(events[0].occurred_at).toBe('2026-11-10T15:00:00.000Z')
    expect(events[1].occurred_at).toBe('2026-11-10T15:00:01.500Z')
    const times = events.map((e) => Date.parse(e.occurred_at))
    expect(times).toEqual([...times].sort((a, b) => a - b))
  })

  it('plays the Dr. Lee call through to a rescheduled ending', () => {
    const view = deriveLiveCall(events)
    expect(view.status).toBe('ended')
    expect(view.ended?.outcome).toBe('rescheduled')
    expect(view.lines.length).toBeGreaterThan(5)
  })

  it('opens with the DC-1 disclosure: AI assistant, on behalf of, transcribed', () => {
    const firstAgentLine = deriveLiveCall(events).lines.find((l) => l.role === 'agent')
    expect(firstAgentLine?.text).toMatch(/^Hi, I'm an AI assistant calling on behalf of \w+\. This call is transcribed\. /)
  })
})
