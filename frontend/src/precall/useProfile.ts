/**
 * The signed-in user's profile, loaded once per user in API mode.
 *
 * Demo mode has no real profile, so `profile` stays null there. In API mode it is null
 * while loading, then the stored profile (possibly empty and not onboarded). After the
 * onboarding form saves, `setProfile` replaces it without a reload.
 */
import { useEffect, useState } from 'react'
import { loadProfile, loadSupabaseProfileStore, type LoadedProfile } from '../lib/profile'
import type { DataMode } from './mode'

export function useProfile(mode: DataMode | null): {
  profile: LoadedProfile | null
  setProfile: (profile: LoadedProfile) => void
} {
  const userId = mode?.kind === 'api' ? mode.account.userId : null
  // Kept with the user it belongs to, so a change of user reads as "not loaded" at once.
  const [loaded, setLoaded] = useState<{ userId: string; profile: LoadedProfile } | null>(null)

  useEffect(() => {
    if (!userId) return
    let live = true
    void loadSupabaseProfileStore()
      .then((store) => loadProfile(store, userId))
      .then((profile) => {
        if (live) setLoaded({ userId, profile })
      })
    return () => {
      live = false
    }
  }, [userId])

  const profile = userId && loaded?.userId === userId ? loaded.profile : null
  const setProfile = (next: LoadedProfile) => {
    if (userId) setLoaded({ userId, profile: next })
  }
  return { profile, setProfile }
}
