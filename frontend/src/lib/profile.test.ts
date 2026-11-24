import { describe, expect, it, vi } from 'vitest'
import {
  defaultCallingName,
  editInputFrom,
  isPlausibleBirthDate,
  saveProfileEdit,
  validateProfileEdit,
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

describe('editInputFrom', () => {
  it('fills the form from stored fields and uses the ON-5 defaults for the rest', () => {
    const form = editInputFrom([
      { key: 'full_name', value: 'Alex Chen', sharing: 'share_freely' },
      { key: 'email', value: 'alex@example.com', sharing: 'ask_first' },
    ])
    expect(form).toMatchObject({ fullName: 'Alex Chen', email: 'alex@example.com', phone: '', dateOfBirth: '', address: '' })
    expect(form.sharing).toEqual({
      full_name: 'share_freely',
      phone: 'share_freely',
      email: 'ask_first',
      date_of_birth: 'ask_first',
      address: 'ask_first',
    })
  })
})

describe('isPlausibleBirthDate', () => {
  const today = new Date('2026-11-24T12:00:00Z')
  it('accepts a real past date and refuses nonsense', () => {
    expect(isPlausibleBirthDate('2004-03-15', today)).toBe(true)
    expect(isPlausibleBirthDate('2026-11-24', today)).toBe(true)
    expect(isPlausibleBirthDate('2027-01-01', today)).toBe(false)
    expect(isPlausibleBirthDate('2004-02-30', today)).toBe(false)
    expect(isPlausibleBirthDate('1850-01-01', today)).toBe(false)
    expect(isPlausibleBirthDate('03/15/2004', today)).toBe(false)
    expect(isPlausibleBirthDate('', today)).toBe(false)
  })
})

describe('validateProfileEdit', () => {
  const today = new Date('2026-11-24T12:00:00Z')
  const base = editInputFrom([])
  const input = { ...base, fullName: 'Alex Chen', phone: '(617) 555-0142', email: 'alex@example.com' }

  it('stores the chosen sharing, but the calling name is always share freely', () => {
    const checked = validateProfileEdit({ ...input, sharing: { ...input.sharing, phone: 'ask_first' } }, [], today)
    expect(checked).toEqual({
      ok: true,
      fields: [
        { key: 'full_name', value: 'Alex Chen', sharing: 'share_freely' },
        { key: 'calling_name', value: 'Alex', sharing: 'share_freely' },
        { key: 'phone', value: '+16175550142', sharing: 'ask_first' },
        { key: 'email', value: 'alex@example.com', sharing: 'share_freely' },
      ],
    })
  })

  it('adds the optional fields when filled and refuses a bad date of birth', () => {
    const filled = validateProfileEdit({ ...input, dateOfBirth: '2004-03-15', address: ' 1 Example St ' }, [], today)
    expect(filled.ok && filled.fields.slice(4)).toEqual([
      { key: 'date_of_birth', value: '2004-03-15', sharing: 'ask_first' },
      { key: 'address', value: '1 Example St', sharing: 'ask_first' },
    ])
    expect(validateProfileEdit({ ...input, dateOfBirth: '2099-01-01' }, [], today)).toMatchObject({ ok: false, problem: /date of birth/ })
  })

  it('writes a cleared optional field back blank only if it was stored before', () => {
    const stored = [{ key: 'address', value: '1 Example St', sharing: 'ask_first' as const }]
    const cleared = validateProfileEdit({ ...input, address: '' }, stored, today)
    expect(cleared.ok && cleared.fields.find((f) => f.key === 'address')).toEqual({ key: 'address', value: '', sharing: 'ask_first' })
    const never = validateProfileEdit({ ...input, address: '' }, [], today)
    expect(never.ok && never.fields.some((f) => f.key === 'address')).toBe(false)
  })

  it('reuses the required-field checks', () => {
    expect(validateProfileEdit({ ...input, fullName: '' }, [], today)).toEqual({ ok: false, problem: 'Enter your name.' })
    expect(validateProfileEdit({ ...input, email: 'nope' }, [], today)).toMatchObject({ ok: false, problem: /email address/ })
  })
})

describe('saveProfileEdit', () => {
  const current: LoadedProfile = {
    onboardedAt: '2026-11-01T00:00:00.000Z',
    fields: [{ key: 'insurance', value: 'Fake Health, ID 000', sharing: 'ask_first' }],
  }
  const input = { ...editInputFrom([]), fullName: 'Alex Chen', phone: '(617) 555-0142', email: 'alex@example.com' }

  it('keeps the original onboarding time and the fields the page does not edit', async () => {
    const store = fakeStore()
    const outcome = await saveProfileEdit(store, 'user-1', current, input)
    expect(store.save).toHaveBeenCalledWith('user-1', expect.any(Array), '2026-11-01T00:00:00.000Z')
    expect(outcome.kind).toBe('saved')
    if (outcome.kind === 'saved') {
      expect(outcome.profile.onboardedAt).toBe('2026-11-01T00:00:00.000Z')
      expect(outcome.profile.fields).toContainEqual(current.fields[0])
      expect(outcome.profile.fields.find((f) => f.key === 'full_name')?.value).toBe('Alex Chen')
    }
  })

  it('does not store anything when a check fails, and reports a store error in words', async () => {
    const store = fakeStore()
    expect(await saveProfileEdit(store, 'user-1', current, { ...input, fullName: '' })).toEqual({ kind: 'failed', message: 'Enter your name.' })
    expect(store.save).not.toHaveBeenCalled()
    const broken = fakeStore({ save: vi.fn(async () => { throw new Error('offline') }) })
    expect(await saveProfileEdit(broken, 'user-1', current, input)).toMatchObject({ kind: 'failed', message: /offline/ })
  })
})
