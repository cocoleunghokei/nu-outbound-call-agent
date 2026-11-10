import { describe, expect, it } from 'vitest'
import { CUSTOM_TASK_LINE, buildBrief } from './brief'
import { chatReducer, completedDraft, initialChatState, type CallDraft, type ChatAction, type ChatContext } from './chat'
import { contractExampleBrief } from './contractExample'
import { fakeBriefRecords, fakeProfile, fakeTemplates } from './fakeBriefs'
import type { Brief, CallTemplate, ProfileField } from './types'

const ctx: ChatContext = { profile: fakeProfile, templates: fakeTemplates }
const send = (text: string): ChatAction => ({ type: 'send', text })
const done = (label: string): ChatAction => ({ type: 'list_done', label })

function template(key: string): CallTemplate {
  const t = fakeTemplates.find((x) => x.key === key)
  if (!t) throw new Error(`no template ${key}`)
  return t
}

/** Run the real chat to the end and return its draft, so the test covers chat -> brief. */
function chatDraft(context: ChatContext, actions: ChatAction[]): CallDraft {
  const state = actions.reduce(chatReducer(context), initialChatState(context))
  const draft = completedDraft(state)
  if (!draft) throw new Error(`chat did not finish; waiting for ${state.awaiting} ${state.fieldKey ?? state.listKey ?? ''}`)
  return draft
}

/** A draft built by hand, for edge cases the chat itself prevents. */
function draft(overrides: Partial<CallDraft> = {}): CallDraft {
  return {
    template_key: null,
    goal: 'Ask if they can hold a cake for pickup tomorrow.',
    business_name: 'Sunrise Bakery',
    phone_e164: '+16175550122',
    answers: {},
    profile_additions: [],
    constraints: [],
    preferences: [],
    fallbacks: [],
    never_agree: [],
    done_lists: ['constraints', 'preferences', 'fallbacks', 'never_agree'],
    ...overrides,
  }
}

/** The brief.v1 rules from contracts/brief.v1.json (PR #19) plus the privacy rule. */
function expectValidBrief(brief: Brief, profile: ProfileField[]) {
  expect(Object.keys(brief).sort()).toEqual(Object.keys(contractExampleBrief).sort())
  expect(brief.goal.length).toBeGreaterThan(0)
  expect(brief.business_name.length).toBeGreaterThan(0)
  expect(brief.phone_e164).toMatch(/^\+[1-9][0-9]{1,14}$/)
  expect(brief.task_line.length).toBeLessThanOrEqual(120)
  for (const list of ['constraints', 'preferences', 'fallbacks', 'never_agree'] as const) {
    for (const item of brief[list]) expect(Object.keys(item)).toEqual(['text'])
  }
  for (const v of Object.values(brief.task_vars)) {
    expect(Object.keys(v)).toHaveLength(1)
    if ('field_ref' in v) {
      expect(profile.find((f) => f.key === v.field_ref)?.sharing).toBe('share_freely')
    }
  }
  const askFirst = profile.filter((f) => f.sharing === 'ask_first')
  const json = JSON.stringify(brief)
  for (const f of askFirst) expect(json, `ask-first value of ${f.key} leaked`).not.toContain(f.value)
  for (const key of brief.ask_first_keys) expect(brief.share_freely_keys).not.toContain(key)
}

describe('buildBrief from the real chat (Step 5 "Done when")', () => {
  it('restaurant: all four lists land in the brief in brief.v1 shape, backups in order', () => {
    const d = chatDraft(ctx, [
      { type: 'pick_template', templateKey: 'restaurant_booking' },
      send('Corner Table Bistro'),
      send('617 555 0100'),
      send('6'),
      send('Friday'),
      send('7pm'),
      send('Between 6:30pm and 8pm'),
      done("That's all"),
      send('Window seat'),
      done("That's all"),
      send('Saturday same time'),
      send('Friday at 8:30pm'),
      done("That's all"),
      send('A deposit or fee'),
      done("That's all"),
    ])
    const { brief, missing_fields } = buildBrief(d, template('restaurant_booking'), fakeProfile)
    expectValidBrief(brief, fakeProfile)
    expect(missing_fields).toEqual([])
    expect(brief.constraints).toEqual([{ text: 'Between 6:30pm and 8pm' }])
    expect(brief.preferences).toEqual([{ text: 'Window seat' }])
    expect(brief.fallbacks).toEqual([{ text: 'Saturday same time' }, { text: 'Friday at 8:30pm' }])
    expect(brief.never_agree).toEqual([{ text: 'A deposit or fee' }])
    expect(brief.task_line).toBe(template('restaurant_booking').task_line)
    expect(brief.task_vars).toEqual({
      party_size: { literal: '6' },
      day: { literal: 'Friday' },
      time: { literal: '7pm' },
    })
    expect(brief.share_freely_keys).toEqual(['calling_name', 'phone'])
    expect(brief.ask_first_keys).toEqual([])
  })

  it('doctor: DOB is listed as Ask me first and never referenced or copied into the brief', () => {
    const d = chatDraft(ctx, [
      { type: 'pick_template', templateKey: 'doctor_booking' },
      send('Back Bay Family Clinic'),
      send('617 555 0111'),
      send('Dr. Lee'),
      send('Existing'),
      send('Check-up'),
      send('Before 3pm'),
      done("That's all"),
      done('No preferences'),
      send('Friday morning'),
      done("That's all"),
      done('Nothing else'),
    ])
    const { brief, missing_fields } = buildBrief(d, template('doctor_booking'), fakeProfile)
    expectValidBrief(brief, fakeProfile)
    expect(missing_fields).toEqual([])
    expect(brief.task_vars).toEqual({ provider_name: { literal: 'Dr. Lee' } })
    expect(brief.share_freely_keys).toEqual(['calling_name', 'phone'])
    expect(brief.ask_first_keys).toEqual(['date_of_birth'])
    expect(brief.never_agree).toEqual([])
  })

  it('custom call: uses the tech doc task line with the goal as {goal_summary}', () => {
    const d = chatDraft(ctx, [
      { type: 'pick_custom' },
      send('Ask if they can hold a cake for pickup tomorrow.'),
      send('Sunrise Bakery'),
      send('617 555 0122'),
      done('No limits'),
      done('No preferences'),
      send('Any cake they have ready'),
      done("That's all"),
      done('Nothing else'),
    ])
    const { brief, missing_fields } = buildBrief(d, undefined, fakeProfile)
    expectValidBrief(brief, fakeProfile)
    expect(missing_fields).toEqual([])
    expect(brief.template_key).toBeNull()
    expect(brief.task_line).toBe(CUSTOM_TASK_LINE)
    expect(brief.task_vars).toEqual({ goal_summary: { literal: 'ask if they can hold a cake for pickup tomorrow' } })
    expect(brief.fallbacks).toEqual([{ text: 'Any cake they have ready' }])
  })
})

