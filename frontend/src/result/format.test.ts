import { describe, expect, it } from 'vitest'
import { actionLabel, approvalNote, changeSummary, disclosureLabel, fieldLabel, formatCents, isChange, summaryLabel } from './format'
import type { ResultItem } from './types'

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

const row = (changes: Partial<ResultItem> = {}): ResultItem => ({
  label: 'Cake',
  asked: 'Mochi cake',
  got: 'Marshmallow cake',
  status: 'changed',
  reason: null,
  approvedBy: null,
  costCents: null,
  evidence: [],
  verified: true,
  ...changes,
})

describe('asked-vs-got summary', () => {
  // The coco resto cake call: two changes and a $3 ice pack fee.
  const cakeCall = [
    row(),
    row({ label: 'Pickup', asked: 'Wed by 10:00 AM', got: 'Wed 12:00 PM' }),
    row({ label: 'Ice pack', asked: null, got: '+$3.00', status: 'extra', costCents: 300 }),
    row({ label: 'Topper, candles, knife', got: 'All included', status: 'as_asked' }),
  ]

  it('counts every row that is not "as asked", and adds up the fees', () => {
    expect(changeSummary(cakeCall)).toEqual({ changes: 3, extraCents: 300 })
    expect(summaryLabel(cakeCall)).toBe('3 changes · +$3')
  })

  it('says "1 change" in the singular, shows cents when there are some, and has no fee part without one', () => {
    expect(summaryLabel([row()])).toBe('1 change')
    expect(summaryLabel([row({ costCents: 350 })])).toBe('1 change · +$3.50')
  })

  it('says "As you asked" when nothing changed', () => {
    expect(summaryLabel([row({ status: 'as_asked' })])).toBe('As you asked')
    expect(summaryLabel([])).toBe('As you asked')
  })

  it('counts a status this version does not know as a change', () => {
    expect(isChange(row({ status: 'something_new' }))).toBe(true)
    expect(isChange(row({ status: 'as_asked' }))).toBe(false)
  })

  it('formats cents as dollars', () => {
    expect(formatCents(300)).toBe('$3.00')
    expect(formatCents(1250)).toBe('$12.50')
    expect(formatCents(5)).toBe('$0.05')
  })

  it('notes who allowed a changed row, and nothing for a row that went as asked', () => {
    expect(approvalNote(row({ approvedBy: 'user' }))).toBe('You approved this on the call.')
    expect(approvalNote(row({ approvedBy: 'brief' }))).toBe('Within your brief.')
    expect(approvalNote(row({ approvedBy: 'none' }))).toBe('Not approved. Check it.')
    expect(approvalNote(row({ approvedBy: null }))).toBeNull()
    expect(approvalNote(row({ status: 'as_asked', approvedBy: 'none' }))).toBeNull()
    expect(approvalNote(row({ approvedBy: 'constructor' }))).toBeNull()
  })
})
