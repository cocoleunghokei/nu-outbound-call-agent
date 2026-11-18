import { ChatHome } from './precall/ChatHome'
import { fakeProfile, fakeTemplates } from './precall/fakeBriefs'
import { modeNote } from './precall/mode'
import { useDataMode } from './precall/useDataMode'

// API mode needs VITE_API_URL and a Supabase session; the session is created anonymously on
// first use, and the mode is re-picked whenever the signed-in user changes. See precall/mode.ts.
// The profile and templates are still fake in both modes (no profile or templates API yet).
export function PrecallDemo({ onCallStarted }: { onCallStarted: (callId: string | null) => void }) {
  const { mode, starter } = useDataMode(import.meta.env.VITE_API_URL)

  return (
    <ChatHome
      context={{ profile: fakeProfile, templates: fakeTemplates }}
      starter={starter}
      onCallStarted={onCallStarted}
      modeNote={modeNote(mode)}
    />
  )
}
