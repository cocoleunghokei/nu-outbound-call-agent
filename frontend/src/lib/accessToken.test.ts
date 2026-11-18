import { beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
}))
vi.mock('./supabase', () => ({ supabase: { auth } }))

import { accessToken, onSignInChange } from './accessToken'

describe('accessToken', () => {
  beforeEach(() => vi.clearAllMocks())

  it('returns the session token, or null when signed out', async () => {
    auth.getSession.mockResolvedValueOnce({ data: { session: { access_token: 'tok' } } })
    expect(await accessToken()).toBe('tok')
    auth.getSession.mockResolvedValueOnce({ data: { session: null } })
    expect(await accessToken()).toBeNull()
  })
})

describe('onSignInChange', () => {
  beforeEach(() => vi.clearAllMocks())

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
