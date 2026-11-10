import { describe, expect, it } from 'vitest'
import {
  MAX_REPLY_LENGTH,
  allowsFreeText,
  closedMessage,
  parsePing,
  sanitizeReplyText,
  timeLeft,
} from './ping'

/** A valid `ping.opened` payload with fake data. */
function pingPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ping_id: 'p1',
    call_id: 'c1',
    kind: 'decision',
    question: 'They offered Thu 4pm',
    reason: 'You said before 3pm.',
    suggested_replies: ['Ask for Fri', 'Accept 4pm', 'Decline'],
    expires_at: '2026-11-10T15:00:45.000Z',
    remaining_ms: 45_000,
    state: 'open',
    ...overrides,
  }
}

describe('parsePing', () => {
  it('reads a valid ping', () => {
    expect(parsePing(pingPayload())).toMatchObject({ ping_id: 'p1', kind: 'decision' })
  })

  it('keeps field_key on a field approval', () => {
    const ping = parsePing(pingPayload({ kind: 'field_approval', field_key: 'date_of_birth' }))
    expect(ping?.field_key).toBe('date_of_birth')
  })

  it('accepts a paused ping (expires_at null)', () => {
    expect(parsePing(pingPayload({ expires_at: null, remaining_ms: 20_000 }))).not.toBeNull()
  })

  it.each([
    ['missing ping_id', { ping_id: undefined }],
    ['empty question', { question: '  ' }],
    ['unknown kind', { kind: 'survey' }],
    ['replies not strings', { suggested_replies: ['ok', 3] }],
    ['replies not an array', { suggested_replies: 'ok' }],
    ['bad expires_at', { expires_at: 12 }],
    ['bad remaining_ms', { remaining_ms: '45' }],
    ['bad field_key', { field_key: 1 }],
  ])('drops a malformed ping: %s', (_, overrides) => {
    expect(parsePing(pingPayload(overrides))).toBeNull()
  })
})

describe('allowsFreeText', () => {
  it('allows free text on decisions only, never on Ask-me-first fields (DC-6)', () => {
    expect(allowsFreeText(parsePing(pingPayload())!)).toBe(true)
    expect(allowsFreeText(parsePing(pingPayload({ kind: 'field_approval' }))!)).toBe(false)
  })
})

describe('timeLeft', () => {
  const now = Date.parse('2026-11-10T15:00:30.000Z')

  it('counts down to expires_at', () => {
    expect(timeLeft(parsePing(pingPayload())!, now)).toEqual({ ms: 15_000, paused: false })
  })

  it('never goes below zero', () => {
    expect(timeLeft(parsePing(pingPayload())!, now + 60_000).ms).toBe(0)
  })

  it('shows the frozen time while paused', () => {
    const ping = parsePing(pingPayload({ expires_at: null, remaining_ms: 20_000 }))!
    expect(timeLeft(ping, now)).toEqual({ ms: 20_000, paused: true })
  })

  it('treats an unreadable expires_at as no time left', () => {
    expect(timeLeft(parsePing(pingPayload({ expires_at: 'soon' }))!, now).ms).toBe(0)
  })
})

describe('sanitizeReplyText', () => {
  it('trims and collapses whitespace', () => {
    expect(sanitizeReplyText('  ask   for\n\tSaturday  ')).toBe('ask for Saturday')
  })

  it('strips control characters', () => {
    expect(sanitizeReplyText('ok\u0000\u0007\u009bgo')).toBe('ok go')
  })

  it(`caps the reply at ${MAX_REPLY_LENGTH} characters`, () => {
    expect(sanitizeReplyText('a'.repeat(500))).toHaveLength(MAX_REPLY_LENGTH)
  })

  it('returns an empty string when nothing is left', () => {
    expect(sanitizeReplyText(' \n\u0001 ')).toBe('')
  })
})

describe('closedMessage', () => {
  it('quotes the reply that was sent', () => {
    expect(closedMessage('decision', 'answered', 'Ask for Fri')).toBe('Sent: “Ask for Fri”')
  })

  it('says nothing was shared when an Ask-me-first ping times out', () => {
    expect(closedMessage('field_approval', 'expired', undefined)).toMatch(/nothing was shared/)
  })

  it('explains a timeout, a replaced question and an ended call', () => {
    expect(closedMessage('decision', 'expired', undefined)).toMatch(/fallback/)
    expect(closedMessage('decision', 'superseded', undefined)).toMatch(/replaced/)
    expect(closedMessage('decision', 'cancelled', undefined)).toMatch(/call ended/)
  })

  it('has a fallback for a state it does not know', () => {
    expect(closedMessage('decision', 'archived', undefined)).toBe('This question is closed.')
  })
})
