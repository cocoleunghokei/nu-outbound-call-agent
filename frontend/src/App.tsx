import { PrecallDemo } from './PrecallDemo'
import { LiveCallDemo } from './LiveCallDemo'
import { HistoryDemo } from './HistoryDemo'

// Temporary screen switcher until the app layout and routing exist. Each section's demo
// screen lives in its own *Demo.tsx file; pick one with ?screen=<key>.
const screens = {
  precall: PrecallDemo,
  live: LiveCallDemo,
  history: HistoryDemo,
} as const

type ScreenKey = keyof typeof screens

function pickScreen(search: string): ScreenKey {
  const params = new URLSearchParams(search)
  const s = params.get('screen')
  if (s && s in screens) return s as ScreenKey
  if (!s && 'live' in screens && params.get('call')) return 'live'
  return 'precall'
}

function App() {
  const Screen = screens[pickScreen(window.location.search)]
  return <Screen />
}

export default App
