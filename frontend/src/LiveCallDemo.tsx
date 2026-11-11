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
 * The live-call page for `/live?call=<call_id>` (see `router.ts`).
 * See liveCallMode() for when it uses the real backend; without a call id it plays the
 * scripted demo call.
 */
export function LiveCallDemo({ callId }: { callId: string | null }) {
  const [mode] = useState(() =>
    liveCallMode(
      import.meta.env.VITE_LIVE_CALL_SOURCE,
      import.meta.env.VITE_API_URL,
      callId ? `?call=${encodeURIComponent(callId)}` : '',
    ),
  )
  return mode.kind === 'real' ? (
    <RealLiveCall callId={mode.callId} apiBase={mode.apiBase} />
  ) : (
    <MockLiveCall />
  )
}
