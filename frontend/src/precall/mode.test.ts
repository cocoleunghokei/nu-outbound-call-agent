import { describe, expect, it, vi } from 'vitest'
import { contractExampleBrief } from './contractExample'
import { modeNote, resolveDataMode } from './mode'
import { emptySession } from './start'

const tokenSource = (token: string | null) => async () => async () => token

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
    const mode = await resolveDataMode('http://localhost:8000/', tokenSource('jwt-abc'), fetchMock)
    expect(mode.kind).toBe('api')
    const { result } = await mode.starter(contractExampleBrief, emptySession)
    expect(result).toMatchObject({ kind: 'started', callId: 'c1' })
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual(['http://localhost:8000/briefs', 'http://localhost:8000/calls'])
    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer jwt-abc')
    expect(headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
  })
})

describe('modeNote', () => {
  it('shows a quiet note in demo mode only', async () => {
    const demo = await resolveDataMode('', tokenSource(null))
    expect(modeNote(demo)).toMatchObject({ text: 'Demo mode (fake data)' })
    expect(modeNote(null)).toBeNull()
    const api = await resolveDataMode('http://localhost:8000', tokenSource('jwt'))
    expect(modeNote(api)).toBeNull()
  })
})
