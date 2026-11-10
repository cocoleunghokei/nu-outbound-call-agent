import { useState } from 'react'
import { LiveCall } from './live-call/LiveCall'
import { DR_LEE_SCRIPT, MOCK_CALL_ID, mockSummary, useMockCallEvents } from './live-call/mockCall'

/**
 * Temporary: shows the live-call page playing a fake call until the app
 * layout and routing exist.
 */
function App() {
  const [startedAt] = useState(() => new Date().toISOString())
  const events = useMockCallEvents(DR_LEE_SCRIPT, MOCK_CALL_ID, startedAt)
  return <LiveCall summary={mockSummary(startedAt)} events={events} />
}

export default App
