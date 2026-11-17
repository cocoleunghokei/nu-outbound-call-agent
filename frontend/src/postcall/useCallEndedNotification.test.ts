import { describe, expect, it } from 'vitest'
import { CLOCK_SKEW_MS, createCallEndedTracker, type EndedInfo } from './useCallEndedNotification'

const MOUNTED_AT = Date.parse('2026-11-15T18:00:00Z')
const iso = (ms: number) => new Date(ms).toISOString()
const endedAfterMount: EndedInfo = { outcome: 'booked', endedAt: iso(MOUNTED_AT + 60_000) }

describe('createCallEndedTracker', () => {
  it('notifies when the call ends while the page is open', () => {
    const tracker = createCallEndedTracker(MOUNTED_AT)
    expect(tracker.check('c1', 'Juniper Ramen', null)).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', endedAfterMount)).toEqual({
      callId: 'c1',
      title: 'Juniper Ramen',
      body: 'Booked. Tap to see the summary.',
    })
  })

  it('notifies only once, even when re-renders pass a new ended object each time', () => {
    const tracker = createCallEndedTracker(MOUNTED_AT)
    expect(tracker.check('c1', 'Juniper Ramen', { ...endedAfterMount })).not.toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', { ...endedAfterMount })).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', { ...endedAfterMount, outcome: 'cancelled' })).toBeNull()
  })

  it('skips a call that ended before the page opened, even when its events load late (real mode)', () => {
    const tracker = createCallEndedTracker(MOUNTED_AT)
    // First render: real mode has no events yet, so the call looks still running.
    expect(tracker.check('c1', 'Juniper Ramen', null)).toBeNull()
    // The stored call.ended arrives later, but it happened an hour before the reload.
    const stored: EndedInfo = { outcome: 'booked', endedAt: iso(MOUNTED_AT - 3_600_000) }
    expect(tracker.check('c1', 'Juniper Ramen', stored)).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', { ...stored })).toBeNull()
  })

  it('still notifies for an end within the allowed clock difference', () => {
    const tracker = createCallEndedTracker(MOUNTED_AT)
    const slightlyEarly: EndedInfo = { outcome: 'booked', endedAt: iso(MOUNTED_AT - CLOCK_SKEW_MS + 1_000) }
    expect(tracker.check('c1', 'Juniper Ramen', slightlyEarly)).not.toBeNull()
  })

  it('waits for the real business name instead of using the loading placeholder', () => {
    const tracker = createCallEndedTracker(MOUNTED_AT)
    expect(tracker.check('c1', null, endedAfterMount)).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', endedAfterMount)?.title).toBe('Juniper Ramen')
  })

  it('treats an unreadable end time as live', () => {
    const tracker = createCallEndedTracker(MOUNTED_AT)
    expect(tracker.check('c1', 'Shop', { outcome: null, endedAt: 'not a date' })?.body).toBe(
      'The call ended. The summary will be ready soon.',
    )
  })
})
