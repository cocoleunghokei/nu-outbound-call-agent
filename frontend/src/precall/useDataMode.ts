/**
 * The pre-call screen's data mode, kept current.
 *
 * `resolveDataMode` runs on mount and again whenever the signed-in user changes
 * (`onSignInChange`), so the anonymous sign-in that `loadSupabaseTokenSource` performs, or a
 * later sign-out, moves the screen between demo and API mode without a reload. `starter`
 * always waits for the latest resolution, in case Start is tapped before it finishes.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { onSignInChange } from '../lib/accessToken'
import { resolveDataMode, type DataMode } from './mode'
import type { Starter } from './start'

export function useDataMode(apiUrl: string | undefined): { mode: DataMode | null; starter: Starter } {
  const [mode, setMode] = useState<DataMode | null>(null)
  const latest = useRef<Promise<DataMode> | null>(null)

  useEffect(() => {
    let live = true
    let stop: () => void = () => {}
    const pick = () => {
      const pending = resolveDataMode(apiUrl)
      latest.current = pending
      void pending.then((picked) => {
        if (live && latest.current === pending) setMode(picked)
      })
    }
    pick()
    void onSignInChange(pick).then((unsubscribe) => {
      if (live) stop = unsubscribe
      else unsubscribe()
    })
    return () => {
      live = false
      stop()
    }
  }, [apiUrl])

  const starter = useCallback<Starter>(
    async (brief, session) => {
      const pending = latest.current ?? resolveDataMode(apiUrl)
      latest.current = pending
      return (await pending).starter(brief, session)
    },
    [apiUrl],
  )

  return { mode, starter }
}
