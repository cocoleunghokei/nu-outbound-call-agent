import { useCallback, useState } from 'react'
import { ChatHome } from './precall/ChatHome'
import { EmailStep } from './precall/EmailStep'
import { fakeProfile, fakeTemplates } from './precall/fakeBriefs'
import { modeNote } from './precall/mode'
import { signInRequired, type Starter } from './precall/start'
import { useDataMode } from './precall/useDataMode'

// API mode needs VITE_API_URL and a Supabase session; the session is created anonymously on
// first use, and the mode is re-picked whenever the signed-in user changes. See precall/mode.ts.
// Placing a call needs an email-verified user (ON-1): Start while anonymous opens the email
// step instead, and the backend's 403 `sign_in_required` does the same if the mode is stale.
// The profile and templates are still fake in both modes (no profile or templates API yet).
export function PrecallDemo({ onCallStarted }: { onCallStarted: (callId: string | null) => void }) {
  const { mode, starter } = useDataMode(import.meta.env.VITE_API_URL)
  const [askEmail, setAskEmail] = useState(false)

  const anonymous = mode?.kind === 'api' && mode.account.anonymous
  const gatedStarter = useCallback<Starter>(
    async (brief, session) => {
      if (anonymous) {
        setAskEmail(true)
        return { result: signInRequired(), session }
      }
      return starter(brief, session)
    },
    [anonymous, starter],
  )

  return (
    <ChatHome
      context={{ profile: fakeProfile, templates: fakeTemplates }}
      starter={gatedStarter}
      onCallStarted={onCallStarted}
      onSignInRequired={() => setAskEmail(true)}
      modeNote={modeNote(mode)}
      overlay={askEmail && mode?.kind === 'api' && <EmailStep account={mode.account} onClose={() => setAskEmail(false)} />}
    />
  )
}
