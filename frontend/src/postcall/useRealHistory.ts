/**
 * The history screen's data from the real backend: the call list, and the
 * result of the call that is open. The Supabase client (for the user's access
 * token) is loaded only here, on first use, so fake mode never needs it.
 */
import { useEffect, useState } from 'react'
import type { CallHistoryItem } from '../history/types'
import type { CallResult } from '../result/types'
import { fetchCallResult, fetchHistory, type Loaded } from './api'

const SIGNED_OUT: Loaded<never> = { ok: false, message: 'Sign in to see your calls.' }

async function accessToken(): Promise<string | null> {
  try {
    const { supabase } = await import('../lib/supabase')
    const { data } = await supabase.auth.getSession()
    return data.session?.access_token ?? null
  } catch {
    return null // Supabase settings missing: same as signed out
  }
}

export interface RealHistory {
  /** Null while loading. */
  calls: Loaded<CallHistoryItem[]> | null
  /** The open call's result; null while loading or when no call is open. */
  result: Loaded<CallResult> | null
}

export function useRealHistory(apiBase: string, openCallId: string | null): RealHistory {
  const [calls, setCalls] = useState<Loaded<CallHistoryItem[]> | null>(null)
  const [loadedResult, setLoadedResult] = useState<{ callId: string; result: Loaded<CallResult> } | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const token = await accessToken()
      const loaded = token ? await fetchHistory(apiBase, token) : SIGNED_OUT
      if (!cancelled) setCalls(loaded)
    })()
    return () => {
      cancelled = true
    }
  }, [apiBase])

  useEffect(() => {
    if (openCallId === null) return
    let cancelled = false
    void (async () => {
      const token = await accessToken()
      const result = token ? await fetchCallResult(apiBase, token, openCallId) : SIGNED_OUT
      if (!cancelled) setLoadedResult({ callId: openCallId, result })
    })()
    return () => {
      cancelled = true
    }
  }, [apiBase, openCallId])

  // A result loaded for a different call than the open one counts as still loading.
  const result = openCallId !== null && loadedResult?.callId === openCallId ? loadedResult.result : null
  return { calls, result }
}
