import { fakeHistory } from '../history/fakeHistory'
import type { CallResult } from './types'

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
  },
}
