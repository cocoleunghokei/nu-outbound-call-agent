import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchHistory, historySource, loadedFrom, toCallResult, toHistory, toHistoryItem } from './api'

const CALL = {
  id: '7d4c1f0e-1a2b-4c3d-8e9f-000000000002',
  business_name: 'Juniper Ramen',
  phone_e164: '+16175550177',
  goal: 'Book a table for 2 on Saturday',
  template_key: 'restaurant_booking',
  status: 'ended',
  outcome: 'booked',
  created_at: '2026-10-22T16:30:00Z',
  ended_at: '2026-10-22T16:31:45Z',
}

describe('historySource', () => {
  it('uses the backend only when the source is "real" and the API URL is valid', () => {
    expect(historySource('real', 'http://localhost:8000/')).toEqual({ kind: 'real', apiBase: 'http://localhost:8000' })
  })

  it('falls back to fake calls otherwise', () => {
    expect(historySource(undefined, 'http://localhost:8000')).toEqual({ kind: 'fake' })
    expect(historySource('real', '')).toEqual({ kind: 'fake' })
    expect(historySource('real', 'localhost:8000')).toEqual({ kind: 'fake' })
  })
})

describe('toHistoryItem', () => {
  it('maps the backend fields to the screen’s', () => {
    expect(toHistoryItem(CALL)).toEqual({
      id: CALL.id,
      businessName: 'Juniper Ramen',
      phoneE164: '+16175550177',
      goal: 'Book a table for 2 on Saturday',
      templateKey: 'restaurant_booking',
      status: 'ended',
      outcome: 'booked',
      createdAt: CALL.created_at,
      endedAt: CALL.ended_at,
    })
  })

  it('keeps a call with no outcome or end yet', () => {
    const item = toHistoryItem({ ...CALL, outcome: null, ended_at: null, template_key: null })
    expect(item?.outcome).toBeNull()
    expect(item?.endedAt).toBeNull()
    expect(item?.templateKey).toBeNull()
  })

  it('rejects a call with no id, date or status', () => {
    expect(toHistoryItem({ ...CALL, id: undefined })).toBeNull()
    expect(toHistoryItem({ ...CALL, created_at: 5 })).toBeNull()
    expect(toHistoryItem({ ...CALL, status: null })).toBeNull()
    expect(toHistoryItem('nope')).toBeNull()
  })
})

describe('toHistory', () => {
  it('drops malformed rows and keeps the rest', () => {
    expect(toHistory({ calls: [CALL, { id: 7 }] })?.map((c) => c.id)).toEqual([CALL.id])
  })

  it('rejects a body without a calls list', () => {
    expect(toHistory({})).toBeNull()
    expect(toHistory(null)).toBeNull()
  })
})

describe('toCallResult', () => {
  const body = {
    call: CALL,
    business_confirmed: true,
    details: { date: '2026-10-24', time: '19:30', confirmation_number: null, staff_name: 'Marco' },
    open_items: ['Bring the confirmation number', '  ', 3],
    decisions: [
      { situation: '7pm is full', action: 'take_fallback', reason: 'Matched your fallback', field_key: null },
      { action: 'accept' },
    ],
    shared_fields: [{ field_key: 'calling_name', state: 'spoken' }, { field_key: 1 }],
    transcript: [
      { seq: 1, role: 'business', text: 'Juniper Ramen.', started_at: '2026-10-22T16:30:00Z' },
      { seq: 2, role: 'agent', text: 'Hi.', started_at: null },
      { seq: 'x', role: 'agent', text: 'bad' },
      { seq: 3, role: 'robot', text: 'bad role' },
    ],
  }

  it('maps a full result and drops the malformed parts', () => {
    const result = toCallResult(body)
    expect(result?.call.businessName).toBe('Juniper Ramen')
    expect(result?.businessConfirmed).toBe(true)
    expect(result?.details).toEqual({ date: '2026-10-24', time: '19:30', staffName: 'Marco' })
    expect(result?.openItems).toEqual(['Bring the confirmation number'])
    expect(result?.decisions).toEqual([
      { situation: '7pm is full', action: 'take_fallback', reason: 'Matched your fallback' },
    ])
    expect(result?.sharedFields).toEqual([{ fieldKey: 'calling_name', state: 'spoken' }])
    expect(result?.transcript).toEqual([
      { seq: 1, role: 'business', text: 'Juniper Ramen.', startedAt: '2026-10-22T16:30:00Z' },
      { seq: 2, role: 'agent', text: 'Hi.', startedAt: null },
    ])
  })

  it('treats a missing confirmation as not confirmed and missing lists as empty', () => {
    const result = toCallResult({ call: CALL })
    expect(result?.businessConfirmed).toBe(false)
    expect(result?.details).toEqual({})
    expect(result?.openItems).toEqual([])
    expect(result?.transcript).toEqual([])
  })

  it('rejects a result whose call is malformed', () => {
    expect(toCallResult({ ...body, call: { id: 1 } })).toBeNull()
  })
})

describe('loadedFrom', () => {
  it('returns the checked data on 200', () => {
    expect(loadedFrom(200, { calls: [] }, toHistory)).toEqual({ ok: true, data: [] })
  })

  it('explains each failure in plain words', () => {
    expect(loadedFrom(200, { nope: 1 }, toHistory)).toEqual({
      ok: false,
      message: 'The server sent something unexpected. Try again.',
    })
    expect(loadedFrom(401, null, toHistory).ok).toBe(false)
    expect(loadedFrom(404, null, toHistory)).toEqual({ ok: false, message: 'That call wasn’t found.' })
    expect(loadedFrom(500, null, toHistory)).toEqual({ ok: false, message: 'Could not load your calls. Try again.' })
  })
})

describe('fetchHistory', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends the token and reads the list', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ calls: [CALL] }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const loaded = await fetchHistory('http://api.test', 'tok')
    expect(loaded.ok && loaded.data.map((c) => c.id)).toEqual([CALL.id])
    expect(fetchMock).toHaveBeenCalledWith('http://api.test/calls', { headers: { Authorization: 'Bearer tok' } })
  })

  it('turns a network failure into a message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))))
    expect(await fetchHistory('http://api.test', 'tok')).toEqual({
      ok: false,
      message: 'No connection. Check your internet and try again.',
    })
  })
})
