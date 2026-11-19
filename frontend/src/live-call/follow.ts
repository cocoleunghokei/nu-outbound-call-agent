/**
 * How the live-call page follows one call, without React: sign the browser in, subscribe
 * to the call's events, load what is stored, keep the page told about problems.
 * `useRealCall` wires it to state through `FollowSink`; tests drive it with fakes.
 *
 * `followCall` returns its stop function synchronously and does all waiting inside, so a
 * React effect can hand the stop function straight back as its cleanup: nothing is ever
 * subscribed after the effect was cleaned up, however long the sign-in or the client load
 * take.
 */
import type { SessionResult } from '../lib/session'
import { toCallEvent } from './backend'
import type { CallEvent } from './types'

export const SIGN_IN_FAILED = "I couldn't sign this browser in, so the call can't load. Reload to try again."
export const RECONNECTING = 'Live updates paused. Reconnecting…'
export const NOT_FOUND = 'Couldn’t load the call. It may not exist, or you may be signed out.'
export const SUMMARY_FAILED = "Couldn't load the call's details. Reload to try again."

export type EventsResult = { kind: 'rows'; rows: unknown[] } | { kind: 'error' }
/** `'none'` is a successful read that found no row: for this user the call does not exist. */
export type SummaryResult = { kind: 'row'; row: unknown } | { kind: 'none' } | { kind: 'error' }

/** The call's data over a loaded Supabase client (or a fake in tests). */
export interface CallSource {
  events(callId: string): Promise<EventsResult>
  summary(callId: string): Promise<SummaryResult>
  /** Starts the Realtime subscription; returns the unsubscribe. */
  subscribe(
    callId: string,
    onInsert: (row: unknown) => void,
    onStatus: (status: string) => void,
  ): () => void
}

export interface FollowIo {
  /** Loads the Supabase client; `failed` when the app has no Supabase settings. */
  open(): Promise<{ kind: 'ok'; source: CallSource } | { kind: 'failed'; message: string }>
  /** Makes sure this browser has a session (`ensureSignedIn`). */
  signIn(): Promise<SessionResult>
}

/** What the page keeps; every call is skipped once the follower was stopped. */
export interface FollowSink {
  /** Checked events to merge in (by `seq`); only called with at least one. */
  events(rows: CallEvent[]): void
  /** A load of the stored events finished: the rows, or null when it failed (see `markHistory`). */
  historical(loaded: CallEvent[] | null): void
  /** The raw `calls` row with its brief. */
  summary(row: unknown): void
  /** Whether a successful read found no such call for this user. */
  missing(value: boolean): void
  /** The socket's state and the events load: `RECONNECTING`, `NOT_FOUND`, or null. */
  connection(problem: string | null): void
  /** The summary load: `SUMMARY_FAILED` or null. */
  detail(problem: string | null): void
}

/**
 * The new `historicalUpTo` after a load of the call's events: `loaded` is what came back,
 * or null when the load failed.
 * - Only the first successful load marks history; reloads after a reconnect don't move it.
 * - A failed load leaves it null (still unknown), so the call-ended notification waits for
 *   a load that works instead of treating every stored event as live.
 */
export function markHistory(first: number | null, loaded: CallEvent[] | null): number | null {
  if (first !== null || loaded === null) return first
  return loaded.reduce((max, e) => Math.max(max, e.seq), 0)
}

/**
 * The one problem line the page shows. A call that does not exist for this user outranks
 * everything (a reconnect must not hide it), then the socket, then the call's details.
 */
export function problemOf(missing: boolean, connection: string | null, detail: string | null): string | null {
  if (missing) return NOT_FOUND
  return connection ?? detail
}

function checked(rows: unknown[]): CallEvent[] {
  return rows.map(toCallEvent).filter((e): e is CallEvent => e !== null)
}

/**
 * Follows `callId`: open the client, sign in, subscribe, load, and reload on every
 * (re)connect. Returns the stop function.
 *
 * Loads carry the socket generation they were started in. Data is applied whenever it
 * arrives (events merge by `seq`; a summary applies unless a newer one already did), but
 * the problem lines are written only by a load from the current generation, so an old
 * load finishing after a `CHANNEL_ERROR` cannot wipe "Reconnecting…".
 */
export function followCall(callId: string, io: FollowIo, sink: FollowSink): () => void {
  let stopped = false
  let unsubscribe: (() => void) | null = null
  let generation = 0
  let appliedSummaryGen = -1

  const loadEvents = async (source: CallSource, gen: number) => {
    const result = await source.events(callId).catch((): EventsResult => ({ kind: 'error' }))
    if (stopped) return
    if (result.kind === 'rows') {
      const rows = checked(result.rows)
      if (rows.length) sink.events(rows)
      sink.historical(rows)
    } else {
      sink.historical(null)
    }
    if (gen === generation) sink.connection(result.kind === 'error' ? NOT_FOUND : null)
  }

  const loadSummary = async (source: CallSource, gen: number) => {
    const result = await source.summary(callId).catch((): SummaryResult => ({ kind: 'error' }))
    if (stopped) return
    // A failed read moves nothing, so an older load's row still lands after a newer failure.
    if (result.kind !== 'error' && gen >= appliedSummaryGen) {
      appliedSummaryGen = gen
      if (result.kind === 'row') {
        sink.summary(result.row)
        sink.missing(false)
      } else {
        sink.missing(true)
      }
    }
    if (gen === generation) sink.detail(result.kind === 'error' ? SUMMARY_FAILED : null)
  }

  // Two independent reads: a slow one must not hold back the other's answer.
  const loadAll = (source: CallSource, gen: number) => {
    void loadEvents(source, gen)
    void loadSummary(source, gen)
  }

  void (async () => {
    const opened = await io.open()
    if (stopped) return
    if (opened.kind === 'failed') {
      sink.connection(opened.message)
      return
    }
    const signedIn = await io.signIn()
    if (stopped) return
    if (signedIn.kind === 'failed') {
      sink.connection(SIGN_IN_FAILED)
      return
    }
    const { source } = opened
    unsubscribe = source.subscribe(
      callId,
      (row) => {
        if (stopped) return
        const rows = checked([row])
        if (rows.length) sink.events(rows)
      },
      (status) => {
        if (stopped) return
        generation += 1
        if (status === 'SUBSCRIBED') {
          // The refresh is what closes the gap between the first snapshot and the socket
          // coming up (subscribing is asynchronous), and what catches up after a drop.
          sink.connection(null)
          loadAll(source, generation)
        } else if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') {
          sink.connection(RECONNECTING)
        }
      },
    )
    // Plain HTTP reads: they must arrive even if Realtime never connects.
    loadAll(source, 0)
  })()

  return () => {
    stopped = true
    unsubscribe?.()
    unsubscribe = null
  }
}
