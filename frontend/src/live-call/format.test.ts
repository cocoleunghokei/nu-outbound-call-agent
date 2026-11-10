import { describe, expect, it } from 'vitest'
import { formatClock, formatPhone, initials } from './format'

describe('formatClock', () => {
  it('formats milliseconds as mm:ss', () => {
    expect(formatClock(0)).toBe('00:00')
    expect(formatClock(75_000)).toBe('01:15')
    expect(formatClock(900_000)).toBe('15:00')
  })

  it('rounds down partial seconds', () => {
    expect(formatClock(1_999)).toBe('00:01')
  })

  it('shows negative durations (clock skew) as 00:00', () => {
    expect(formatClock(-5_000)).toBe('00:00')
  })
})

describe('formatPhone', () => {
  it('formats a US number', () => {
    expect(formatPhone('+16175550142')).toBe('(617) 555-0142')
  })

  it('shows other numbers as given', () => {
    expect(formatPhone('+442071838750')).toBe('+442071838750')
    expect(formatPhone('not a number')).toBe('not a number')
  })
})

describe('initials', () => {
  it('takes the first letters of the first two words', () => {
    expect(initials('Brookline Family Medicine')).toBe('BF')
    expect(initials('tatte')).toBe('T')
  })

  it('handles extra spaces and empty names', () => {
    expect(initials('  Juniper   Ramen ')).toBe('JR')
    expect(initials('')).toBe('?')
  })
})
