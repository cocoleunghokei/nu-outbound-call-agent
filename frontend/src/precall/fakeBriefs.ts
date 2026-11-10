/**
 * Fake data for building the pre-call screens before the backend is ready.
 * Shaped exactly like the PR #19 tables and the brief.v1 contract, so
 * connecting the real API later only swaps the data source.
 *
 * Profile keys follow the PR #19 seed (`calling_name`, `full_name`,
 * `date_of_birth`) plus the rest of ON-3. The official key list is still to
 * confirm with Steve.
 */
import type { BriefRecord, CallTemplate, ProfileField } from './types'

/** Alex, the PRD's example user. DOB and address default to Ask me first (ON-5). */
export const fakeProfile: ProfileField[] = [
  { key: 'full_name', value: 'Alex Chen', sharing: 'share_freely' },
  // Always shared: the opening line says it (DC-1, ON-5).
  { key: 'calling_name', value: 'Alex', sharing: 'share_freely' },
  { key: 'phone', value: '+16175550142', sharing: 'share_freely' },
  { key: 'email', value: 'alex.chen@example.com', sharing: 'share_freely' },
  { key: 'date_of_birth', value: '2004-03-15', sharing: 'ask_first' },
  { key: 'address', value: '123 Example St, Boston, MA', sharing: 'ask_first' },
]

/** The two P0 templates (PC-2). Draft format until agreed with Harper. */
export const fakeTemplates: CallTemplate[] = [
  {
    key: 'restaurant_booking',
    label: 'Restaurant: book a table',
    task_line: "I'd like to book a table for {party_size} on {day} at {time}.",
    required_fields: [
      { key: 'calling_name', source: 'profile', question: 'What name should the booking be under?' },
      { key: 'phone', source: 'profile', question: "What's the best number for the reservation?" },
      { key: 'party_size', source: 'call', question: 'How many people?' },
      { key: 'day', source: 'call', question: 'Which day?' },
      { key: 'time', source: 'call', question: 'What time?' },
    ],
    likely_questions: [
      'Name for the reservation?',
      'Phone number?',
      'Any dietary needs or a special occasion?',
      'Indoor or outdoor seating?',
    ],
    default_constraints: [],
  },
  {
    key: 'doctor_booking',
    label: 'Doctor: book an appointment',
    task_line: "I'd like to book an appointment with {provider_name}.",
    required_fields: [
      { key: 'calling_name', source: 'profile', question: 'Who is the appointment for?' },
      { key: 'date_of_birth', source: 'profile', question: "What's your date of birth?" },
      { key: 'provider_name', source: 'call', question: 'Which doctor?' },
      { key: 'patient_status', source: 'call', question: 'Are you a new or existing patient?' },
      { key: 'reason', source: 'call', question: "What's the visit for?" },
    ],
    likely_questions: [
      'Date of birth?',
      'New or existing patient?',
      'Insurance carrier and member ID?',
      'Reason for the visit?',
    ],
    default_constraints: [],
  },
]

/** Example briefs, one per flow. */
export const fakeBriefRecords: BriefRecord[] = [
  // Restaurant, complete and ready for review (PC-6).
  {
    id: '00000000-0000-4000-8000-000000000001',
    goal: 'Book a table for 6 at 7pm on Friday',
    business_name: 'Corner Table Bistro',
    phone_e164: '+16175550100',
    template_key: 'restaurant_booking',
    brief: {
      goal: 'Book a table for 6 at 7pm on Friday',
      business_name: 'Corner Table Bistro',
      phone_e164: '+16175550100',
      template_key: 'restaurant_booking',
      task_line: "I'd like to book a table for {party_size} on {day} at {time}.",
      task_vars: {
        party_size: { literal: '6' },
        day: { literal: 'Friday' },
        time: { literal: '7pm' },
      },
      constraints: [{ text: 'Between 6:30pm and 8pm' }],
      preferences: [{ text: 'Window seat' }],
      fallbacks: [{ text: 'Saturday same time' }, { text: 'Friday at 8:30pm' }],
      never_agree: [{ text: 'A deposit' }],
      share_freely_keys: ['calling_name', 'phone'],
      ask_first_keys: [],
    },
    missing_fields: [],
    created_at: '2026-11-10T15:00:00Z',
  },
  // Doctor, still missing info the chat must ask for (PC-4).
  {
    id: '00000000-0000-4000-8000-000000000002',
    goal: 'Book a check-up with Dr. Lee before 3pm',
    business_name: 'Back Bay Family Clinic',
    phone_e164: '+16175550111',
    template_key: 'doctor_booking',
    brief: {
      goal: 'Book a check-up with Dr. Lee before 3pm',
      business_name: 'Back Bay Family Clinic',
      phone_e164: '+16175550111',
      template_key: 'doctor_booking',
      task_line: "I'd like to book an appointment with {provider_name}.",
      task_vars: {
        provider_name: { literal: 'Dr. Lee' },
      },
      constraints: [{ text: 'Before 3pm' }],
      preferences: [{ text: 'Thursday' }],
      fallbacks: [],
      never_agree: [],
      share_freely_keys: ['calling_name'],
      // DOB is Ask me first: the agent pings before sharing it (DC-6).
      ask_first_keys: ['date_of_birth'],
    },
    // patient_status and reason aren't known yet. No backup is fine: PC-5 only
    // prompts for one, so an empty fallbacks list never blocks the call.
    missing_fields: ['patient_status', 'reason'],
    created_at: '2026-11-10T15:05:00Z',
  },
  // Custom call (PC-3): no template.
  {
    id: '00000000-0000-4000-8000-000000000003',
    goal: 'Ask if they can hold a cake for pickup tomorrow',
    business_name: 'Sunrise Bakery',
    phone_e164: '+16175550122',
    template_key: null,
    brief: {
      goal: 'Ask if they can hold a cake for pickup tomorrow',
      business_name: 'Sunrise Bakery',
      phone_e164: '+16175550122',
      template_key: null,
      task_line: "I'm calling to ask if you can hold a cake for pickup {pickup_day}.",
      task_vars: {
        pickup_day: { literal: 'tomorrow' },
      },
      constraints: [{ text: 'Pickup before 6pm' }],
      preferences: [{ text: 'Chocolate' }],
      fallbacks: [{ text: 'Any cake they have ready' }],
      never_agree: [{ text: 'Paying in advance' }],
      share_freely_keys: ['calling_name', 'phone'],
      ask_first_keys: [],
    },
    missing_fields: [],
    created_at: '2026-11-10T15:10:00Z',
  },
]
