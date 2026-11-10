import { useState } from 'react'
import { LiveCall } from './live-call/LiveCall'
import { DR_LEE_SCRIPT, MOCK_CALL_ID, mockSummary, useMockCall } from './live-call/mockCall'

/**
 * Temporary: shows the live-call page playing a fake call until the app
 * layout and routing exist.
 */
export function LiveCallDemo() {
  const [startedAt] = useState(() => new Date().toISOString())
  const { events, sendReply, sendCommand } = useMockCall(DR_LEE_SCRIPT, MOCK_CALL_ID, startedAt)
  return (
    <LiveCall
      summary={mockSummary(startedAt)}
      events={events}
      sendReply={sendReply}
      sendCommand={sendCommand}
    />
  )
}
