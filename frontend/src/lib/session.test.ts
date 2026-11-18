import { describe, expect, it, vi } from 'vitest'
import { ensureSession, type SessionAuth } from './session'

const session = (id: string) => ({ user: { id } })

function fakeAuth(current: ReturnType<typeof session> | null, signIn: SessionAuth['signInAnonymously']): SessionAuth {
  return { getSession: async () => ({ data: { session: current } }), signInAnonymously: signIn }
}

describe('ensureSession', () => {
  it('keeps an existing session and does not sign in again', async () => {
    const signIn = vi.fn<SessionAuth['signInAnonymously']>()
    await expect(ensureSession(fakeAuth(session('user-a'), signIn))).resolves.toEqual({ kind: 'session', userId: 'user-a' })
    expect(signIn).not.toHaveBeenCalled()
  })

  it('signs in anonymously when there is no session', async () => {
    const signIn = vi.fn<SessionAuth['signInAnonymously']>(async () => ({ data: { session: session('anon-1') }, error: null }))
    await expect(ensureSession(fakeAuth(null, signIn))).resolves.toEqual({ kind: 'session', userId: 'anon-1' })
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
