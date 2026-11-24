import { useCallback, useState } from 'react'
import { isOnboarded } from './lib/profile'
import { ChatHome } from './precall/ChatHome'
import { fakeProfile, fakeTemplates } from './precall/fakeBriefs'
import { modeNote } from './precall/mode'
import { OnboardingStep } from './precall/OnboardingStep'
import { profileRequired, type Starter } from './precall/start'
import { useDataMode } from './precall/useDataMode'
import { useProfile } from './precall/useProfile'

// API mode needs VITE_API_URL and a Supabase session; the session is created anonymously on
// first use, and the mode is re-picked whenever the signed-in user changes. See precall/mode.ts.
// In API mode the chat runs on the user's real profile, collected once by the onboarding form
// on the first visit (ON-3, ON-8); the server checks each brief against the same rows. Start
// before the form is saved opens it instead. Templates are still fake (no templates API yet).
export function PrecallDemo({ onCallStarted }: { onCallStarted: (callId: string | null) => void }) {
  const { mode, starter } = useDataMode(import.meta.env.VITE_API_URL)
  const { profile, setProfile } = useProfile(mode)
  const [askProfile, setAskProfile] = useState(false)

  const needsProfile = mode?.kind === 'api' && profile !== null && !isOnboarded(profile)
  const gatedStarter = useCallback<Starter>(
    async (brief, session) => {
      if (needsProfile) {
        setAskProfile(true)
        return { result: profileRequired(), session }
      }
      return starter(brief, session)
    },
    [needsProfile, starter],
  )

  if (mode?.kind === 'demo') {
    return (
      <ChatHome
        context={{ profile: fakeProfile, templates: fakeTemplates }}
        starter={starter}
        onCallStarted={onCallStarted}
        modeNote={modeNote(mode)}
      />
    )
  }
  if (!mode || !profile) {
    return (
      <section className="precall-chat" aria-label="Chat" aria-busy="true">
        <p className="precall-small precall-loading">Loading…</p>
      </section>
    )
  }
  const showForm = !isOnboarded(profile) || askProfile
  return (
    // Keyed on the save time: the chat builds its state from the profile once, so a saved
    // profile starts it fresh (the form is shown before anything is typed).
    <ChatHome
      key={profile.onboardedAt ?? 'new'}
      context={{ profile: profile.fields, templates: fakeTemplates }}
      starter={gatedStarter}
      onCallStarted={onCallStarted}
      onProfileRequired={() => setAskProfile(true)}
      modeNote={modeNote(mode, profile)}
      overlay={
        showForm && (
          <OnboardingStep
            userId={mode.account.userId}
            initial={profile.fields}
            onSaved={(saved) => {
              setProfile(saved)
              setAskProfile(false)
            }}
          />
        )
      }
    />
  )
}
