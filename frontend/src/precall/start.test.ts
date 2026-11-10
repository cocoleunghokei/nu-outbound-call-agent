import { describe, expect, it, vi } from 'vitest'
import { ApiError, NETWORK_ERROR, type ApiClient, type BriefResponse } from './api'
import { chatReducer, initialChatState, type ChatAction, type ChatContext } from './chat'
import { contractExampleBrief } from './contractExample'
import { fakeProfile, fakeTemplates } from './fakeBriefs'
import {
  apiStarter,
  demoStarter,
  describeError,
  emptySession,
  isCallRouteMissing,
  reviewView,
  singleFlight,
  type SaveSession,
} from './start'
import type { Brief } from './types'

const BRIEF_ID = '00000000-0000-4000-8000-000000000001'
const OPENING = "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. Book a table for 4 under Alex."

function saved(brief: Brief, patch: Partial<BriefResponse> = {}): BriefResponse {
  return { brief_id: BRIEF_ID, brief, missing_fields: [], opening_line: OPENING, created_at: '2026-11-09T12:00:00+00:00', ...patch }
}

const routeMissing = () => new ApiError('not_found', 'Not Found', 404, false)

function fakeClient(overrides: Partial<ApiClient> = {}) {
  const client = {
    createBrief: vi.fn<ApiClient['createBrief']>(async (brief) => saved(brief)),
    getBrief: vi.fn<ApiClient['getBrief']>(),
    replaceBrief: vi.fn<ApiClient['replaceBrief']>(async (_id, brief) => saved(brief)),
    startCall: vi.fn<ApiClient['startCall']>(async () => ({ call_id: 'call-1' })),
    ...overrides,
  }
  return client
}

/** Deterministic Idempotency-Keys: key-1, key-2, ... */
function keys() {
  let n = 0
  return () => `key-${++n}`
}

