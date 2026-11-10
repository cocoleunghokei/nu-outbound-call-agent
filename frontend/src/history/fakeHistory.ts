import type { CallHistoryItem } from './types'

/**
 * Fake calls for building the history screen before the backend endpoint exists.
 * Every name and number is made up; 555-01xx numbers are reserved for fiction.
 */
export const fakeHistory: CallHistoryItem[] = [
  {
    id: '7d4c1f0e-1a2b-4c3d-8e9f-000000000001',
    businessName: 'Tatte Bakery & Cafe',
    phoneE164: '+16175550133',
    goal: 'Hold a table for 6 at 7pm Friday',
    templateKey: 'restaurant_book',
    status: 'ended',
    outcome: 'needs_follow_up',
    createdAt: '2026-10-26T18:04:00-04:00',
    endedAt: '2026-10-26T18:06:10-04:00',
  },
  {
    id: '7d4c1f0e-1a2b-4c3d-8e9f-000000000002',
    businessName: 'Juniper Ramen',
    phoneE164: '+16175550177',
    goal: 'Book a table for 2 on Saturday',
    templateKey: 'restaurant_book',
    status: 'ended',
    outcome: 'booked',
    createdAt: '2026-10-22T12:30:00-04:00',
    endedAt: '2026-10-22T12:31:45-04:00',
  },
  {
    id: '7d4c1f0e-1a2b-4c3d-8e9f-000000000003',
    businessName: 'Back Bay Dental',
    phoneE164: '+16175550160',
    goal: 'Book a cleaning next week',
    templateKey: 'doctor_book',
    status: 'ended',
    outcome: 'refused_ai',
    createdAt: '2026-10-20T10:15:00-04:00',
    endedAt: '2026-10-20T10:15:50-04:00',
  },
  {
    id: '7d4c1f0e-1a2b-4c3d-8e9f-000000000004',
    businessName: 'Eastside Pizza Co.',
    phoneE164: '+16175550121',
    goal: 'Cancel Friday’s reservation for 4',
    templateKey: 'restaurant_cancel',
    status: 'ended',
    outcome: 'cancelled',
    createdAt: '2026-10-19T16:40:00-04:00',
    endedAt: '2026-10-19T16:41:20-04:00',
  },
]
