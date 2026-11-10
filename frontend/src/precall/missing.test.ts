import { describe, expect, it } from 'vitest'
import { fakeProfile, fakeTemplates } from './fakeBriefs'
import {
  defaultSharing,
  describeProfileKeys,
  hasProfileValue,
  knownProfileFields,
  missingTemplateFields,
  withProfileField,
} from './missing'
import type { CallTemplate, ProfileField } from './types'

function template(key: string): CallTemplate {
  const t = fakeTemplates.find((x) => x.key === key)
  if (!t) throw new Error(`no template ${key}`)
  return t
}

const restaurant = template('restaurant_booking')
const doctor = template('doctor_booking')
const keys = (fields: { key: string }[]) => fields.map((f) => f.key)

describe('missingTemplateFields (PC-4)', () => {
  it('restaurant: with a full profile, asks only for the call details', () => {
    expect(keys(missingTemplateFields(restaurant, fakeProfile, {}))).toEqual(['party_size', 'day', 'time'])
  })

  it('doctor: never re-asks for profile fields, including the Ask-me-first DOB', () => {
    const missing = keys(missingTemplateFields(doctor, fakeProfile, {}))
    expect(missing).toEqual(['provider_name', 'patient_status', 'reason'])
    expect(missing).not.toContain('date_of_birth')
    expect(missing).not.toContain('calling_name')
  })

  it('never asks for any profile field the profile already has, in any template', () => {
    for (const t of fakeTemplates) {
      for (const field of missingTemplateFields(t, fakeProfile, {})) {
        if (field.source === 'profile') expect(hasProfileValue(fakeProfile, field.key)).toBe(false)
      }
    }
  })

  it('asks for a profile field when the profile lacks it', () => {
    const noDob = fakeProfile.filter((f) => f.key !== 'date_of_birth')
    expect(keys(missingTemplateFields(doctor, noDob, {}))).toContain('date_of_birth')
  })

  it('treats a blank profile value as missing', () => {
    const blankPhone = fakeProfile.map((f) => (f.key === 'phone' ? { ...f, value: '  ' } : f))
    expect(keys(missingTemplateFields(restaurant, blankPhone, {}))).toContain('phone')
  })

  it('drops each call detail once it is answered, keeping template order', () => {
    expect(keys(missingTemplateFields(restaurant, fakeProfile, { day: 'Friday' }))).toEqual(['party_size', 'time'])
    expect(
      missingTemplateFields(restaurant, fakeProfile, { party_size: '6', day: 'Friday', time: '7pm' }),
    ).toEqual([])
  })

  it('treats a blank answer as missing', () => {
    expect(keys(missingTemplateFields(restaurant, fakeProfile, { party_size: ' ' }))).toContain('party_size')
  })

  it('asks for everything with an empty profile', () => {
    expect(missingTemplateFields(doctor, [], {})).toHaveLength(doctor.required_fields.length)
  })
})

describe('knownProfileFields', () => {
  it('lists the profile fields the chat will not ask for', () => {
    expect(knownProfileFields(restaurant, fakeProfile)).toEqual(['calling_name', 'phone'])
    expect(knownProfileFields(doctor, fakeProfile)).toEqual(['calling_name', 'date_of_birth'])
    expect(knownProfileFields(doctor, [])).toEqual([])
  })
})

describe('describeProfileKeys', () => {
  it('turns keys into a short phrase', () => {
    expect(describeProfileKeys(['calling_name'])).toBe('your name')
    expect(describeProfileKeys(['calling_name', 'phone'])).toBe('your name and phone number')
    expect(describeProfileKeys(['calling_name', 'phone', 'date_of_birth'])).toBe(
      'your name, phone number and date of birth',
    )
  })

  it('merges keys with the same label and handles unknown keys', () => {
    expect(describeProfileKeys(['full_name', 'calling_name'])).toBe('your name')
    expect(describeProfileKeys(['favourite_colour'])).toBe('your favourite colour')
    expect(describeProfileKeys([])).toBe('')
  })
})

describe('defaultSharing (ON-5)', () => {
  it('defaults DOB, address and insurance to Ask me first', () => {
    expect(defaultSharing('date_of_birth')).toBe('ask_first')
    expect(defaultSharing('address')).toBe('ask_first')
    expect(defaultSharing('insurance_member_id')).toBe('ask_first')
  })

  it('defaults everything else to Share freely, and the calling name is always shared', () => {
    expect(defaultSharing('phone')).toBe('share_freely')
    expect(defaultSharing('calling_name')).toBe('share_freely')
  })
})

describe('withProfileField', () => {
  it('adds a new field with its default sharing', () => {
    const p = withProfileField([], 'date_of_birth', '2004-03-15')
    expect(p).toEqual<ProfileField[]>([{ key: 'date_of_birth', value: '2004-03-15', sharing: 'ask_first' }])
  })

  it('replaces an existing field in place without touching the others', () => {
    const p = withProfileField(fakeProfile, 'phone', '+16175550199')
    expect(p).toHaveLength(fakeProfile.length)
    expect(p.find((f) => f.key === 'phone')?.value).toBe('+16175550199')
    expect(keys(p)).toEqual(keys(fakeProfile))
  })
})
