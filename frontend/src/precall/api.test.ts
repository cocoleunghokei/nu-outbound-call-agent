import { describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  BAD_RESPONSE,
  NETWORK_ERROR,
  NO_SESSION,
  createApiClient,
  normaliseBaseUrl,
  parseApiError,
  type BriefResponse,
} from './api'
import { contractExampleBrief } from './contractExample'

const BRIEF_ID = '00000000-0000-4000-8000-000000000001'
const KEY = '11111111-1111-4111-8111-111111111111'

const okBrief: BriefResponse = {
  brief_id: BRIEF_ID,
  brief: contractExampleBrief,
  missing_fields: [],
  opening_line: "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. Book a table for 4 under Alex.",
  created_at: '2026-11-09T12:00:00+00:00',
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function setup(respond: () => Response | Promise<Response> = () => json(201, okBrief), token: string | null = 'jwt-abc') {
  const fetchMock = vi.fn<typeof fetch>(async () => respond())
  const client = createApiClient({ baseUrl: 'https://api.test/', getToken: async () => token, fetch: fetchMock })
  const call = (i = 0) => {
    const [url, init] = fetchMock.mock.calls[i]
    return { url: String(url), init: init ?? {}, headers: (init?.headers ?? {}) as Record<string, string> }
  }
  return { client, fetchMock, call }
}

describe('createApiClient', () => {
  it('POST /briefs sends the Bearer token, the Idempotency-Key and the brief as JSON', async () => {
    const { client, call } = setup()
    await expect(client.createBrief(contractExampleBrief, KEY)).resolves.toEqual(okBrief)
    const { url, init, headers } = call()
    expect(url).toBe('https://api.test/briefs')
    expect(init.method).toBe('POST')
    expect(headers.Authorization).toBe('Bearer jwt-abc')
    expect(headers['Idempotency-Key']).toBe(KEY)
    expect(headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(String(init.body))).toEqual(contractExampleBrief)
  })

  it('PATCH /briefs/{id} sends the key; GET /briefs/{id} sends none', async () => {
    const { client, call } = setup(() => json(200, okBrief))
    await client.replaceBrief(BRIEF_ID, contractExampleBrief, KEY)
    await client.getBrief(BRIEF_ID)
    expect(call(0).url).toBe(`https://api.test/briefs/${BRIEF_ID}`)
    expect(call(0).init.method).toBe('PATCH')
    expect(call(0).headers['Idempotency-Key']).toBe(KEY)
    expect(call(1).init.method).toBe('GET')
    expect(call(1).headers.Authorization).toBe('Bearer jwt-abc')
    expect(call(1).headers).not.toHaveProperty('Idempotency-Key')
    expect(call(1).init.body).toBeUndefined()
  })

  it('startCall posts {brief_id} to /calls with the key', async () => {
    const { client, call } = setup(() => json(201, { call_id: 'call-1', status: 'queued' }))
    await expect(client.startCall(BRIEF_ID, KEY)).resolves.toEqual({ call_id: 'call-1', status: 'queued' })
    expect(call().url).toBe('https://api.test/calls')
    expect(call().init.method).toBe('POST')
    expect(call().headers['Idempotency-Key']).toBe(KEY)
    expect(JSON.parse(String(call().init.body))).toEqual({ brief_id: BRIEF_ID })
  })

  it('a retry with the same key sends the same Idempotency-Key header', async () => {
    let n = 0
    const { client, call } = setup(() => (n++ === 0 ? json(503, { error: { code: 'database_unavailable', message: 'Database is unavailable', retryable: true } }) : json(201, okBrief)))
    await expect(client.createBrief(contractExampleBrief, KEY)).rejects.toMatchObject({ code: 'database_unavailable', retryable: true, status: 503 })
    await client.createBrief(contractExampleBrief, KEY)
    expect(call(0).headers['Idempotency-Key']).toBe(KEY)
    expect(call(1).headers['Idempotency-Key']).toBe(KEY)
  })

  it('parses the shared error shape into an ApiError with the server message', async () => {
    const { client } = setup(() =>
      json(422, { error: { code: 'invalid_phone', message: "That number can't be called.", retryable: false } }),
    )
    const err = await client.createBrief(contractExampleBrief, KEY).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ code: 'invalid_phone', message: "That number can't be called.", retryable: false, status: 422 })
  })

  it('turns a failed fetch into a retryable network_error with status 0', async () => {
    const { client } = setup(() => {
      throw new TypeError('Failed to fetch')
    })
    await expect(client.getBrief(BRIEF_ID)).rejects.toMatchObject({ code: NETWORK_ERROR, status: 0, retryable: true })
  })

  it('does not send anything when signed out', async () => {
    const { client, fetchMock } = setup(undefined, null)
    await expect(client.createBrief(contractExampleBrief, KEY)).rejects.toMatchObject({ code: NO_SESSION, status: 401 })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects a malformed brief response', async () => {
    const { client } = setup(() => json(201, { brief_id: 42 }))
    await expect(client.createBrief(contractExampleBrief, KEY)).rejects.toMatchObject({ code: BAD_RESPONSE })
  })

  it('encodes the brief id in the path', async () => {
    const { client, call } = setup(() => json(200, okBrief))
    await client.getBrief('a/b')
    expect(call().url).toBe('https://api.test/briefs/a%2Fb')
  })
})

describe('parseApiError', () => {
  it('falls back to a generic error for a body without the shared shape', () => {
    expect(parseApiError(502, '<html>Bad gateway</html>')).toMatchObject({ code: 'http_502', status: 502, retryable: true })
    expect(parseApiError(404, null)).toMatchObject({ code: 'http_404', retryable: false })
  })

  it('defaults retryable from the status when the server leaves it out', () => {
    expect(parseApiError(500, { error: { code: 'internal', message: 'Internal server error' } }).retryable).toBe(true)
  })
})

describe('normaliseBaseUrl', () => {
  it('treats empty or blank as not configured and strips trailing slashes', () => {
    expect(normaliseBaseUrl(undefined)).toBeNull()
    expect(normaliseBaseUrl('  ')).toBeNull()
    expect(normaliseBaseUrl('http://localhost:8000//')).toBe('http://localhost:8000')
  })
})