describe('apiStarter (Start in API mode)', () => {
  it('saves with POST /briefs, then hands the brief_id to POST /calls', async () => {
    const client = fakeClient()
    const { result, session } = await apiStarter(client, keys())(contractExampleBrief, emptySession)
    expect(client.createBrief).toHaveBeenCalledWith(contractExampleBrief, 'key-1')
    expect(client.startCall).toHaveBeenCalledWith(BRIEF_ID, 'key-2')
    expect(result).toMatchObject({ kind: 'started', callId: 'call-1', server: { missing_fields: [], opening_line: OPENING } })
    expect(session.briefId).toBe(BRIEF_ID)
  })

  it("blocks Start when the server says something is missing, even if the local check didn't", async () => {
    const client = fakeClient({
      createBrief: vi.fn(async (brief: Brief) => saved(brief, { missing_fields: ['calling_name'], opening_line: null })),
    })
    const { result } = await apiStarter(client, keys())(contractExampleBrief, emptySession)
    expect(result.kind).toBe('blocked')
    if (result.kind !== 'blocked') return
    expect(result.server.missing_fields).toEqual(['calling_name'])
    expect(result.message).toMatch(/^I can't call yet\. Still needed: /)
    expect(client.startCall).not.toHaveBeenCalled()
  })

  it.each([
    ['404 from FastAPI', routeMissing()],
    ['405', new ApiError('method_not_allowed', 'Method Not Allowed', 405, false)],
    ['no answer', new ApiError(NETWORK_ERROR, 'offline', 0, true)],
  ])('says "saved, not connected" when POST /calls is not there (%s)', async (_label, error) => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => {})
    const client = fakeClient({ startCall: vi.fn(async () => Promise.reject(error)) })
    const { result } = await apiStarter(client, keys())(contractExampleBrief, emptySession)
    expect(result).toMatchObject({ kind: 'saved_not_connected', briefId: BRIEF_ID })
    expect(info).toHaveBeenCalledWith(expect.stringContaining('POST /calls'), BRIEF_ID)
    info.mockRestore()
  })

  it("surfaces the server's message when saving fails (invalid_phone)", async () => {
    const error = new ApiError('invalid_phone', "I can't call emergency numbers.", 422, false)
    const client = fakeClient({ createBrief: vi.fn(async () => Promise.reject(error)) })
    const { result, session } = await apiStarter(client, keys())(contractExampleBrief, emptySession)
    expect(result.kind).toBe('error')
    if (result.kind !== 'error') return
    expect(result.message).toContain("I can't call emergency numbers.")
    expect(session.briefId).toBeNull()
    expect(client.startCall).not.toHaveBeenCalled()
  })

  it('a real error from POST /calls is shown, not treated as "not connected"', async () => {
    const error = new ApiError('brief_not_found', 'Brief not found', 404, false)
    const client = fakeClient({ startCall: vi.fn(async () => Promise.reject(error)) })
    const { result } = await apiStarter(client, keys())(contractExampleBrief, emptySession)
    expect(result).toMatchObject({ kind: 'error', message: "I couldn't start the call: Brief not found" })
  })

  it('retrying the same save after a failure reuses its Idempotency-Key', async () => {
    const createBrief = vi
      .fn<ApiClient['createBrief']>()
      .mockRejectedValueOnce(new ApiError('database_unavailable', 'Database is unavailable', 503, true))
      .mockImplementation(async (brief) => saved(brief))
    const client = fakeClient({ createBrief })
    const start = apiStarter(client, keys())
    const first = await start(contractExampleBrief, emptySession)
    expect(first.result.kind).toBe('error')
    await start(contractExampleBrief, first.session)
    expect(createBrief.mock.calls.map((c) => c[1])).toEqual(['key-1', 'key-1'])
  })

  it('retrying Start after POST /calls failed does not save again and reuses the call key', async () => {
    const startCall = vi
      .fn<ApiClient['startCall']>()
      .mockRejectedValueOnce(new ApiError('internal', 'Internal server error', 500, true))
      .mockResolvedValue({ call_id: 'call-1' })
    const client = fakeClient({ startCall })
    const start = apiStarter(client, keys())
    const first = await start(contractExampleBrief, emptySession)
    expect(first.result).toMatchObject({ kind: 'error', message: "I couldn't start the call: Internal server error Tap Start to try again." })
    const second = await start(contractExampleBrief, first.session)
    expect(second.result.kind).toBe('started')
    expect(client.createBrief).toHaveBeenCalledTimes(1)
    expect(startCall.mock.calls.map((c) => c[1])).toEqual(['key-2', 'key-2'])
  })

  it('an edit after an earlier save is a PATCH to the same brief, with a new key', async () => {
    const client = fakeClient({ startCall: vi.fn(async () => Promise.reject(routeMissing())) })
    vi.spyOn(console, 'info').mockImplementation(() => {})
    const start = apiStarter(client, keys())
    const first = await start(contractExampleBrief, emptySession)
    const edited: Brief = { ...contractExampleBrief, fallbacks: [{ text: 'Saturday same time' }] }
    await start(edited, first.session)
    expect(client.createBrief).toHaveBeenCalledTimes(1)
    expect(client.replaceBrief).toHaveBeenCalledWith(BRIEF_ID, edited, 'key-3')
    vi.restoreAllMocks()
  })
})

describe('demoStarter', () => {
  it('only logs the brief and reports a started call', async () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => {})
    const session: SaveSession = emptySession
    await expect(demoStarter(contractExampleBrief, session)).resolves.toEqual({
      result: { kind: 'started', server: null, callId: null },
      session,
    })
    expect(info).toHaveBeenCalledOnce()
    info.mockRestore()
  })
})

describe('singleFlight (no double submit)', () => {
  it('ignores a second call while the first is still running', async () => {
    let finish: (value: string) => void = () => {}
    const work = vi.fn(() => new Promise<string>((resolve) => (finish = resolve)))
    const guarded = singleFlight(work)
    const first = guarded()
    await expect(guarded()).resolves.toBeNull()
    finish('done')
    await expect(first).resolves.toBe('done')
    expect(work).toHaveBeenCalledTimes(1)
    // Once finished, the next tap goes through again.
    void guarded()
    expect(work).toHaveBeenCalledTimes(2)
  })

  it('a double tap on Start sends one save and one call', async () => {
    const client = fakeClient()
    const guarded = singleFlight(apiStarter(client, keys()))
    const [a, b] = await Promise.all([guarded(contractExampleBrief, emptySession), guarded(contractExampleBrief, emptySession)])
    expect(b).toBeNull()
    expect(a?.result.kind).toBe('started')
    expect(client.createBrief).toHaveBeenCalledTimes(1)
    expect(client.startCall).toHaveBeenCalledTimes(1)
  })
})

