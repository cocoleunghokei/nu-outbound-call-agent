import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchCallResult, fetchHistory, historySource, loadedFrom, toCallResult, toHistory, toHistoryItem } from './api'

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

  it('uses real calls with only the API URL set: the source flag is an opt-out', () => {
    expect(historySource(undefined, 'http://localhost:8000')).toEqual({ kind: 'real', apiBase: 'http://localhost:8000' })
    expect(historySource('', 'http://localhost:8000')).toEqual({ kind: 'real', apiBase: 'http://localhost:8000' })
  })

  it('falls back to fake calls otherwise', () => {
    expect(historySource('fake', 'http://localhost:8000')).toEqual({ kind: 'fake' })
    expect(historySource(undefined, undefined)).toEqual({ kind: 'fake' })
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

  it('maps a full result, drops the malformed parts and keeps unknown roles', () => {
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
      { seq: 3, role: 'unknown', text: 'bad role', startedAt: null },
    ])
  })

  it('treats a missing confirmation as not confirmed and missing lists as empty', () => {
    const result = toCallResult({ call: CALL })
    expect(result?.businessConfirmed).toBe(false)
    expect(result?.details).toEqual({})
    expect(result?.openItems).toEqual([])
    expect(result?.transcript).toEqual([])
  })

  it('maps result items and the follow-up, dropping malformed rows', () => {
    const result = toCallResult({
      ...body,
      follow_up: '  Call to confirm.  ',
      result_items: [
        {
          label: 'Ice pack',
          asked: null,
          got: '+$3.00',
          status: 'extra',
          reason: 'They would not waive it.',
          approved_by: 'user',
          cost_cents: 300,
          evidence: [10, 'x', 1.5, 11],
          verified: true,
        },
        { label: 'Party', asked: 'Table for 2', got: 'Table for 2', status: 'as_asked', verified: false },
        { label: 'No status', got: 'x' },
        { label: '  ', got: 'x', status: 'changed' },
        { got: 'x', status: 'changed' },
        'nope',
      ],
    })
    expect(result?.followUp).toBe('Call to confirm.')
    expect(result?.items).toEqual([
      {
        label: 'Ice pack',
        asked: null,
        got: '+$3.00',
        status: 'extra',
        reason: 'They would not waive it.',
        approvedBy: 'user',
        costCents: 300,
        evidence: [10, 11],
        verified: true,
      },
      {
        label: 'Party',
        asked: 'Table for 2',
        got: 'Table for 2',
        status: 'as_asked',
        reason: null,
        approvedBy: null,
        costCents: null,
        evidence: [],
        verified: false,
      },
    ])
  })

  it('has no items and no follow-up when an older server does not send them', () => {
    const result = toCallResult({ call: CALL })
    expect(result?.items).toEqual([])
    expect(result?.followUp).toBeNull()
  })

  it('reads a row without "verified" as verified, and a bad cost as no cost', () => {
    const result = toCallResult({
      call: CALL,
      result_items: [
        { label: 'A', got: 'b', status: 'changed', cost_cents: -5 },
        { label: 'C', got: 'd', status: 'changed', cost_cents: 2.5 },
        { label: 'E', got: 'f', status: 'changed', cost_cents: '300' },
      ],
    })
    expect(result?.items.map((i) => [i.verified, i.costCents])).toEqual([
      [true, null],
      [true, null],
      [true, null],
    ])
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
    expect(loadedFrom(500, null, toHistory, 'Could not load this call. Try again.')).toEqual({
      ok: false,
      message: 'Could not load this call. Try again.',
    })
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

describe('fetchCallResult', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('treats an id that is not a UUID as not found, without a request', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    for (const id of ['__proto__', 'constructor', '../me', '']) {
      expect(await fetchCallResult('http://api.test', 'tok', id)).toEqual({
        ok: false,
        message: 'That call wasn’t found.',
      })
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('names the call, not the list, when its result fails to load', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 500 })))
    expect(await fetchCallResult('http://api.test', 'tok', CALL.id)).toEqual({
      ok: false,
      message: 'Could not load this call. Try again.',
    })
  })

  it('fetches a result for a UUID', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ call: CALL }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const loaded = await fetchCallResult('http://api.test', 'tok', CALL.id)
    expect(loaded.ok && loaded.data.call.id).toBe(CALL.id)
    expect(fetchMock).toHaveBeenCalledWith(`http://api.test/calls/${CALL.id}/result`, {
      headers: { Authorization: 'Bearer tok' },
    })
  })
})
