import { describe, expect, it, vi } from 'vitest'
import { ensureSession, type SessionAuth, type SessionUser } from './session'

const session = (user: SessionUser) => ({ user })

function fakeAuth(current: ReturnType<typeof session> | null, signIn: SessionAuth['signInAnonymously']): SessionAuth {
  return { getSession: async () => ({ data: { session: current } }), signInAnonymously: signIn }
}

describe('ensureSession', () => {
  it('keeps an existing session and does not sign in again', async () => {
    const signIn = vi.fn<SessionAuth['signInAnonymously']>()
    const existing = fakeAuth(session({ id: 'user-a', is_anonymous: false, email: 'alex@example.test' }), signIn)
    await expect(ensureSession(existing)).resolves.toEqual({
      kind: 'session',
      userId: 'user-a',
      account: { userId: 'user-a', anonymous: false, email: 'alex@example.test' },
    })
    expect(signIn).not.toHaveBeenCalled()
  })

  it('reports an existing anonymous session as anonymous', async () => {
    const signIn = vi.fn<SessionAuth['signInAnonymously']>()
    const anonymous = fakeAuth(session({ id: 'anon-1', is_anonymous: true, email: '' }), signIn)
    await expect(ensureSession(anonymous)).resolves.toMatchObject({ userId: 'anon-1', account: { userId: 'anon-1', anonymous: true, email: null } })
    // A token from before the claim existed is a permanent user.
    const legacy = fakeAuth(session({ id: 'user-b' }), signIn)
    await expect(ensureSession(legacy)).resolves.toMatchObject({ account: { anonymous: false, email: null } })
  })

  it('signs in anonymously when there is no session', async () => {
    const signIn = vi.fn<SessionAuth['signInAnonymously']>(async () => ({
      data: { session: session({ id: 'anon-1', is_anonymous: true }) },
      error: null,
    }))
    await expect(ensureSession(fakeAuth(null, signIn))).resolves.toEqual({
      kind: 'session',
      userId: 'anon-1',
      account: { userId: 'anon-1', anonymous: true, email: null },
    })
    expect(signIn).toHaveBeenCalledOnce()
  })

  it('reports a refused sign-in instead of throwing', async () => {
    const refused = fakeAuth(null, async () => ({ data: { session: null }, error: { message: 'Anonymous sign-ins are disabled' } }))
    await expect(ensureSession(refused)).resolves.toEqual({ kind: 'failed', message: 'Anonymous sign-ins are disabled' })
    const offline = fakeAuth(null, async () => Promise.reject(new Error('Failed to fetch')))
    await expect(ensureSession(offline)).resolves.toEqual({ kind: 'failed', message: 'Failed to fetch' })
    const empty = fakeAuth(null, async () => ({ data: { session: null }, error: null }))
    await expect(ensureSession(empty)).resolves.toMatchObject({ kind: 'failed' })
  })
})
