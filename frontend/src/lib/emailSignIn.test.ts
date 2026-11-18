import { describe, expect, it, vi } from 'vitest'
import { isPlausibleEmail, requestCode, verifyCode, type CodeSent, type EmailAuth } from './emailSignIn'

const ok = async () => ({ error: null })
const fails = (message: string, code?: string) => async () => ({ error: { message, code } })

function fakeAuth(overrides: Partial<EmailAuth> = {}) {
  return {
    updateUser: vi.fn<EmailAuth['updateUser']>(ok),
    signInWithOtp: vi.fn<EmailAuth['signInWithOtp']>(ok),
    verifyOtp: vi.fn<EmailAuth['verifyOtp']>(ok),
    ...overrides,
  }
}

const anonymous = { anonymous: true, email: null }
const permanent = { anonymous: false, email: 'alex@example.test' }

describe('requestCode', () => {
  it('links the email to the anonymous user, so the code verifies as an email change', async () => {
    const auth = fakeAuth()
    await expect(requestCode(auth, ' Alex@example.test ', anonymous)).resolves.toEqual({
      kind: 'code_sent',
      email: 'Alex@example.test',
      verify: 'email_change',
      switchesUser: false,
    })
    expect(auth.updateUser).toHaveBeenCalledWith({ email: 'Alex@example.test' })
    expect(auth.signInWithOtp).not.toHaveBeenCalled()
  })

  it('signs in to the existing account when the email is already registered (returning user)', async () => {
    for (const refusal of [
      fails('A user with this email address has already been registered', 'email_exists'),
      fails('Email address already exists'),
    ]) {
      const auth = fakeAuth({ updateUser: vi.fn<EmailAuth['updateUser']>(refusal) })
      await expect(requestCode(auth, 'alex@example.test', anonymous)).resolves.toEqual({
        kind: 'code_sent',
        email: 'alex@example.test',
        verify: 'email',
        switchesUser: true,
      })
      // Never creates a second account for an address that already has one.
      expect(auth.signInWithOtp).toHaveBeenCalledWith({ email: 'alex@example.test', options: { shouldCreateUser: false } })
    }
  })

  it('signs in (creating the account if needed) when nobody is signed in', async () => {
    const auth = fakeAuth()
    await expect(requestCode(auth, 'alex@example.test', null)).resolves.toMatchObject({ verify: 'email', switchesUser: false })
    expect(auth.signInWithOtp).toHaveBeenCalledWith({ email: 'alex@example.test', options: { shouldCreateUser: true } })
    expect(auth.updateUser).not.toHaveBeenCalled()
  })

  it('switching a permanent user to another email signs in to that account', async () => {
    const auth = fakeAuth()
    await expect(requestCode(auth, 'other@example.test', permanent)).resolves.toMatchObject({ verify: 'email', switchesUser: true })
    expect(auth.updateUser).not.toHaveBeenCalled()
  })

  it('refuses a typo before asking Supabase, and reports failures in plain words', async () => {
    const auth = fakeAuth()
    await expect(requestCode(auth, 'alex-at-example', anonymous)).resolves.toMatchObject({ kind: 'failed' })
    expect(auth.updateUser).not.toHaveBeenCalled()

    const limited = fakeAuth({ updateUser: vi.fn<EmailAuth['updateUser']>(fails('Email rate limit exceeded', 'over_email_send_rate_limit')) })
    await expect(requestCode(limited, 'alex@example.test', anonymous)).resolves.toEqual({
      kind: 'failed',
      message: 'Too many emails just now. Try again in a minute.',
    })

    const offline = fakeAuth({ signInWithOtp: vi.fn<EmailAuth['signInWithOtp']>(async () => Promise.reject(new Error('Failed to fetch'))) })
    await expect(requestCode(offline, 'alex@example.test', null)).resolves.toEqual({
      kind: 'failed',
      message: "Couldn't send the code: Failed to fetch",
    })
  })
})

describe('verifyCode', () => {
  const linking: CodeSent = { kind: 'code_sent', email: 'alex@example.test', verify: 'email_change', switchesUser: false }

  it('verifies the typed digits against the flow the code was sent for', async () => {
    const auth = fakeAuth()
    await expect(verifyCode(auth, linking, '123 456')).resolves.toEqual({ kind: 'verified' })
    expect(auth.verifyOtp).toHaveBeenCalledWith({ email: 'alex@example.test', token: '123456', type: 'email_change' })
    await verifyCode(auth, { ...linking, verify: 'email' }, '654321')
    expect(auth.verifyOtp).toHaveBeenLastCalledWith({ email: 'alex@example.test', token: '654321', type: 'email' })
  })

  it('refuses a short code locally and explains an expired or wrong one', async () => {
    const auth = fakeAuth()
    await expect(verifyCode(auth, linking, '12345')).resolves.toEqual({ kind: 'failed', message: 'The code is 6 digits.' })
    expect(auth.verifyOtp).not.toHaveBeenCalled()

    const expired = fakeAuth({ verifyOtp: vi.fn<EmailAuth['verifyOtp']>(fails('Token has expired or is invalid', 'otp_expired')) })
    await expect(verifyCode(expired, linking, '123456')).resolves.toEqual({
      kind: 'failed',
      message: "That code didn't match or has expired. Request a new one.",
    })
  })
})

describe('isPlausibleEmail', () => {
  it('wants one @ with something on both sides and no spaces', () => {
    expect(isPlausibleEmail('alex@example.test')).toBe(true)
    expect(isPlausibleEmail('  alex@example.test ')).toBe(true)
    for (const bad of ['', 'alex', '@example.test', 'alex@', 'a@@b', 'alex @example.test']) {
      expect(isPlausibleEmail(bad)).toBe(false)
    }
  })
})