describe('reviewView (server wins)', () => {
  const local = { missing_fields: [], opening_line: 'local line' }
  const server = { briefJson: JSON.stringify(contractExampleBrief), missing_fields: ['calling_name'], opening_line: null }

  it("uses the server's check for the brief it checked", () => {
    expect(reviewView(contractExampleBrief, local, server)).toEqual({ missing_fields: ['calling_name'], opening_line: null })
  })

  it('falls back to the local preview once the brief was edited', () => {
    const edited: Brief = { ...contractExampleBrief, goal: 'Something else' }
    expect(reviewView(edited, local, server)).toBe(local)
    expect(reviewView(contractExampleBrief, local, null)).toBe(local)
  })
})

describe('isCallRouteMissing and describeError', () => {
  it('only treats a missing route or no answer as "not connected"', () => {
    expect(isCallRouteMissing(routeMissing())).toBe(true)
    expect(isCallRouteMissing(new ApiError('http_404', 'x', 404, false))).toBe(true)
    expect(isCallRouteMissing(new ApiError('brief_not_found', 'x', 404, false))).toBe(false)
    expect(isCallRouteMissing(new ApiError('invalid_phone', 'x', 422, false))).toBe(false)
  })

  it('uses plain words for sign-in and connection problems', () => {
    expect(describeError(new ApiError('unauthenticated', 'Missing or invalid credentials', 401, false), 'save')).toMatch(/signed out/)
    expect(describeError(new ApiError(NETWORK_ERROR, 'x', 0, true), 'save')).toMatch(/couldn't reach the server/)
    expect(describeError(new ApiError('brief_in_use', 'A call already started from this brief', 409, false), 'save')).toMatch(/Start a new call/)
  })
})

describe('chat reducer: Start outcomes from the API', () => {
  const ctx: ChatContext = { profile: fakeProfile, templates: fakeTemplates }
  const reduce = chatReducer(ctx)
  const send = (text: string): ChatAction => ({ type: 'send', text })
  const done = (label: string): ChatAction => ({ type: 'list_done', label })
  const ready = [
    { type: 'pick_custom' } as ChatAction,
    send('Ask if they can hold a cake for pickup tomorrow'),
    send('Sunrise Bakery'),
    send('617 555 0122'),
    done('No limits'),
    done('No preferences'),
    send('Any cake they have ready'),
    done("That's all"),
    done('Nothing else'),
  ].reduce(reduce, initialChatState(ctx))

  it('"saved, not connected" locks the review without claiming a call', () => {
    const s = reduce(ready, { type: 'saved_not_connected' })
    expect(s.stage).toBe('started')
    expect(s.startOutcome).toBe('saved')
    expect(s.messages[s.messages.length - 1]?.text).toBe("Saved your brief. Calling isn't connected yet, so I haven't dialed.")
    expect(reduce(s, { type: 'saved_not_connected' })).toBe(s)
  })

  it('a started call is marked as calling', () => {
    expect(reduce(ready, { type: 'start' }).startOutcome).toBe('calling')
  })

  it('a note adds an agent message and leaves the review open', () => {
    const s = reduce(ready, { type: 'note', text: 'I can’t call yet.' })
    expect(s.stage).toBe('draft_ready')
    expect(s.messages[s.messages.length - 1]).toMatchObject({ from: 'agent', text: 'I can’t call yet.' })
    expect(reduce(ready, { type: 'note', text: '  ' })).toBe(ready)
  })

  it('a new call clears the outcome', () => {
    const s = reduce(reduce(ready, { type: 'saved_not_connected' }), { type: 'new_call' })
    expect(s.startOutcome).toBeNull()
  })
})
