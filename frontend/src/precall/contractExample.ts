import type { Brief } from './types'

/**
 * Verbatim copy of `contracts/examples/brief.v1.valid.json` from PR #19
 * (`feat/backend-foundation`). Tests compare the fake briefs against it.
 * Kept as a .ts file so no tsconfig change is needed for JSON imports.
 * Delete once #19 merges and import the real example instead.
 */
export const contractExampleBrief: Brief = {
  goal: 'Book a table for 4',
  business_name: 'Test Bistro',
  phone_e164: '+14155550123',
  template_key: 'restaurant_booking',
  task_line: 'Book a table for {party_size} under {name}.',
  task_vars: {
    party_size: { literal: '4' },
    name: { field_ref: 'calling_name' },
  },
  constraints: [{ text: 'Between 6pm and 8pm' }],
  preferences: [{ text: 'Window seat' }],
  fallbacks: [{ text: 'Friday same time' }],
  never_agree: [{ text: 'A deposit' }],
  share_freely_keys: ['calling_name', 'phone'],
  ask_first_keys: ['date_of_birth'],
}
