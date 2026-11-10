import { useEffect, useState } from 'react'
import { ChatHome } from './precall/ChatHome'
import { fakeProfile, fakeTemplates } from './precall/fakeBriefs'
import { modeNote, resolveDataMode, type DataMode } from './precall/mode'
import type { Starter } from './precall/start'

// Picked once at startup: API mode needs VITE_API_URL and a signed-in session,
// otherwise demo mode (fake data, Start only logs the brief). See precall/mode.ts.
const dataMode: Promise<DataMode> = resolveDataMode(import.meta.env.VITE_API_URL)

// Start waits for the mode, in case it is tapped before the session check ends.
const starter: Starter = async (brief, session) => (await dataMode).starter(brief, session)

// Temporary: shows the pre-call chat home until the app layout and routing exist.
// The profile and templates are still fake in both modes (no profile or templates API yet).
export function PrecallDemo() {
  const [mode, setMode] = useState<DataMode | null>(null)

  useEffect(() => {
    let live = true
    void dataMode.then((m) => live && setMode(m))
    return () => {
      live = false
    }
  }, [])

  return (
    <ChatHome
      context={{ profile: fakeProfile, templates: fakeTemplates }}
      starter={starter}
      modeNote={modeNote(mode)}
    />
  )
}
