import { describe, expect, it, vi } from 'vitest'
import {
  defaultCallingName,
  EMPTY_PROFILE,
  fieldValue,
  isOnboarded,
  isPlausibleEmail,
  loadProfile,
  saveOnboarding,
  validateOnboarding,
  type LoadedProfile,
  type ProfileStore,
} from './profile'

const complete = { fullName: 'Alex Chen', callingName: '', phone: '(617) 555-0142', email: 'alex.chen@example.com' }

function fakeStore(overrides: Partial<ProfileStore> = {}): ProfileStore {
  return {
    load: vi.fn<ProfileStore['load']>(async () => EMPTY_PROFILE),
    save: vi.fn<ProfileStore['save']>(async () => {}),
    ...overrides,
  }
}

describe('validateOnboarding', () => {
  it('stores the four fields as share freely, the phone as E.164 and the calling name defaulted', () => {
    expect(validateOnboarding(complete)).toEqual({
      ok: true,
      fields: [
        { key: 'full_name', value: 'Alex Chen', sharing: 'share_freely' },
        { key: 'calling_name', value: 'Alex', sharing: 'share_freely' },
        { key: 'phone', value: '+16175550142', sharing: 'share_freely' },
        { key: 'email', value: 'alex.chen@example.com', sharing: 'share_freely' },
      ],
    })
    const edited = validateOnboarding({ ...complete, callingName: ' Alex C. ' })
    expect(edited.ok && edited.fields[1]).toEqual({ key: 'calling_name', value: 'Alex C.', sharing: 'share_freely' })
    expect(defaultCallingName('  Jordan   Lee ')).toBe('Jordan')
    expect(defaultCallingName('')).toBe('')
  })

  it('refuses a blank name, a bad phone and a bad email with plain words', () => {
    expect(validateOnboarding({ ...complete, fullName: '  ' })).toEqual({ ok: false, problem: 'Enter your name.' })
    expect(validateOnboarding({ ...complete, phone: '' })).toMatchObject({ ok: false, problem: /10-digit US phone/ })
    expect(validateOnboarding({ ...complete, phone: '555' })).toMatchObject({ ok: false, problem: /10-digit US phone/ })
    expect(validateOnboarding({ ...complete, phone: '911' })).toMatchObject({ ok: false, problem: /emergency/ })
    expect(validateOnboarding({ ...complete, email: 'alex.example.com' })).toMatchObject({ ok: false, problem: /email address/ })
  })
})

describe('isPlausibleEmail', () => {
  it('wants one @ with something on both sides and no spaces', () => {
    expect(isPlausibleEmail(' alex@example.com ')).toBe(true)
    expect(isPlausibleEmail('alex@')).toBe(false)
    expect(isPlausibleEmail('@example.com')).toBe(false)
    expect(isPlausibleEmail('a@b@c')).toBe(false)
    expect(isPlausibleEmail('alex @example.com')).toBe(false)
  })
})

describe('saveOnboarding', () => {
  it('writes the rows with a timestamp and reports the saved profile', async () => {
    const store = fakeStore()
    const now = () => new Date('2026-11-18T12:00:00Z')
    const outcome = await saveOnboarding(store, 'user-a', complete, now)
    expect(outcome).toMatchObject({ kind: 'saved', profile: { onboardedAt: '2026-11-18T12:00:00.000Z' } })
    expect(store.save).toHaveBeenCalledWith(
      'user-a',
      expect.arrayContaining([{ key: 'calling_name', value: 'Alex', sharing: 'share_freely' }]),
      '2026-11-18T12:00:00.000Z',
    )
    if (outcome.kind === 'saved') {
      expect(isOnboarded(outcome.profile)).toBe(true)
      expect(fieldValue(outcome.profile.fields, 'email')).toBe('alex.chen@example.com')
    }
  })

  it('does not write when the form is invalid, and turns a store error into a message', async () => {
    const store = fakeStore()
    await expect(saveOnboarding(store, 'user-a', { ...complete, email: 'nope' })).resolves.toMatchObject({ kind: 'failed' })
    expect(store.save).not.toHaveBeenCalled()

    const broken = fakeStore({ save: async () => Promise.reject(new Error('new row violates row-level security policy')) })
    await expect(saveOnboarding(broken, 'user-a', complete)).resolves.toEqual({
      kind: 'failed',
      message: "I couldn't save your profile (new row violates row-level security policy). Try again.",
    })
  })
})

describe('loadProfile', () => {
  it('returns what the store holds, and an empty, not-onboarded profile when the read fails', async () => {
    const stored: LoadedProfile = {
      onboardedAt: '2026-11-18T12:00:00.000Z',
      fields: [{ key: 'email', value: 'alex.chen@example.com', sharing: 'share_freely' }],
    }
    await expect(loadProfile(fakeStore({ load: async () => stored }), 'user-a')).resolves.toEqual(stored)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const offline = fakeStore({ load: async () => Promise.reject(new Error('Failed to fetch')) })
    await expect(loadProfile(offline, 'user-a')).resolves.toEqual(EMPTY_PROFILE)
    expect(isOnboarded(EMPTY_PROFILE)).toBe(false)
    expect(fieldValue(EMPTY_PROFILE.fields, 'email')).toBeNull()
    warn.mockRestore()
  })
})
