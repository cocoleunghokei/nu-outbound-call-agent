import { describe, expect, it, vi } from 'vitest'
import type { SessionResult } from '../lib/session'
import {
  followCall,
  markHistory,
  NOT_FOUND,
  problemOf,
  RECONNECTING,
  SIGN_IN_FAILED,
  SUMMARY_FAILED,
  type CallSource,
  type EventsResult,
  type FollowIo,
  type FollowSink,
  type SummaryResult,
} from './follow'
import { mergeEvents } from './state'
import type { CallEvent } from './types'

const CALL_ID = 'call-1'

/** A stored call event with fake data; only `seq` matters here. */
function ev(seq: number): CallEvent {
  return {
    event_id: `e${seq}`,
    call_id: CALL_ID,
    seq,
    type: 'status.changed',
    occurred_at: new Date(Date.UTC(2026, 10, 10, 15, 0, seq)).toISOString(),
    schema_version: 1,
    payload: { status: 'ended' },
  }
}

const SUMMARY_ROW = {
  id: CALL_ID,
  created_at: '2026-11-18T20:00:00+00:00',
  max_duration_s: 900,
  briefs: { business_name: 'Eastside Test Bistro', phone_e164: '+16175550199', goal: 'Hold a table for two' },
}

const SIGNED_IN: SessionResult = {
  kind: 'session',
  userId: 'anon-1',
  account: { userId: 'anon-1', anonymous: true, email: null },
}

