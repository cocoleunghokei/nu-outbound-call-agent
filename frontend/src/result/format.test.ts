import { describe, expect, it } from 'vitest'
import { actionLabel, disclosureLabel, fieldLabel } from './format'

describe('labels for keys that come from the server', () => {
  it('uses the known labels', () => {
    expect(fieldLabel('date_of_birth')).toBe('Date of birth')
    expect(actionLabel('take_fallback')).toBe('Took a fallback')
    expect(disclosureLabel('spoken')).toBe('Said on the call')
  })

  it('never reads Object.prototype, so "__proto__" or "constructor" cannot blank the page', () => {
    expect(fieldLabel('__proto__')).toBe('  proto  ')
    expect(fieldLabel('constructor')).toBe('constructor')
    expect(typeof fieldLabel('toString')).toBe('string')
    expect(actionLabel('constructor')).toBe('Acted')
    expect(disclosureLabel('__proto__')).toBe('Unknown')
  })

  it('shows unknown field keys as written, with underscores as spaces', () => {
    expect(fieldLabel('loyalty_number')).toBe('loyalty number')
  })
})
