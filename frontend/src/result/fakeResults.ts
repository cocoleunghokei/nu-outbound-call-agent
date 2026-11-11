import { fakeHistory } from '../history/fakeHistory'
import type { TranscriptLine } from './transcript'
import type { CallResult } from './types'

/** Builds fake transcript lines, `seconds` after `start`, in the order given. */
function lines(start: string, said: Array<[TranscriptLine['role'], number, string]>): TranscriptLine[] {
  const t0 = Date.parse(start)
  return said.map(([role, seconds, text], i) => ({
    seq: i + 1,
    role,
    text,
    startedAt: new Date(t0 + seconds * 1000).toISOString(),
  }))
}

/**
 * Fake end-of-call results for the calls in `fakeHistory`, keyed by call id.
 * Everything here is made up.
 */
export const fakeResults: Record<string, CallResult> = {
  [fakeHistory[0].id]: {
    call: fakeHistory[0],
    businessConfirmed: false,
    details: {},
    openItems: ['They only take reservations for 8 or more. Call again with a walk-in time, or try another place.'],
    decisions: [
      {
        situation: 'Parties under 8 are walk-in only',
        action: 'end_call',
        reason: 'No reply in 45 seconds, and no fallback matched.',
      },
    ],
    sharedFields: [{ fieldKey: 'calling_name', state: 'spoken' }],
    transcript: lines(fakeHistory[0].createdAt, [
      ['business', 0, 'Tatte Charles Street, how can I help?'],
      ['agent', 3, 'Hi, I’m an AI assistant calling on behalf of Alex. This call is transcribed. Could you hold a table for 6 at 7pm on Friday?'],
      ['business', 11, 'We only take reservations for parties of 8 or more. Smaller groups are walk-in.'],
      ['agent', 16, 'Let me check with Alex, one moment.'],
      ['agent', 61, 'Thanks for holding. I’ll check with Alex and call back. Thank you for your time. Goodbye.'],
    ]),
  },
  [fakeHistory[1].id]: {
    call: fakeHistory[1],
    businessConfirmed: true,
    details: { date: '2026-10-24', time: '19:30', staffName: 'Marco' },
    openItems: [],
    decisions: [
      {
        situation: '7pm is full, 7:30 is open',
        action: 'take_fallback',
        reason: 'Matched your fallback: 7:30pm if 7 is full.',
      },
    ],
    sharedFields: [
      { fieldKey: 'calling_name', state: 'spoken' },
      { fieldKey: 'phone', state: 'spoken' },
    ],
    transcript: lines(fakeHistory[1].createdAt, [
      ['business', 0, 'Juniper Ramen.'],
      ['agent', 2, 'Hi, I’m an AI assistant calling on behalf of Alex. This call is transcribed. Do you have a table for 2 at 7pm Saturday?'],
      ['business', 9, '7 is full, but 7:30 is open.'],
      ['agent', 13, '7:30 works, thank you. The name is Alex, and the number is 617-555-0142.'],
      ['business', 20, 'Got it, Alex, party of 2 at 7:30 Saturday. I’m Marco, see you then.'],
      ['agent', 26, 'Thank you, Marco. Goodbye.'],
    ]),
  },
  [fakeHistory[2].id]: {
    call: fakeHistory[2],
    businessConfirmed: true,
    details: {},
    openItems: ['The office wants to talk to you directly. Call them yourself at (617) 555-0160.'],
    decisions: [
      {
        situation: 'They don’t book with automated callers',
        action: 'end_call',
        reason: 'They wouldn’t continue with an AI assistant.',
      },
    ],
    sharedFields: [],
    transcript: lines(fakeHistory[2].createdAt, [
      ['business', 0, 'Back Bay Dental.'],
      ['agent', 2, 'Hi, I’m an AI assistant calling on behalf of Alex. This call is transcribed. I’d like to book a cleaning.'],
      ['business', 8, 'Sorry, we don’t book with automated callers.'],
      ['agent', 12, 'Understood. I apologize for the trouble. Goodbye.'],
    ]),
  },
  [fakeHistory[3].id]: {
    call: fakeHistory[3],
    businessConfirmed: true,
    details: { date: '2026-10-23', time: '20:00', confirmationNumber: 'EP-5521', staffName: 'Jen' },
    openItems: [],
    decisions: [
      {
        situation: 'They confirmed the cancellation',
        action: 'accept',
        reason: 'Matched your goal. No fee was mentioned.',
      },
    ],
    sharedFields: [{ fieldKey: 'calling_name', state: 'spoken' }],
    transcript: lines(fakeHistory[3].createdAt, [
      ['business', 0, 'Eastside Pizza.'],
      ['agent', 2, 'Hi, I’m an AI assistant calling on behalf of Alex. This call is transcribed. I need to cancel a reservation for 4 on Friday at 8.'],
      ['business', 10, 'Got it, that’s cancelled. Your confirmation is EP-5521. This is Jen.'],
      ['agent', 16, 'Thank you, Jen. Goodbye.'],
    ]),
  },
}