interface Deferred<T> {
  promise: Promise<T>
  resolve: (value: T) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

/** Lets every pending microtask settle. */
const settle = () => new Promise<void>((r) => setTimeout(r, 0))

/** A fake data source whose answers the test releases one by one, in any order. */
function fakeSource() {
  const eventsAnswers: Deferred<EventsResult>[] = []
  const summaryAnswers: Deferred<SummaryResult>[] = []
  let onInsert: ((row: unknown) => void) | null = null
  let onStatus: ((status: string) => void) | null = null
  const unsubscribe = vi.fn()
  const log: string[] = []
  const source: CallSource = {
    events: vi.fn(async () => {
      log.push('events')
      const d = deferred<EventsResult>()
      eventsAnswers.push(d)
      return d.promise
    }),
    summary: vi.fn(async () => {
      log.push('summary')
      const d = deferred<SummaryResult>()
      summaryAnswers.push(d)
      return d.promise
    }),
    subscribe: vi.fn((_callId, insert, status) => {
      log.push('subscribe')
      onInsert = insert
      onStatus = status
      return unsubscribe
    }),
  }
  return {
    source,
    log,
    unsubscribe,
    /** Answer the n-th events load (0-based). */
    events: (n: number, rows: CallEvent[] | 'error') =>
      eventsAnswers[n].resolve(rows === 'error' ? { kind: 'error' } : { kind: 'rows', rows }),
    /** Answer the n-th summary load (0-based). */
    summary: (n: number, answer: 'row' | 'none' | 'error') =>
      summaryAnswers[n].resolve(answer === 'row' ? { kind: 'row', row: SUMMARY_ROW } : { kind: answer }),
    status: (s: string) => onStatus?.(s),
    insert: (row: unknown) => onInsert?.(row),
    loads: () => eventsAnswers.length,
  }
}

/** Records every sink call and keeps the page's derived values the way the hook does. */
function fakeSink() {
  const calls: Record<keyof FollowSink, unknown[]> = {
    events: [],
    historical: [],
    summary: [],
    missing: [],
    connection: [],
    detail: [],
  }
  const state = { events: [] as CallEvent[], boundary: null as number | null, missing: false, connection: null as string | null, detail: null as string | null }
  const sink: FollowSink = {
    events: (rows) => {
      calls.events.push(rows)
      state.events = mergeEvents(state.events, rows)
    },
    historical: (loaded) => {
      calls.historical.push(loaded)
      state.boundary = markHistory(state.boundary, loaded)
    },
    summary: (row) => calls.summary.push(row),
    missing: (value) => {
      calls.missing.push(value)
      state.missing = value
    },
    connection: (problem) => {
      calls.connection.push(problem)
      state.connection = problem
    },
    detail: (problem) => {
      calls.detail.push(problem)
      state.detail = problem
    },
  }
  return { sink, calls, state, problem: () => problemOf(state.missing, state.connection, state.detail) }
}

function io(source: CallSource, signIn: () => Promise<SessionResult> = async () => SIGNED_IN): FollowIo {
  return { open: async () => ({ kind: 'ok', source }), signIn }
}

/** Starts a follower and lets it get past open, sign-in, subscribe and the first load's requests. */
async function started(signIn?: () => Promise<SessionResult>) {
  const src = fakeSource()
  const out = fakeSink()
  const stop = followCall(CALL_ID, io(src.source, signIn), out.sink)
  await settle()
  return { ...src, ...out, stop }
}

describe('followCall: before anything is subscribed', () => {
  it('reports a missing Supabase configuration and never signs in', async () => {
    const src = fakeSource()
    const out = fakeSink()
    const signIn = vi.fn(async () => SIGNED_IN)
    followCall(CALL_ID, { open: async () => ({ kind: 'failed', message: 'No settings' }), signIn }, out.sink)
    await settle()
    expect(out.calls.connection).toEqual(['No settings'])
    expect(signIn).not.toHaveBeenCalled()
    expect(src.source.subscribe).not.toHaveBeenCalled()
  })

  it('does nothing after being stopped while the client was still loading', async () => {
    const src = fakeSource()
    const out = fakeSink()
    const opening = deferred<{ kind: 'ok'; source: CallSource }>()
    const signIn = vi.fn(async () => SIGNED_IN)
    const stop = followCall(CALL_ID, { open: () => opening.promise, signIn }, out.sink)
    stop()
    opening.resolve({ kind: 'ok', source: src.source })
    await settle()
    expect(signIn).not.toHaveBeenCalled()
    expect(src.source.subscribe).not.toHaveBeenCalled()
    expect(out.calls.connection).toEqual([])
  })

  it('reports a failed sign-in and runs no query', async () => {
    const f = await started(async () => ({ kind: 'failed', message: 'Anonymous sign-ins are disabled' }))
    expect(f.calls.connection).toEqual([SIGN_IN_FAILED])
    expect(f.source.subscribe).not.toHaveBeenCalled()
    expect(f.source.events).not.toHaveBeenCalled()
    expect(f.source.summary).not.toHaveBeenCalled()
  })

  // The orphaned-channel window: React cleaned the effect up while the sign-in was pending.
  it('never subscribes after being stopped while the sign-in was pending', async () => {
    const src = fakeSource()
    const out = fakeSink()
    const signingIn = deferred<SessionResult>()
    const stop = followCall(CALL_ID, io(src.source, () => signingIn.promise), out.sink)
    await settle()
    stop()
    signingIn.resolve(SIGNED_IN)
    await settle()
    expect(src.source.subscribe).not.toHaveBeenCalled()
    expect(src.source.events).not.toHaveBeenCalled()
  })
})

describe('followCall: loading and reconnecting', () => {
  it('subscribes first, loads without waiting for the socket, and refreshes on SUBSCRIBED', async () => {
    const f = await started()
    expect(f.log).toEqual(['subscribe', 'events', 'summary'])
    f.events(0, [ev(1), ev(2)])
    f.summary(0, 'row')
    await settle()
    expect(f.state.events.map((e) => e.seq)).toEqual([1, 2])
    expect(f.state.boundary).toBe(2)
    expect(f.calls.summary).toEqual([SUMMARY_ROW])
    expect(f.calls.missing).toEqual([false])
    expect(f.problem()).toBeNull()

    f.status('SUBSCRIBED')
    expect(f.loads()).toBe(2)
    f.events(1, [ev(1), ev(2), ev(3)])
    f.summary(1, 'row')
    await settle()
    // Both loads reported their rows; the boundary stays where the first load put it.
    expect(f.calls.historical).toHaveLength(2)
    expect(f.state.boundary).toBe(2)
    expect(f.problem()).toBeNull()
  })

  // Subscribing is asynchronous, so an insert between the first snapshot and the socket
  // coming up reaches neither; the refresh on SUBSCRIBED is what picks it up.
  it('picks up an event that the first snapshot and the channel both missed', async () => {
    const f = await started()
    f.events(0, [ev(1)])
    f.summary(0, 'row')
    await settle()
    f.status('SUBSCRIBED')
    f.events(1, [ev(1), ev(2)])
    f.summary(1, 'row')
    await settle()
    expect(f.state.events.map((e) => e.seq)).toEqual([1, 2])
    f.insert(ev(3))
    expect(f.state.events.map((e) => e.seq)).toEqual([1, 2, 3])
  })

  it('still delivers the stored events and the summary when Realtime never connects', async () => {
    const f = await started()
    f.status('CHANNEL_ERROR')
    expect(f.problem()).toBe(RECONNECTING)
    f.events(0, [ev(1)])
    f.summary(0, 'row')
    await settle()
    expect(f.state.events.map((e) => e.seq)).toEqual([1])
    expect(f.calls.summary).toEqual([SUMMARY_ROW])
    expect(f.state.boundary).toBe(1)
    // The load began before the drop, so it does not clear "Reconnecting…".
    expect(f.problem()).toBe(RECONNECTING)
  })

  it('applies each read as it arrives, without waiting for the other', async () => {
    const f = await started()
    f.summary(0, 'none')
    await settle()
    expect(f.calls.missing).toEqual([true])
    expect(f.calls.historical).toEqual([])
    f.events(0, [ev(1)])
    await settle()
    expect(f.calls.historical).toEqual([[ev(1)]])
  })

  it('reports an events load that failed, as before', async () => {
    const f = await started()
    f.events(0, 'error')
    f.summary(0, 'row')
    await settle()
    expect(f.calls.historical).toEqual([null])
    expect(f.state.boundary).toBeNull()
    expect(f.problem()).toBe(NOT_FOUND)
  })

  it('stops delivering after being stopped, and unsubscribes once', async () => {
    const f = await started()
    f.stop()
    f.events(0, [ev(1)])
    f.summary(0, 'row')
    await settle()
    expect(f.unsubscribe).toHaveBeenCalledOnce()
    expect(f.calls.events).toEqual([])
    expect(f.calls.summary).toEqual([])
    expect(f.calls.connection).toEqual([])
  })
})

describe('followCall: a call this user cannot see', () => {
  // A fresh browser signs in as a new user, so a link from another browser finds no row
  // (RLS filters silently): the page must say so instead of loading forever.
  it('marks the call missing, and a reconnect does not hide that', async () => {
    const f = await started()
    f.events(0, [])
    f.summary(0, 'none')
    await settle()
    expect(f.state.boundary).toBe(0)
    expect(f.problem()).toBe(NOT_FOUND)

    f.status('CHANNEL_ERROR')
    f.status('SUBSCRIBED')
    expect(f.calls.connection).toContain(null)
    expect(f.problem()).toBe(NOT_FOUND)
  })

  it('clears "missing" when the call appears on a later load', async () => {
    const f = await started()
    f.events(0, [])
    f.summary(0, 'none')
    await settle()
    f.status('SUBSCRIBED')
    f.events(1, [ev(1)])
    f.summary(1, 'row')
    await settle()
    expect(f.calls.missing).toEqual([true, false])
    expect(f.problem()).toBeNull()
  })

  it('ignores an older load\'s "none" that lands after a newer load\'s row', async () => {
    const f = await started()
    f.status('SUBSCRIBED')
    expect(f.loads()).toBe(2)
    f.summary(1, 'row')
    await settle()
    f.summary(0, 'none')
    f.events(0, [ev(1)])
    f.events(1, [ev(1)])
    await settle()
    expect(f.calls.missing).toEqual([false])
    expect(f.problem()).toBeNull()
    // The older load wrote no problem line of its own.
    expect(f.calls.detail).toEqual([null])
  })
})

describe('followCall: the summary read on its own', () => {
  it('reports a summary that failed while events still stream, then recovers on reconnect', async () => {
    const f = await started()
    f.events(0, [ev(1)])
    f.summary(0, 'error')
    await settle()
    expect(f.calls.summary).toEqual([])
    expect(f.calls.missing).toEqual([])
    expect(f.problem()).toBe(SUMMARY_FAILED)

    f.status('SUBSCRIBED')
    f.events(1, [ev(1)])
    f.summary(1, 'row')
    await settle()
    expect(f.calls.summary).toEqual([SUMMARY_ROW])
    expect(f.problem()).toBeNull()
  })

  it('still takes an older load\'s row when a newer load failed first', async () => {
    const f = await started()
    f.status('SUBSCRIBED')
    f.summary(1, 'error')
    await settle()
    expect(f.problem()).toBe(SUMMARY_FAILED)
    f.summary(0, 'row')
    await settle()
    expect(f.calls.summary).toEqual([SUMMARY_ROW])
    expect(f.calls.missing).toEqual([false])
  })
})

describe('problemOf', () => {
  it('ranks a missing call over the socket, and the socket over the details', () => {
    expect(problemOf(true, null, null)).toBe(NOT_FOUND)
    expect(problemOf(true, RECONNECTING, SUMMARY_FAILED)).toBe(NOT_FOUND)
    expect(problemOf(false, RECONNECTING, SUMMARY_FAILED)).toBe(RECONNECTING)
    expect(problemOf(false, null, SUMMARY_FAILED)).toBe(SUMMARY_FAILED)
    expect(problemOf(false, null, null)).toBeNull()
  })
})

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
