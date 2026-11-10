import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  apiBase,
  commandResultFrom,
  liveCallMode,
  postCommand,
  postReply,
  replyResultFrom,
  toCallEvent,
  toCallSummary,
} from './backend'

const CALL_ID = '00000000-0000-4000-8000-0000000000aa'

/** A `call_events` row as Supabase returns it, with fake data. */
function row(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    event_id: 'e1',
    call_id: CALL_ID,
    seq: 1,
    type: 'status.changed',
    occurred_at: '2026-11-10T15:00:00Z',
    source: 'backend',
    dedup_key: null,
    schema_version: 1,
    payload: { status: 'talking' },
    ...overrides,
  }
}

describe('toCallEvent', () => {
  it('reads a valid row', () => {
    expect(toCallEvent(row())).toEqual({
      event_id: 'e1',
      call_id: CALL_ID,
      seq: 1,
      type: 'status.changed',
      occurred_at: '2026-11-10T15:00:00Z',
      source: 'backend',
      schema_version: 1,
      payload: { status: 'talking' },
    })
  })

  it('keeps an event type it does not know (open enum)', () => {
    expect(toCallEvent(row({ type: 'some.future.event' }))?.type).toBe('some.future.event')
  })

  it('drops an unknown source rather than the whole row', () => {
    expect(toCallEvent(row({ source: 'martian' }))).not.toHaveProperty('source')
  })

  it.each([
    ['not an object', 'nope'],
    ['null', null],
    ['missing event_id', row({ event_id: undefined })],
    ['seq not a whole number', row({ seq: 1.5 })],
    ['seq zero', row({ seq: 0 })],
    ['empty type', row({ type: '' })],
    ['other schema version', row({ schema_version: 2 })],
    ['payload is an array', row({ payload: [] })],
    ['payload missing', row({ payload: null })],
  ])('drops a malformed row: %s', (_, raw) => {
    expect(toCallEvent(raw)).toBeNull()
  })
})

describe('toCallSummary', () => {
  const callRow = {
    id: CALL_ID,
    created_at: '2026-11-10T15:00:00Z',
    max_duration_s: 900,
    briefs: { business_name: 'Brookline Family Medicine', phone_e164: '+16175550142', goal: 'Move my appointment' },
  }

  it('reads a call with its brief', () => {
    expect(toCallSummary(callRow)).toEqual({
      callId: CALL_ID,
      businessName: 'Brookline Family Medicine',
      phoneE164: '+16175550142',
      goal: 'Move my appointment',
      createdAt: '2026-11-10T15:00:00Z',
      maxDurationS: 900,
    })
  })

  it('accepts the brief as a one-item list too', () => {
    expect(toCallSummary({ ...callRow, briefs: [callRow.briefs] })?.businessName).toBe(
      'Brookline Family Medicine',
    )
  })

  it('returns null without a brief or for a missing call', () => {
    expect(toCallSummary({ ...callRow, briefs: null })).toBeNull()
    expect(toCallSummary(null)).toBeNull()
  })
})

describe('replyResultFrom', () => {
  it('maps 200 to accepted', () => {
    expect(replyResultFrom(200, { state: 'answered' })).toEqual({ kind: 'accepted' })
  })

  it('maps 409 ping_closed to closed ("too late")', () => {
    const body = { error: { code: 'ping_closed', message: 'closed', retryable: false } }
    expect(replyResultFrom(409, body)).toEqual({ kind: 'closed' })
  })

  it('shows the backend message for other errors', () => {
    const body = { error: { code: 'invalid_reply', message: 'Choose Share it or Don’t share' } }
    expect(replyResultFrom(422, body)).toEqual({ kind: 'error', message: 'Choose Share it or Don’t share' })
  })

  it('explains a sign-out and has a fallback message', () => {
    expect(replyResultFrom(401, null)).toMatchObject({ kind: 'error', message: expect.stringMatching(/signed out/) })
    expect(replyResultFrom(500, 'gateway')).toMatchObject({ kind: 'error', message: expect.stringMatching(/Try again/) })
  })
})

describe('commandResultFrom', () => {
  it('maps 200 to accepted and errors to their message', () => {
    expect(commandResultFrom(200, {})).toEqual({ kind: 'accepted' })
    const body = { error: { code: 'invalid_call_state', message: 'You can pause once they pick up' } }
    expect(commandResultFrom(409, body)).toEqual({ kind: 'error', message: 'You can pause once they pick up' })
  })
})

describe('apiBase', () => {
  it('trims and drops trailing slashes', () => {
    expect(apiBase(' https://api.example.test/ ')).toBe('https://api.example.test')
  })

  it('rejects empty and non-http values', () => {
    expect(apiBase(undefined)).toBeNull()
    expect(apiBase('')).toBeNull()
    expect(apiBase('javascript:alert(1)')).toBeNull()
  })
})

describe('liveCallMode', () => {
  const search = `?call=${CALL_ID}`

  it('plays the fake call unless real mode is fully set up', () => {
    expect(liveCallMode(undefined, 'http://localhost:8000', search)).toEqual({ kind: 'mock' })
    expect(liveCallMode('real', undefined, search)).toEqual({ kind: 'mock' })
    expect(liveCallMode('real', 'http://localhost:8000', '')).toEqual({ kind: 'mock' })
    expect(liveCallMode('real', 'http://localhost:8000', '?call=not-a-uuid')).toEqual({ kind: 'mock' })
  })

  it('follows a real call when all three are set', () => {
    expect(liveCallMode('real', 'http://localhost:8000/', search)).toEqual({
      kind: 'real',
      callId: CALL_ID,
      apiBase: 'http://localhost:8000',
    })
  })
})

describe('postReply and postCommand', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends the token, a fresh Idempotency-Key and the body', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ state: 'answered' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await postReply('https://api.example.test', 'tok', CALL_ID, 'p1', { choice: 0 })
    expect(result).toEqual({ kind: 'accepted' })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe(`https://api.example.test/calls/${CALL_ID}/pings/p1/reply`)
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tok')
    expect(headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
    expect(init.body).toBe(JSON.stringify({ choice: 0 }))
  })

  it('sends commands as { type }', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await postCommand('https://api.example.test', 'tok', CALL_ID, 'pause')
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe(`https://api.example.test/calls/${CALL_ID}/commands`)
    expect(init.body).toBe(JSON.stringify({ type: 'pause' }))
  })

  it('turns a network failure or a non-JSON body into a readable result', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    expect(await postCommand('https://api.example.test', 'tok', CALL_ID, 'end')).toMatchObject({
      kind: 'error',
      message: expect.stringMatching(/No connection/),
    })
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Bad gateway</html>', { status: 502 })))
    expect(await postReply('https://api.example.test', 'tok', CALL_ID, 'p1', { text: 'hi' })).toMatchObject({
      kind: 'error',
    })
  })
})
