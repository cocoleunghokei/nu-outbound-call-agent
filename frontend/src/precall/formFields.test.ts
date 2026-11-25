import { describe, expect, it } from 'vitest'
import { dayError, formatAnswer, nowTimeValue, parseAnswer, suggestionsFor, timeError, todayValue, widgetFor } from './formFields'

// Wed Nov 25 2026, 3:07 PM local time.
const NOW = new Date(2026, 10, 25, 15, 7)

describe('day and time checks', () => {
  it('writes today and now the way date and time inputs do', () => {
    expect(todayValue(NOW)).toBe('2026-11-25')
    expect(nowTimeValue(NOW)).toBe('15:07')
  })

  it('accepts today and later days, rejects earlier days, ignores an empty day', () => {
    expect(dayError('2026-11-25', NOW)).toBeNull()
    expect(dayError('2026-12-01', NOW)).toBeNull()
    expect(dayError('2026-11-24', NOW)).not.toBeNull()
    expect(dayError('', NOW)).toBeNull()
  })

  it('rejects a time that already passed today, and only today', () => {
    expect(timeError('2026-11-25', '14:00', NOW)).not.toBeNull()
    expect(timeError('2026-11-25', '15:07', NOW)).toBeNull()
    expect(timeError('2026-11-25', '19:00', NOW)).toBeNull()
    expect(timeError('2026-11-26', '09:00', NOW)).toBeNull()
    expect(timeError('2026-11-25', '', NOW)).toBeNull()
  })
})

describe('widgetFor', () => {
  it('gives party size a stepper, day a date picker and time a time picker', () => {
    expect(widgetFor('party_size')).toBe('stepper')
    expect(widgetFor('day')).toBe('date')
    expect(widgetFor('time')).toBe('time')
  })

  it('falls back to a text box for any other field', () => {
    expect(widgetFor('provider_name')).toBe('text')
    expect(widgetFor('something_new')).toBe('text')
  })
})

describe('formatAnswer', () => {
  it('turns a picked date into the words the agent says', () => {
    expect(formatAnswer('date', '2026-12-23')).toBe('December 23')
    expect(formatAnswer('date', '2027-01-03')).toBe('January 3')
  })

  it('turns a picked time into 12-hour words', () => {
    expect(formatAnswer('time', '19:00')).toBe('7 PM')
    expect(formatAnswer('time', '19:30')).toBe('7:30 PM')
    expect(formatAnswer('time', '00:15')).toBe('12:15 AM')
    expect(formatAnswer('time', '12:00')).toBe('12 PM')
    expect(formatAnswer('time', '09:05')).toBe('9:05 AM')
  })

  it('gives an empty answer for an empty or invalid date or time', () => {
    expect(formatAnswer('date', '')).toBe('')
    expect(formatAnswer('date', '2026-13-01')).toBe('')
    expect(formatAnswer('date', 'next friday')).toBe('')
    expect(formatAnswer('time', '')).toBe('')
    expect(formatAnswer('time', '25:00')).toBe('')
    expect(formatAnswer('time', '7pm')).toBe('')
  })

  it('trims text and stepper values', () => {
    expect(formatAnswer('text', '  Dr. Lee ')).toBe('Dr. Lee')
    expect(formatAnswer('stepper', ' 4 ')).toBe('4')
  })
})

describe('parseAnswer', () => {
  it('refills a date input from the words, in the given year', () => {
    expect(parseAnswer('date', 'December 23', 2026)).toBe('2026-12-23')
    expect(parseAnswer('date', 'january 3', 2027)).toBe('2027-01-03')
  })

  it('refills a time input from the words', () => {
    expect(parseAnswer('time', '7 PM')).toBe('19:00')
    expect(parseAnswer('time', '7:30 PM')).toBe('19:30')
    expect(parseAnswer('time', '12 AM')).toBe('00:00')
    expect(parseAnswer('time', '12 PM')).toBe('12:00')
  })

  it('gives an empty value for words it cannot read, so the user picks again', () => {
    expect(parseAnswer('date', 'Friday', 2026)).toBe('')
    expect(parseAnswer('date', 'Smarch 4', 2026)).toBe('')
    expect(parseAnswer('time', '7pm')).toBe('')
    expect(parseAnswer('time', '13 PM')).toBe('')
  })

  it('round-trips what formatAnswer writes', () => {
    expect(parseAnswer('date', formatAnswer('date', '2026-12-23'), 2026)).toBe('2026-12-23')
    expect(parseAnswer('time', formatAnswer('time', '19:30'))).toBe('19:30')
    expect(parseAnswer('time', formatAnswer('time', '08:00'))).toBe('08:00')
  })
})

describe('suggestionsFor', () => {
  it('has suggestions for each P0 template and none for an unknown one', () => {
    expect(suggestionsFor('restaurant_booking').constraints.length).toBeGreaterThan(0)
    expect(suggestionsFor('doctor_booking').fallbacks.length).toBeGreaterThan(0)
    expect(suggestionsFor('nope')).toEqual({ constraints: [], preferences: [], fallbacks: [] })
  })
})
