import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  getSession: vi.fn(),
  signInAnonymously: vi.fn(),
  onAuthStateChange: vi.fn(),
}))
vi.mock('./supabase', () => ({ supabase: { auth } }))

import { accessToken, ensureSignedIn, onSignInChange, signedInAccessToken } from './accessToken'

describe('accessToken', () => {
  beforeEach(() => vi.resetAllMocks())

  it('returns the session token, or null when signed out', async () => {
    auth.getSession.mockResolvedValueOnce({ data: { session: { access_token: 'tok' } } })
    expect(await accessToken()).toBe('tok')
    auth.getSession.mockResolvedValueOnce({ data: { session: null } })
    expect(await accessToken()).toBeNull()
  })
})

describe('ensureSignedIn and signedInAccessToken', () => {
  beforeEach(() => vi.resetAllMocks())

  const anon = { user: { id: 'anon-1', is_anonymous: true }, access_token: 'tok-anon' }

  it('keeps an existing session and returns its token without signing in', async () => {
    auth.getSession.mockResolvedValue({ data: { session: anon } })
    await expect(ensureSignedIn()).resolves.toMatchObject({ kind: 'session', userId: 'anon-1' })
    expect(await signedInAccessToken()).toBe('tok-anon')
    expect(auth.signInAnonymously).not.toHaveBeenCalled()
  })

  // The history list and an open call's result load at the same time, and StrictMode runs
  // effects twice: two sign-ins would create two users and the second would win.
  it('signs in once for concurrent callers, and not again once the session exists', async () => {
    let current: typeof anon | null = null
    auth.getSession.mockImplementation(async () => ({ data: { session: current } }))
    let finish!: () => void
    const pending = new Promise<void>((resolve) => {
      finish = resolve
    })
    auth.signInAnonymously.mockImplementation(async () => {
      await pending
      current = anon
      return { data: { session: anon }, error: null }
    })

    const first = ensureSignedIn()
    const second = ensureSignedIn()
    finish()
    const [a, b] = await Promise.all([first, second])
    expect(a).toMatchObject({ kind: 'session', userId: 'anon-1' })
    expect(b).toEqual(a)
    expect(auth.signInAnonymously).toHaveBeenCalledOnce()

    // The reload that the sign-in change triggers finds the session: no second user, no loop.
    await expect(ensureSignedIn()).resolves.toMatchObject({ kind: 'session', userId: 'anon-1' })
    expect(auth.signInAnonymously).toHaveBeenCalledOnce()
    expect(await signedInAccessToken()).toBe('tok-anon')
  })

  it('reports a refused sign-in as failed, and the token as null', async () => {
    auth.getSession.mockResolvedValue({ data: { session: null } })
    auth.signInAnonymously.mockResolvedValue({
      data: { session: null },
      error: { message: 'Anonymous sign-ins are disabled' },
    })
    await expect(ensureSignedIn()).resolves.toEqual({ kind: 'failed', message: 'Anonymous sign-ins are disabled' })
    expect(await signedInAccessToken()).toBeNull()
  })

  it('resolves to null instead of rejecting when the token read fails after the sign-in check', async () => {
    auth.getSession
      .mockResolvedValueOnce({ data: { session: anon } })
      .mockRejectedValueOnce(new Error('storage gone'))
    expect(await signedInAccessToken()).toBeNull()
  })
})

describe('onSignInChange', () => {
  beforeEach(() => vi.resetAllMocks())

  type Listener = (event: string, session: { user: { id: string } } | null) => void
  const as = (id: string) => ({ user: { id } })

  async function subscribe() {
    const unsubscribe = vi.fn()
    let listener: Listener = () => {}
    auth.onAuthStateChange.mockImplementation((cb: Listener) => {
      listener = cb
      return { data: { subscription: { unsubscribe } } }
    })
    const onChange = vi.fn()
    const stop = await onSignInChange(onChange)
    return { emit: (event: string, session: ReturnType<typeof as> | null) => listener(event, session), onChange, stop, unsubscribe }
  }

  it('calls back when the user signs in or out, and unsubscribes', async () => {
    const { emit, onChange, stop, unsubscribe } = await subscribe()
    emit('INITIAL_SESSION', null)
    expect(onChange).not.toHaveBeenCalled()
    emit('SIGNED_IN', as('user-a'))
    expect(onChange).toHaveBeenCalledTimes(1)
    emit('SIGNED_OUT', null)
    expect(onChange).toHaveBeenCalledTimes(2)
    emit('SIGNED_IN', as('user-b'))
    expect(onChange).toHaveBeenCalledTimes(3)
    stop()
    expect(unsubscribe).toHaveBeenCalledOnce()
  })

  // Regression (#65 review): auth-js reports SIGNED_IN for the same user each time the
  // tab becomes visible again, which reloaded the history screen and lost its place.
  it('ignores events that keep the same user, including a repeated SIGNED_IN', async () => {
    const { emit, onChange } = await subscribe()
    emit('INITIAL_SESSION', as('user-a'))
    emit('SIGNED_IN', as('user-a'))
    emit('TOKEN_REFRESHED', as('user-a'))
    emit('USER_UPDATED', as('user-a'))
    expect(onChange).not.toHaveBeenCalled()
    emit('SIGNED_IN', as('user-b'))
    expect(onChange).toHaveBeenCalledOnce()
  })

  // Converting an anonymous user to a permanent one keeps the user id; the screen must
  // still re-pick its mode (ready for a later email sign-in).
  it('calls back when the same user stops being anonymous', async () => {
    const { emit, onChange } = await subscribe()
    const anonymous = { user: { id: 'user-a', is_anonymous: true } }
    const permanent = { user: { id: 'user-a', is_anonymous: false } }
    emit('INITIAL_SESSION', anonymous)
    emit('TOKEN_REFRESHED', anonymous)
    expect(onChange).not.toHaveBeenCalled()
    emit('USER_UPDATED', permanent)
    expect(onChange).toHaveBeenCalledOnce()
    emit('SIGNED_IN', permanent)
    expect(onChange).toHaveBeenCalledOnce()
  })
})

// Last on purpose: it swaps the `./supabase` mock for one that throws at import, the way
// `supabase.ts` does without its variables, and takes a fresh copy of the module under test.
describe('ensureSignedIn without Supabase settings', () => {
  afterEach(() => {
    vi.doMock('./supabase', () => ({ supabase: { auth } }))
    vi.resetModules()
  })

  it('is failed, and the token is null, instead of a rejection', async () => {
    vi.resetModules()
    vi.doMock('./supabase', () => {
      throw new Error('VITE_SUPABASE_URL is not set')
    })
    const fresh = await import('./accessToken')
    // vitest rewraps an error thrown by a mock factory, so only the shape is asserted.
    const result = await fresh.ensureSignedIn()
    expect(result.kind).toBe('failed')
    expect(result.kind === 'failed' && result.message.length > 0).toBe(true)
    expect(await fresh.signedInAccessToken()).toBeNull()
  })
})
