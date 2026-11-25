import { describe, expect, it, vi } from 'vitest'
import { authRedirectProblem, connectGoogle, signInWithGoogle, signOutOfAccount, type GoogleAuth } from './googleSignIn'

const REDIRECT = 'https://app.example.test/profile'

function fakeAuth(error: { message: string } | null = null): GoogleAuth {
  return {
    linkIdentity: vi.fn(async () => ({ error })),
    signInWithOAuth: vi.fn(async () => ({ error })),
    signOut: vi.fn(async () => ({ error })),
  }
}

describe('connectGoogle', () => {
  it('links Google to the current user and comes back to the given page', async () => {
    const auth = fakeAuth()
    await expect(connectGoogle(auth, REDIRECT)).resolves.toEqual({ kind: 'redirecting' })
    expect(auth.linkIdentity).toHaveBeenCalledWith({ provider: 'google', options: { redirectTo: REDIRECT } })
    expect(auth.signInWithOAuth).not.toHaveBeenCalled()
  })

  it('reports the failure when linking is refused', async () => {
    const auth = fakeAuth({ message: 'Manual linking is disabled' })
    await expect(connectGoogle(auth, REDIRECT)).resolves.toEqual({ kind: 'failed', message: 'Manual linking is disabled' })
  })

  it('reports a thrown error instead of rejecting', async () => {
    const auth = fakeAuth()
    auth.linkIdentity = vi.fn(async () => {
      throw new Error('network down')
    })
    await expect(connectGoogle(auth, REDIRECT)).resolves.toEqual({ kind: 'failed', message: 'network down' })
  })
})

describe('signInWithGoogle', () => {
  it('signs in with Google without linking to the current guest', async () => {
    const auth = fakeAuth()
    await expect(signInWithGoogle(auth, REDIRECT)).resolves.toEqual({ kind: 'redirecting' })
    expect(auth.signInWithOAuth).toHaveBeenCalledWith({ provider: 'google', options: { redirectTo: REDIRECT } })
    expect(auth.linkIdentity).not.toHaveBeenCalled()
  })

  it('reports the failure', async () => {
    const auth = fakeAuth({ message: 'Provider is not enabled' })
    await expect(signInWithGoogle(auth, REDIRECT)).resolves.toEqual({ kind: 'failed', message: 'Provider is not enabled' })
  })
})

describe('signOutOfAccount', () => {
  it('signs out', async () => {
    const auth = fakeAuth()
    await expect(signOutOfAccount(auth)).resolves.toEqual({ kind: 'redirecting' })
    expect(auth.signOut).toHaveBeenCalledOnce()
  })

  it('reports a failed sign-out', async () => {
    await expect(signOutOfAccount(fakeAuth({ message: 'nope' }))).resolves.toEqual({ kind: 'failed', message: 'nope' })
  })
})

describe('authRedirectProblem', () => {
  it('is null when the address carries no error', () => {
    expect(authRedirectProblem('https://app.example.test/profile')).toBeNull()
    expect(authRedirectProblem('https://app.example.test/profile#access_token=abc')).toBeNull()
    expect(authRedirectProblem('not a url')).toBeNull()
  })

  it('explains a Google account that already has an account', () => {
    const url = 'https://app.example.test/profile#error=server_error&error_code=identity_already_exists&error_description=Identity+is+already+linked'
    expect(authRedirectProblem(url)).toContain('already has an account here')
  })

  it('explains a cancelled sign-in', () => {
    expect(authRedirectProblem('https://app.example.test/profile#error=access_denied&error_description=User+denied')).toBe('Google sign-in was cancelled.')
  })

  it('reads the query string too, and uses the description for other errors', () => {
    const url = 'https://app.example.test/profile?error=server_error&error_description=Something+broke'
    expect(authRedirectProblem(url)).toBe('Something broke')
  })
})
