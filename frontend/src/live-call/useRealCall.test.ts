import { describe, expect, it } from 'vitest'
import type { CallEvent } from './types'
import { markHistory } from './useRealCall'

/** A stored call event with fake data; only `seq` matters here. */
function ev(seq: number): CallEvent {
  return {
    event_id: `e${seq}`,
    call_id: 'call-1',
    seq,
    type: 'status.changed',
    occurred_at: new Date(Date.UTC(2026, 10, 10, 15, 0, seq)).toISOString(),
    schema_version: 1,
    payload: { status: 'ended' },
  }
}

describe('markHistory', () => {
  it('marks the highest seq of the first successful load, or 0 when there were none', () => {
    expect(markHistory(null, [ev(1), ev(4), ev(2)])).toBe(4)
    expect(markHistory(null, [])).toBe(0)
  })

  it('keeps the first mark on later reloads', () => {
    expect(markHistory(4, [ev(1), ev(4), ev(9)])).toBe(4)
    expect(markHistory(4, null)).toBe(4)
  })

  // Regression (#64 review): a failed first load used to mark 0, so a reload after a
  // reconnect counted an already-ended call's stored end as live and notified.
  it('stays unknown after a failed first load, then marks the next successful one', () => {
    const afterFailure = markHistory(null, null)
    expect(afterFailure).toBeNull()
    expect(markHistory(afterFailure, [ev(1), ev(7)])).toBe(7)
  })
})
