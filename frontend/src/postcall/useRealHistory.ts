/**
 * The history screen's data from the real backend: the call list, and the
 * result of the call that is open. The Supabase client (for the user's access
 * token) is loaded only here, on first use, so fake mode never needs it.
 */
import { useCallback, useEffect, useState } from 'react'
import type { CallHistoryItem } from '../history/types'
import { onSignInChange, signedInAccessToken } from '../lib/accessToken'
import type { CallResult } from '../result/types'
import { fetchCallResult, fetchHistory, type Loaded } from './api'

/** There is no sign-in screen: the anonymous sign-in itself failed, or Supabase isn't configured. */
const SIGNED_OUT: Loaded<never> = {
  ok: false,
  message: "I couldn't sign this browser in, so I can't load your calls. Try again.",
}

/**
 * The access token, signing the browser in anonymously first when it has none (a browser
 * whose first page is `/history` never visited the chat home, which otherwise does it).
 * Null when the sign-in fails or Supabase isn't configured: same as signed out.
 */
const tokenOrNull = signedInAccessToken

export interface RealHistory {
  /** Null while loading. */
  calls: Loaded<CallHistoryItem[]> | null
  /** The open call's result; null while loading or when no call is open. */
  result: Loaded<CallResult> | null
  /** Loads the list (and the open call) again, e.g. from a "Try again" button. */
  retry: () => void
}

/**
 * Loads the signed-in user's calls (`GET /calls`) and, when `openCallId` is set, that
 * call's result (`GET /calls/{id}/result`) from `apiBase`.
 *
 * Both load again on `retry()` and when the user signs in or out, so a screen opened
 * signed out or during a network blip doesn't need a page reload. On a fresh browser the
 * first load's own sign-in is such a change, so the loads run once more; the second pass
 * finds the session (`ensureSignedIn` is single-flight and reuses it) and signs nothing in.
 */
export function useRealHistory(apiBase: string, openCallId: string | null): RealHistory {
  const [loadedCalls, setLoadedCalls] = useState<{ attempt: number; calls: Loaded<CallHistoryItem[]> } | null>(null)
  const [loadedResult, setLoadedResult] = useState<{
    callId: string
    attempt: number
    result: Loaded<CallResult>
  } | null>(null)
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback(() => setAttempt((n) => n + 1), [])

  useEffect(() => {
    let unsubscribe: (() => void) | null = null
    let cancelled = false
    void onSignInChange(retry).then((stop) => {
      if (cancelled) stop()
      else unsubscribe = stop
    })
    return () => {
      cancelled = true
      unsubscribe?.()
    }
  }, [retry])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const token = await tokenOrNull()
      const loaded = token ? await fetchHistory(apiBase, token) : SIGNED_OUT
      if (!cancelled) setLoadedCalls({ attempt, calls: loaded })
    })()
    return () => {
      cancelled = true
    }
  }, [apiBase, attempt])

  useEffect(() => {
    if (openCallId === null) return
    let cancelled = false
    void (async () => {
      const token = await tokenOrNull()
      const result = token ? await fetchCallResult(apiBase, token, openCallId) : SIGNED_OUT
      if (!cancelled) setLoadedResult({ callId: openCallId, attempt, result })
    })()
    return () => {
      cancelled = true
    }
  }, [apiBase, openCallId, attempt])

  // A list or result loaded before the latest retry (or for another call) counts as still
  // loading, so "Try again" shows "Loading" until the new answer arrives.
  const calls = loadedCalls?.attempt === attempt ? loadedCalls.calls : null
  const result =
    openCallId !== null && loadedResult?.callId === openCallId && loadedResult.attempt === attempt
      ? loadedResult.result
      : null
  return { calls, result, retry }
}
