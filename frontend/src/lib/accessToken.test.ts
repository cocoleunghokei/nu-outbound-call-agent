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

  it('calls back on sign-in and sign-out only, and unsubscribes', async () => {
    const unsubscribe = vi.fn()
    let listener: (event: string) => void = () => {}
    auth.onAuthStateChange.mockImplementation((cb: (event: string) => void) => {
      listener = cb
      return { data: { subscription: { unsubscribe } } }
    })
    const onChange = vi.fn()
    const stop = await onSignInChange(onChange)
    listener('INITIAL_SESSION')
    listener('TOKEN_REFRESHED')
    expect(onChange).not.toHaveBeenCalled()
    listener('SIGNED_IN')
    listener('SIGNED_OUT')
    expect(onChange).toHaveBeenCalledTimes(2)
    stop()
    expect(unsubscribe).toHaveBeenCalledOnce()
  })
})
