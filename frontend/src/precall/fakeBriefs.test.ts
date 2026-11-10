import { describe, expect, it } from 'vitest'
import { contractExampleBrief } from './contractExample'
import { fakeBriefRecords, fakeProfile, fakeTemplates } from './fakeBriefs'
import type { Brief } from './types'

// Rules from contracts/brief.v1.json (PR #19).
const E164 = /^\+[1-9][0-9]{1,14}$/
const TASK_LINE_MAX = 120
const PLACEHOLDER = /\{(\w+)\}/g
const TEXT_LISTS = ['constraints', 'preferences', 'fallbacks', 'never_agree'] as const

const profileKeys = new Set(fakeProfile.map((f) => f.key))
const askFirstProfileKeys = new Set(
  fakeProfile.filter((f) => f.sharing === 'ask_first').map((f) => f.key),
)

function placeholders(taskLine: string): string[] {
  return [...taskLine.matchAll(PLACEHOLDER)].map((m) => m[1])
}

describe.each(fakeBriefRecords.map((r) => [r.business_name, r] as const))(
  'fake brief: %s',
  (_name, record) => {
    const brief: Brief = record.brief

    it('has exactly the same fields as the contract example', () => {
      expect(Object.keys(brief).sort()).toEqual(Object.keys(contractExampleBrief).sort())
    })

    it('matches its briefs-table row', () => {
      expect(brief.goal).toBe(record.goal)
      expect(brief.business_name).toBe(record.business_name)
      expect(brief.phone_e164).toBe(record.phone_e164)
      expect(brief.template_key).toBe(record.template_key)
    })

    it('has a non-empty goal and business name, and an E.164 phone number', () => {
      expect(brief.goal.length).toBeGreaterThan(0)
      expect(brief.business_name.length).toBeGreaterThan(0)
      expect(brief.phone_e164).toMatch(E164)
    })

    it('keeps task_line within 120 characters', () => {
      expect(brief.task_line.length).toBeLessThanOrEqual(TASK_LINE_MAX)
    })

    it('resolves every task_line placeholder from task_vars', () => {
      for (const name of placeholders(brief.task_line)) {
        expect(brief.task_vars, `missing task_var {${name}}`).toHaveProperty(name)
      }
    })

    it('has each task_var as exactly one of literal or field_ref', () => {
      for (const v of Object.values(brief.task_vars)) {
        const keys = Object.keys(v)
        expect(keys).toHaveLength(1)
        expect(['literal', 'field_ref']).toContain(keys[0])
        if ('field_ref' in v) {
          expect(profileKeys.has(v.field_ref)).toBe(true)
          // An ask-first value must never reach the in-call prompt (ON-5, DC-6).
          expect(askFirstProfileKeys.has(v.field_ref)).toBe(false)
        }
      }
    })

    it('stores constraints, preferences, fallbacks and never-agree items as { text } lists', () => {
      for (const list of TEXT_LISTS) {
        for (const item of brief[list]) {
          expect(Object.keys(item)).toEqual(['text'])
          expect(item.text.length).toBeGreaterThan(0)
        }
      }
    })

    it('respects the sharing settings: calling_name always shared, ask-first keys match the profile', () => {
      expect(brief.share_freely_keys).toContain('calling_name')
      for (const key of brief.share_freely_keys) {
        expect(askFirstProfileKeys.has(key), `${key} is Ask me first`).toBe(false)
      }
      for (const key of brief.ask_first_keys) {
        expect(askFirstProfileKeys.has(key), `${key} is not Ask me first`).toBe(true)
        expect(brief.share_freely_keys).not.toContain(key)
      }
    })

    it('holds field keys only, never profile values', () => {
      const text = JSON.stringify(brief)
      for (const field of fakeProfile) {
        expect(text, `brief contains the value of ${field.key}`).not.toContain(field.value)
      }
    })

    it('uses a known template, or null for a custom call', () => {
      if (brief.template_key !== null) {
        expect(fakeTemplates.map((t) => t.key)).toContain(brief.template_key)
      }
    })
  },
)

describe('contract example (copy of brief.v1.valid.json)', () => {
  it('passes the same basic rules as the fake briefs', () => {
    expect(contractExampleBrief.phone_e164).toMatch(E164)
    expect(contractExampleBrief.task_line.length).toBeLessThanOrEqual(TASK_LINE_MAX)
    for (const name of placeholders(contractExampleBrief.task_line)) {
      expect(contractExampleBrief.task_vars).toHaveProperty(name)
    }
  })
})

describe('fake data covers the flows the screens need', () => {
  it('has a complete brief, a brief with missing fields, and a custom call', () => {
    expect(fakeBriefRecords.some((r) => r.missing_fields.length === 0)).toBe(true)
    expect(fakeBriefRecords.some((r) => r.missing_fields.length > 0)).toBe(true)
    expect(fakeBriefRecords.some((r) => r.template_key === null)).toBe(true)
  })

  it('has the two P0 templates (PC-2)', () => {
    expect(fakeTemplates.map((t) => t.key).sort()).toEqual(['doctor_booking', 'restaurant_booking'])
  })

  it('has a calling name that is always shared (ON-3, ON-5)', () => {
    const callingName = fakeProfile.find((f) => f.key === 'calling_name')
    expect(callingName?.sharing).toBe('share_freely')
  })

  it('keeps DOB and address Ask me first by default (ON-5)', () => {
    expect(askFirstProfileKeys).toEqual(new Set(['date_of_birth', 'address']))
  })

  it('only marks profile fields as source "profile" in templates', () => {
    for (const t of fakeTemplates) {
      for (const f of t.required_fields.filter((rf) => rf.source === 'profile')) {
        expect(profileKeys.has(f.key), `${t.key}: ${f.key}`).toBe(true)
      }
    }
  })

  it('gives each template a task_line within 120 characters', () => {
    for (const t of fakeTemplates) {
      expect(t.task_line.length).toBeLessThanOrEqual(TASK_LINE_MAX)
    }
  })
})
