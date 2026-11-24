import { PrecallDemo } from './PrecallDemo'
import { LiveCallDemo } from './LiveCallDemo'
import { HistoryDemo } from './HistoryDemo'
import { livePath, navigate, useRoute, type Route } from './router'
import { AppShell } from './shell/AppShell'

/** The screen for a route (see `router.ts` for the routes). */
function Screen({ route }: { route: Route }) {
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

/** Picks the screen for the current address and puts it in the app layout with the sidebar. */
function App() {
  const route = useRoute()
  return (
    <AppShell route={route}>
      <Screen route={route} />
    </AppShell>
  )
}

export default App
