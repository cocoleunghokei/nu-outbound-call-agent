import { describe, expect, it } from 'vitest'
import { createCallEndedTracker, endedHistory, type EndedInfo } from './useCallEndedNotification'

const ended: EndedInfo = { outcome: 'booked' }

describe('endedHistory', () => {
  it('treats the mock call (no stored history) as live', () => {
    expect(endedHistory(5, undefined)).toBe('live')
  })

  it('waits while the first load of stored events is still running', () => {
    expect(endedHistory(undefined, null)).toBe('unknown')
    expect(endedHistory(5, null)).toBe('unknown')
  })

  it('marks an end that was in the first load as historical', () => {
    expect(endedHistory(5, 9)).toBe('historical')
    expect(endedHistory(9, 9)).toBe('historical')
  })

  it('marks an end that arrived after the first load as live', () => {
    expect(endedHistory(10, 9)).toBe('live')
    expect(endedHistory(1, 0)).toBe('live') // the first load was empty
  })
})

describe('createCallEndedTracker', () => {
  it('notifies when the call ends live', () => {
    const tracker = createCallEndedTracker()
    expect(tracker.check('c1', 'Juniper Ramen', null, 'live')).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', ended, 'live')).toEqual({
      callId: 'c1',
      title: 'Juniper Ramen',
      body: 'Booked. Tap to see the summary.',
    })
  })

  it('notifies only once, even when re-renders pass a new ended object each time', () => {
    const tracker = createCallEndedTracker()
    expect(tracker.check('c1', 'Juniper Ramen', { ...ended }, 'live')).not.toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', { ...ended }, 'live')).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', { outcome: 'cancelled' }, 'live')).toBeNull()
  })

  it('skips a call that had already ended when the page loaded, even when its events load late', () => {
    const tracker = createCallEndedTracker()
    // Real mode: nothing loaded yet, then the stored events (with call.ended) arrive.
    expect(tracker.check('c1', 'Juniper Ramen', null, 'unknown')).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', ended, 'historical')).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', { ...ended }, 'historical')).toBeNull()
  })

  it('decides nothing until the first load has finished', () => {
    const tracker = createCallEndedTracker()
    expect(tracker.check('c1', 'Juniper Ramen', ended, 'unknown')).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', ended, 'live')).not.toBeNull()
  })

  it('waits for the real business name instead of using the loading placeholder', () => {
    const tracker = createCallEndedTracker()
    expect(tracker.check('c1', null, ended, 'live')).toBeNull()
    expect(tracker.check('c1', 'Juniper Ramen', ended, 'live')?.title).toBe('Juniper Ramen')
  })

  it('says "Your call" once it gives up waiting for a name that never loads', () => {
    const tracker = createCallEndedTracker()
    expect(tracker.check('c1', null, ended, 'live', false)).toBeNull()
    expect(tracker.check('c1', null, ended, 'live', true)?.title).toBe('Your call')
  })
})