describe('buildBrief: missing fields', () => {
  it('never treats an empty fallbacks list as missing (PC-5 prompts, it does not require)', () => {
    const { brief, missing_fields } = buildBrief(draft({ fallbacks: [] }), undefined, fakeProfile)
    expect(brief.fallbacks).toEqual([])
    expect(missing_fields).not.toContain('fallbacks')
    expect(missing_fields).toEqual([])
  })

  it('lists unanswered template details as missing and leaves them out of task_vars', () => {
    const d = draft({ template_key: 'restaurant_booking', goal: 'Book a table', answers: { party_size: '4' } })
    const { brief, missing_fields } = buildBrief(d, template('restaurant_booking'), fakeProfile)
    expect(missing_fields).toEqual(['day', 'time'])
    expect(brief.task_vars).toEqual({ party_size: { literal: '4' } })
  })

  it('references a Share-freely profile field with field_ref, never its value', () => {
    const t: CallTemplate = { ...template('restaurant_booking'), task_line: 'Book a table under {calling_name}.' }
    const { brief } = buildBrief(draft({ template_key: t.key, goal: 'Book' }), t, fakeProfile)
    expect(brief.task_vars).toEqual({ calling_name: { field_ref: 'calling_name' } })
    expect(JSON.stringify(brief)).not.toContain('"Alex"')
  })

  it('never references an Ask-me-first field in the task line: it is reported missing instead', () => {
    const t: CallTemplate = { ...template('doctor_booking'), task_line: 'Patient born {date_of_birth}.' }
    const { brief, missing_fields } = buildBrief(draft({ template_key: t.key, goal: 'Book' }), t, fakeProfile)
    expect(brief.task_vars).toEqual({})
    expect(missing_fields).toContain('date_of_birth')
  })

  it('reports a missing calling name, since the opening line needs it (ON-3, DC-1)', () => {
    const noName = fakeProfile.filter((f) => f.key !== 'calling_name')
    expect(buildBrief(draft(), undefined, noName).missing_fields).toContain('calling_name')
  })

  it('uses profile fields added during the chat, with their default sharing', () => {
    const noDob = fakeProfile.filter((f) => f.key !== 'date_of_birth')
    const d = draft({
      template_key: 'doctor_booking',
      goal: 'Book',
      answers: { provider_name: 'Dr. Lee', patient_status: 'New', reason: 'Check-up' },
      profile_additions: [{ key: 'date_of_birth', value: '2004-03-15', sharing: 'ask_first' }],
    })
    const { brief, missing_fields } = buildBrief(d, template('doctor_booking'), noDob)
    expect(missing_fields).toEqual([])
    expect(brief.ask_first_keys).toEqual(['date_of_birth'])
    expect(JSON.stringify(brief)).not.toContain('2004-03-15')
  })
})

describe('buildBrief: copies', () => {
  it('copies the list items, so editing the brief later never changes the draft', () => {
    const d = draft({ constraints: [{ text: 'Before 6pm' }] })
    const { brief } = buildBrief(d, undefined, fakeProfile)
    expect(brief.constraints).toEqual(d.constraints)
    expect(brief.constraints[0]).not.toBe(d.constraints[0])
  })
})

describe('fake brief records', () => {
  it('never list fallbacks as a missing field', () => {
    for (const r of fakeBriefRecords) expect(r.missing_fields, r.business_name).not.toContain('fallbacks')
  })
})
