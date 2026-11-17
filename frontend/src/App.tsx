import { PrecallDemo } from './PrecallDemo'
import { LiveCallDemo } from './LiveCallDemo'
import { HistoryDemo } from './HistoryDemo'
import { livePath, navigate, useRoute } from './router'

/** Picks the screen for the current address (see `router.ts` for the routes). */
function App() {
  const route = useRoute()
  switch (route.name) {
    case 'live':
      // Keyed by call, so opening another call starts a fresh live screen.
      return <LiveCallDemo key={route.callId ?? 'demo'} callId={route.callId} />
    case 'history':
      return <HistoryDemo />
    case 'home':
      return <PrecallDemo onCallStarted={(callId) => navigate(livePath(callId))} />
  }
}

export default App
