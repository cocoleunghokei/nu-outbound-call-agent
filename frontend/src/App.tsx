import { useState } from 'react'
import { LiveCall } from './live-call/LiveCall'
import { liveCallMode } from './live-call/backend'
import { DR_LEE_SCRIPT, MOCK_CALL_ID, mockSummary, useMockCall } from './live-call/mockCall'
import { useRealCall } from './live-call/useRealCall'

/** The scripted fake call (the default until the backend is deployed). */
function MockLiveCall() {
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

/** A real call, followed through Supabase and controlled through the backend API. */
function RealLiveCall({ callId, apiBase }: { callId: string; apiBase: string }) {
  const { summary, events, sendReply, sendCommand, problem } = useRealCall(callId, apiBase)
  return (
    <LiveCall
      summary={
        summary ?? {
          callId,
          businessName: 'Loading call…',
          phoneE164: '',
          goal: '',
          createdAt: new Date().toISOString(),
          maxDurationS: 900,
        }
      }
      events={events}
      sendReply={sendReply}
      sendCommand={sendCommand}
      notice={problem}
    />
  )
}

/**
 * Temporary: shows the live-call page until the app layout and routing exist.
 * See liveCallMode() for when it uses the real backend.
 */
function App() {
  const [mode] = useState(() =>
    liveCallMode(
      import.meta.env.VITE_LIVE_CALL_SOURCE,
      import.meta.env.VITE_API_URL,
      window.location.search,
    ),
  )
  return mode.kind === 'real' ? (
    <RealLiveCall callId={mode.callId} apiBase={mode.apiBase} />
  ) : (
    <MockLiveCall />
  )
}

export default App
