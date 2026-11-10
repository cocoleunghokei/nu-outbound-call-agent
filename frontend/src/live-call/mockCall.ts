/**
 * A scripted fake call, so the live-call screen can be built before the
 * backend is ready. It plays the PRD's Dr. Lee reschedule scenario as the same
 * `call_events` rows Supabase Realtime will deliver. All names and numbers are
 * fake.
 *
 * To connect the real backend, replace `useMockCallEvents` with a hook that
 * loads the call's events and subscribes to new ones; the screen stays the same.
 */
import { useEffect, useState } from 'react'
import type { CallEvent, LiveCallSummary } from './types'

export const MOCK_CALL_ID = '00000000-0000-4000-8000-000000000001'

/** Header data for the fake call, started at `createdAt` (ISO 8601). */
export function mockSummary(createdAt: string): LiveCallSummary {
  return {
    callId: MOCK_CALL_ID,
    businessName: 'Brookline Family Medicine',
    phoneE164: '+16175550142',
    goal: 'Move my Thursday appointment with Dr. Lee to before 3pm',
    createdAt,
    maxDurationS: 900,
  }
}

/** One step of the script: wait `delayMs`, then emit an event of `type`. */
export interface ScriptStep {
  delayMs: number
  type: string
  payload: Record<string, unknown>
}

const status = (delayMs: number, value: string): ScriptStep => ({
  delayMs,
  type: 'status.changed',
  payload: { status: value },
})

const line = (delayMs: number, role: 'agent' | 'business', text: string): ScriptStep => ({
  delayMs,
  type: 'transcript.final',
  payload: { role, text, source_start_ts: null },
})

/**
 * The Dr. Lee scenario. The two waits are where the agent pings the user;
 * the ping card itself comes in a later PR, so the script carries on alone.
 */
export const DR_LEE_SCRIPT: readonly ScriptStep[] = [
  status(0, 'dialing'),
  status(1500, 'talking'),
  line(700, 'business', 'Brookline Family Medicine, this is Dana.'),
  line(
    1300,
    'agent',
    "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. I'd like to move Alex's Thursday appointment with Dr. Lee to an earlier time.",
  ),
  line(2000, 'business', "Sure. Can I get the patient's date of birth?"),
  line(1000, 'agent', 'Let me check with Alex, one moment.'),
  status(300, 'waiting_on_you'),
  status(4000, 'talking'),
  line(600, 'agent', 'Thanks for holding. The date of birth is March 4th, 2004.'),
  line(1800, 'business', 'Thanks. The earliest I have with Dr. Lee is Thursday at 4pm.'),
  line(900, 'agent', 'Let me check with Alex, one moment.'),
  status(300, 'waiting_on_you'),
  status(4000, 'talking'),
  line(800, 'agent', "Thanks for waiting. Alex can't make 4pm. Do you have anything Friday morning?"),
  line(1800, 'business', 'Let me look. Friday at 10am is open.'),
  line(700, 'agent', 'Friday at 10am works. Please book it.'),
  line(
    1700,
    'business',
    "Done. Thursday's appointment is cancelled and Friday at 10am is confirmed. The confirmation number is 4821.",
  ),
  line(900, 'agent', 'Thank you, Dana. Have a good day.'),
  status(1200, 'ended'),
  { delayMs: 100, type: 'call.ended', payload: { outcome: 'rescheduled', ended_reason: 'agent_hung_up' } },
]

/** Turns script steps into numbered events, as the backend would store them. */
export function scriptToEvents(
  script: readonly ScriptStep[],
  callId: string,
  startedAt: Date,
): CallEvent[] {
  let elapsedMs = 0
  return script.map((step, index) => {
    elapsedMs += step.delayMs
    const seq = index + 1
    return {
      event_id: `${callId}-${seq}`,
      call_id: callId,
      seq,
      type: step.type,
      occurred_at: new Date(startedAt.getTime() + elapsedMs).toISOString(),
      source: step.type === 'transcript.final' ? 'vapi' : 'backend',
      schema_version: 1,
      payload: step.payload,
    }
  })
}

/**
 * Plays `script` in real time from `startedAt` (ISO 8601) and returns the
 * events emitted so far. A new `startedAt` restarts the call from the top.
 */
export function useMockCallEvents(
  script: readonly ScriptStep[],
  callId: string,
  startedAt: string,
): CallEvent[] {
  const [played, setPlayed] = useState<{ startedAt: string; events: CallEvent[] }>({
    startedAt,
    events: [],
  })

  useEffect(() => {
    const start = new Date(startedAt)
    const all = scriptToEvents(script, callId, start)
    const timers = all.map((event) =>
      window.setTimeout(
        () => {
          setPlayed((prev) =>
            prev.startedAt === startedAt
              ? { startedAt, events: [...prev.events, event] }
              : { startedAt, events: [event] },
          )
        },
        Math.max(0, new Date(event.occurred_at).getTime() - Date.now()),
      ),
    )
    return () => timers.forEach((timer) => window.clearTimeout(timer))
  }, [script, callId, startedAt])

  return played.startedAt === startedAt ? played.events : []
}
