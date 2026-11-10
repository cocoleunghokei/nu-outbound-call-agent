import { describe, expect, it } from 'vitest'
import { fakeBriefRecords } from './fakeBriefs'
import { checkPhone, formatUsPhone, type PhoneError } from './phone'

function expectOk(raw: string, e164: string) {
  expect(checkPhone(raw)).toEqual({ ok: true, e164 })
}

function expectError(raw: string, reason: PhoneError) {
  const result = checkPhone(raw)
  expect(result.ok, `${JSON.stringify(raw)} should be rejected`).toBe(false)
  if (!result.ok) {
    expect(result.reason).toBe(reason)
    expect(result.message.length).toBeGreaterThan(0)
  }
}

describe('checkPhone: valid US numbers', () => {
  it.each([
    ['6175550123', '+16175550123'],
    ['(617) 555-0123', '+16175550123'],
    ['617-555-0123', '+16175550123'],
    ['617.555.0123', '+16175550123'],
    ['  617 555 0123  ', '+16175550123'],
    ['1 617 555 0123', '+16175550123'],
    ['16175550123', '+16175550123'],
    ['+1 (617) 555-0123', '+16175550123'],
    ['+16175550123', '+16175550123'],
    ['212–555–0199', '+12125550199'], // en dash, as pasted from a website
  ])('accepts %j', (raw, e164) => expectOk(raw, e164))

  it('accepts US territories (Puerto Rico, Guam, US Virgin Islands)', () => {
    expectOk('787 555 0123', '+17875550123')
    expectOk('671 555 0123', '+16715550123')
    expectOk('340 555 0123', '+13405550123')
  })

  it('accepts toll-free business numbers', () => {
    expectOk('1-800-555-0199', '+18005550199')
    expectOk('888 555 0199', '+18885550199')
  })

  it('accepts every phone number in the fake briefs', () => {
    for (const record of fakeBriefRecords) {
      expectOk(record.phone_e164, record.phone_e164)
    }
  })
})

describe('checkPhone: emergency and short codes (checked before normalising)', () => {
  it.each(['911', ' 9-1-1 ', '(911)', '112', '999'])('blocks %j as emergency', (raw) =>
    expectError(raw, 'emergency'),
  )

  it.each(['211', '311', '411', '511', '611', '711', '811', '988'])(
    'blocks %j as a short code or invalid',
    (raw) => {
      const result = checkPhone(raw)
      expect(result.ok).toBe(false)
      if (!result.ok) expect(['short_code', 'invalid']).toContain(result.reason)
    },
  )

  it('blocks N11 short codes', () => {
    expectError('311', 'short_code')
    expectError('411', 'short_code')
  })

  it('rejects 988 (crisis line) and other 3-digit input', () => {
    expectError('988', 'invalid')
    expectError('123', 'invalid')
  })

  it('rejects numbers that use 911 or another N11 code as the area code or exchange', () => {
    expectError('911 555 0123', 'invalid')
    expectError('617 911 0123', 'invalid')
    expectError('411 555 0123', 'invalid')
  })
})

describe('checkPhone: non-US numbers', () => {
  it.each([
    ['Toronto', '416 555 0123'],
    ['Vancouver', '+1 604 555 0123'],
    ['Montreal', '1-514-555-0123'],
    ['Canada non-geographic', '600 555 0123'],
    ['Jamaica', '876 555 0123'],
    ['Bahamas', '+1 242 555 0123'],
    ['Dominican Republic', '809 555 0123'],
    ['Bermuda', '441 555 0123'],
  ])('rejects %s (+1 but not US)', (_where, raw) => expectError(raw, 'not_us'))

  it.each(['+44 20 7946 0958', '+852 2123 4567', '+61 2 9876 5432', '011 44 20 7946 0958'])(
    'rejects %j (another country code)',
    (raw) => expectError(raw, 'not_us'),
  )
})

describe('checkPhone: premium and personal numbers', () => {
  it('rejects 900 premium-rate numbers', () => {
    expectError('1-900-555-0123', 'premium')
  })

  it('rejects 5XX personal and 700/710 special numbers', () => {
    expectError('500 555 0123', 'personal')
    expectError('533 555 0123', 'personal')
    expectError('700 555 0123', 'personal')
    expectError('710 555 0123', 'personal')
  })
})

describe('checkPhone: malformed input', () => {
  it('rejects empty input', () => {
    expectError('', 'empty')
    expectError('   ', 'empty')
  })

  it.each([
    '617555012', // 9 digits
    '61755501234', // 11 digits not starting with 1
    '+1 617 555 01234', // too long
    '2 617 555 0123', // 11 digits with a wrong leading digit
    '1-800-FLOWERS', // letters
    '617-555-0123 x12', // extension
    '+1 617 555 0123+', // stray plus
    '017 555 0123', // area code starts with 0
    '117 555 0123', // area code starts with 1
    '617 055 0123', // exchange starts with 0
    '617 155 0123', // exchange starts with 1
  ])('rejects %j as invalid', (raw) => expectError(raw, 'invalid'))
})

describe('formatUsPhone', () => {
  it('formats a US E.164 number for display', () => {
    expect(formatUsPhone('+16175550123')).toBe('(617) 555-0123')
  })

  it('leaves anything else unchanged', () => {
    expect(formatUsPhone('+442079460958')).toBe('+442079460958')
    expect(formatUsPhone('not a number')).toBe('not a number')
  })

  it('round-trips with checkPhone', () => {
    const result = checkPhone('(617) 555-0123')
    expect(result.ok && formatUsPhone(result.e164)).toBe('(617) 555-0123')
  })
})
