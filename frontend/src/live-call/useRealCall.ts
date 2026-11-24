/**
 * The live-call screen's data from the real backend (B4): the call's events
 * from Supabase (loaded once, then live through Realtime) and the reply and
 * command endpoints of the backend API.
 *
 * Returns the same shape as `useMockCall`, so `LiveCall` doesn't change.
 * The sequence itself (sign in, subscribe, load, reload on reconnect) is
 * `followCall` in `follow.ts`; this hook only builds its data source over the
 * shared Supabase client and keeps its results in state. The client is loaded
 * only here, on first use, so the fake-call mode never needs Supabase settings.
 */
import { useCallback, useEffect, useState } from 'react'
import type { SupabaseClient } from '@supabase/supabase-js'
import { CALL_EVENT_COLUMNS, postCommand, postReply, toCallSummary } from './backend'
import { accessToken, ensureSignedIn } from '../lib/accessToken'
import type { SendCallCommand } from './controls'
import { followCall, markHistory, problemOf, type CallSource, type FollowIo } from './follow'
import type { SendPingReply } from './ping'
import { mergeEvents } from './state'
import type { CallEvent, LiveCallSummary } from './types'

export interface RealCall {
  summary: LiveCallSummary | null
  events: CallEvent[]
  sendReply: SendPingReply
  sendCommand: SendCallCommand
  /** A plain-language problem to show above the call, or null. */
  problem: string | null
  /**
   * The highest `seq` in the first load of the call's events (0 if there were none),
   * or null until that load finishes. Events up to it happened before the page opened,
   * so the call-ended notification (PO-5) skips a call that had already ended.
   */
  historicalUpTo: number | null
}

/** The call's data over the shared client: the same three queries as before, behind `CallSource`. */
function sourceOver(client: SupabaseClient): CallSource {
  return {
    async events(callId) {
      const { data, error } = await client
        .from('call_events')
        .select(CALL_EVENT_COLUMNS)
        .eq('call_id', callId)
        .order('seq')
      if (error) return { kind: 'error' }
      return { kind: 'rows', rows: (data ?? []) as unknown[] }
    },
    async summary(callId) {
      const { data, error } = await client
        .from('calls')
        .select('id, created_at, max_duration_s, briefs(business_name, phone_e164, goal)')
        .eq('id', callId)
        .maybeSingle()
      if (error) return { kind: 'error' }
      // No row without an error: RLS shows this user no such call.
      return data === null ? { kind: 'none' } : { kind: 'row', row: data as unknown }
    },
    subscribe(callId, onInsert, onStatus) {
      // A unique name per subscription: React may start this effect twice, and
      // Supabase hands back an already-subscribed channel for a reused name.
      const channel = client
        .channel(`call-events:${callId}:${crypto.randomUUID()}`)
        .on(
          'postgres_changes',
          { event: 'INSERT', schema: 'public', table: 'call_events', filter: `call_id=eq.${callId}` },
          (change) => onInsert(change.new),
        )
        .subscribe((status) => onStatus(status))
      return () => void client.removeChannel(channel)
    },
  }
}

const io: FollowIo = {
  async open() {
    try {
      const { supabase } = await import('../lib/supabase')
      return { kind: 'ok', source: sourceOver(supabase) }
    } catch {
      return { kind: 'failed', message: 'The app is missing its Supabase settings, so the call can’t load.' }
    }
  },
  signIn: ensureSignedIn,
}

/** Follows one call. `apiBase` is the backend URL (see `apiBase()` in backend.ts). */
export function useRealCall(callId: string, apiBase: string): RealCall {
  const [summary, setSummary] = useState<LiveCallSummary | null>(null)
  const [events, setEvents] = useState<CallEvent[]>([])
  const [missing, setMissing] = useState(false)
  const [connection, setConnection] = useState<string | null>(null)
  const [detail, setDetail] = useState<string | null>(null)
  const [historicalUpTo, setHistoricalUpTo] = useState<number | null>(null)

  useEffect(
    () =>
      followCall(callId, io, {
        events: (rows) => setEvents((held) => mergeEvents(held, rows)),
        historical: (loaded) => setHistoricalUpTo((first) => markHistory(first, loaded)),
        summary: (row) => setSummary(toCallSummary(row)),
        missing: setMissing,
        connection: setConnection,
        detail: setDetail,
      }),
    [callId],
  )

  const sendReply = useCallback<SendPingReply>(
    async (id, pingId, reply) => {
      const token = await accessToken()
      if (!token) return { kind: 'error', message: 'You were signed out. Sign in and try again.' }
      return postReply(apiBase, token, id, pingId, reply)
    },
    [apiBase],
  )

  const sendCommand = useCallback<SendCallCommand>(
    async (id, command) => {
      const token = await accessToken()
      if (!token) return { kind: 'error', message: 'You were signed out. Sign in and try again.' }
      return postCommand(apiBase, token, id, command)
    },
    [apiBase],
  )

  return { summary, events, sendReply, sendCommand, problem: problemOf(missing, connection, detail), historicalUpTo }
}
