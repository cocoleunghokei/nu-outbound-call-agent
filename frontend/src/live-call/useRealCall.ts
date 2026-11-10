/**
 * The live-call screen's data from the real backend (B4): the call's events
 * from Supabase (loaded once, then live through Realtime) and the reply and
 * command endpoints of the backend API.
 *
 * Returns the same shape as `useMockCall`, so `LiveCall` doesn't change.
 * The Supabase client is loaded only here, on first use, so the fake-call
 * mode never needs Supabase settings.
 */
import { useCallback, useEffect, useState } from 'react'
import type { SupabaseClient } from '@supabase/supabase-js'
import {
  CALL_EVENT_COLUMNS,
  postCommand,
  postReply,
  toCallEvent,
  toCallSummary,
} from './backend'
import type { SendCallCommand } from './controls'
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
}

async function loadClient(): Promise<SupabaseClient> {
  const { supabase } = await import('../lib/supabase')
  return supabase
}

async function accessToken(): Promise<string | null> {
  const { data } = await (await loadClient()).auth.getSession()
  return data.session?.access_token ?? null
}

/** Follows one call. `apiBase` is the backend URL (see `apiBase()` in backend.ts). */
export function useRealCall(callId: string, apiBase: string): RealCall {
  const [summary, setSummary] = useState<LiveCallSummary | null>(null)
  const [events, setEvents] = useState<CallEvent[]>([])
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let unsubscribe: (() => void) | null = null

    const take = (rows: unknown[]) => {
      const checked = rows.map(toCallEvent).filter((e): e is CallEvent => e !== null)
      if (!cancelled && checked.length) setEvents((held) => mergeEvents(held, checked))
    }

    void (async () => {
      let client: SupabaseClient
      try {
        client = await loadClient()
      } catch {
        if (!cancelled) setProblem('The app is missing its Supabase settings, so the call can’t load.')
        return
      }

      // Load (or reload after a reconnect) everything so far; merging by seq
      // makes overlap with live inserts harmless.
      const loadAll = async () => {
        const { data, error } = await client
          .from('call_events')
          .select(CALL_EVENT_COLUMNS)
          .eq('call_id', callId)
          .order('seq')
        if (cancelled) return
        if (error) setProblem('Couldn’t load the call. It may not exist, or you may be signed out.')
        else take(data ?? [])
      }

      // Subscribe first, then load, so nothing between the two is missed.
      // A unique name per subscription: React may start this effect twice, and
      // Supabase hands back an already-subscribed channel for a reused name.
      const channel = client
        .channel(`call-events:${callId}:${crypto.randomUUID()}`)
        .on(
          'postgres_changes',
          { event: 'INSERT', schema: 'public', table: 'call_events', filter: `call_id=eq.${callId}` },
          (change) => take([change.new]),
        )
        .subscribe((status) => {
          if (cancelled) return
          if (status === 'SUBSCRIBED') {
            setProblem(null)
            void loadAll()
          } else if (status === 'CHANNEL_ERROR' || status === 'TIMED_OUT') {
            setProblem('Live updates paused. Reconnecting…')
          }
        })
      unsubscribe = () => void client.removeChannel(channel)

      const { data: callRow } = await client
        .from('calls')
        .select('id, created_at, max_duration_s, briefs(business_name, phone_e164, goal)')
        .eq('id', callId)
        .maybeSingle()
      if (!cancelled) setSummary(toCallSummary(callRow))
    })()

    return () => {
      cancelled = true
      unsubscribe?.()
    }
  }, [callId])

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

  return { summary, events, sendReply, sendCommand, problem }
}
