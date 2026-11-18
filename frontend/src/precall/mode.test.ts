import { describe, expect, it, vi } from 'vitest'
import { contractExampleBrief } from './contractExample'
import { SignInFailedError, type Account } from '../lib/session'
import { modeNote, resolveDataMode, type LoadTokenSource } from './mode'
import { emptySession } from './start'

const anonymous: Account = { anonymous: true, email: null }
const permanent: Account = { anonymous: false, email: 'alex@example.test' }

const tokenSource =
  (token: string | null, account: Account = anonymous): LoadTokenSource =>
  async () => ({ getToken: async () => token, account })

describe('resolveDataMode', () => {
  it('is demo mode when VITE_API_URL is empty, without touching Supabase', async () => {
    const load = vi.fn(tokenSource('jwt'))
    await expect(resolveDataMode('', load)).resolves.toMatchObject({ kind: 'demo', reason: 'no_api_url' })
    await expect(resolveDataMode(undefined, load)).resolves.toMatchObject({ kind: 'demo', reason: 'no_api_url' })
    expect(load).not.toHaveBeenCalled()
  })

  it('is demo mode when nobody is signed in', async () => {
    await expect(resolveDataMode('http://localhost:8000', tokenSource(null))).resolves.toMatchObject({
      kind: 'demo',
      reason: 'no_session',
    })
  })

  it('is demo mode when the Supabase client cannot load (variables missing)', async () => {
    const load = async () => Promise.reject(new Error('VITE_SUPABASE_URL is not set'))
    await expect(resolveDataMode('http://localhost:8000', load)).resolves.toMatchObject({ kind: 'demo', reason: 'no_supabase' })
  })

  // Regression: a failed anonymous sign-in used to look like "Supabase not configured".
  it('is demo mode with its own reason when the anonymous sign-in fails', async () => {
    const load = async () => Promise.reject(new SignInFailedError('Anonymous sign-ins are disabled'))
    const mode = await resolveDataMode('http://localhost:8000', load)
    expect(mode).toMatchObject({ kind: 'demo', reason: 'sign_in_failed' })
    expect(modeNote(mode)).toMatchObject({ text: "Demo mode (couldn't sign in)" })
    expect(modeNote(mode)?.title).toMatch(/Reload to try again/)
  })

  it('demo Start sends nothing', async () => {
    const fetchMock = vi.fn<typeof fetch>()
    const info = vi.spyOn(console, 'info').mockImplementation(() => {})
    const mode = await resolveDataMode('', tokenSource('jwt'), fetchMock)
    await expect(mode.starter(contractExampleBrief, emptySession)).resolves.toMatchObject({ result: { kind: 'started' } })
    expect(fetchMock).not.toHaveBeenCalled()
    info.mockRestore()
  })

  it('is API mode with a URL and a session, and Start talks to that URL with the token', async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input) =>
      String(input).endsWith('/briefs')
        ? new Response(
            JSON.stringify({ brief_id: 'b1', brief: contractExampleBrief, missing_fields: [], opening_line: 'Hi', created_at: 'now' }),
            { status: 201 },
          )
        : new Response(JSON.stringify({ call_id: 'c1' }), { status: 201 }),
    )
    const mode = await resolveDataMode('http://localhost:8000/', tokenSource('jwt-abc', permanent), fetchMock)
    expect(mode).toMatchObject({ kind: 'api', account: permanent })
    const { result } = await mode.starter(contractExampleBrief, emptySession)
    expect(result).toMatchObject({ kind: 'started', callId: 'c1' })
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual(['http://localhost:8000/briefs', 'http://localhost:8000/calls'])
    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer jwt-abc')
    expect(headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
  })

  // The browser's own check is only a shortcut; the server is the one that refuses.
  it("in API mode with an anonymous session, the server's 403 becomes sign_in_required", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input) =>
      String(input).endsWith('/briefs')
        ? new Response(
            JSON.stringify({ brief_id: 'b1', brief: contractExampleBrief, missing_fields: [], opening_line: 'Hi', created_at: 'now' }),
            { status: 201 },
          )
        : new Response(
            JSON.stringify({ error: { code: 'sign_in_required', message: 'Add your email before placing a call', retryable: false } }),
            { status: 403 },
          ),
    )
    const mode = await resolveDataMode('http://localhost:8000', tokenSource('jwt-anon', anonymous), fetchMock)
    expect(mode).toMatchObject({ kind: 'api', account: anonymous })
    const { result } = await mode.starter(contractExampleBrief, emptySession)
    expect(result).toMatchObject({ kind: 'sign_in_required', message: /Add your email/ })
  })
})

describe('modeNote', () => {
  it('explains demo mode; in API mode says whose data this is', async () => {
    const demo = await resolveDataMode('', tokenSource(null))
    expect(modeNote(demo)).toMatchObject({ text: 'Demo mode (fake data)' })
    expect(modeNote(null)).toBeNull()

    const guest = await resolveDataMode('http://localhost:8000', tokenSource('jwt', anonymous))
    expect(modeNote(guest)).toMatchObject({ text: 'This browser only' })
    expect(modeNote(guest)?.title).toMatch(/Add your email when you place a call/)

    const signedIn = await resolveDataMode('http://localhost:8000', tokenSource('jwt', permanent))
    expect(modeNote(signedIn)).toMatchObject({ text: 'alex@example.test' })
    expect(modeNote(signedIn)?.title).toMatch(/follow this email/)
  })
})
