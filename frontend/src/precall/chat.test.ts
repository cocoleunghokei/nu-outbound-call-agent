import { describe, expect, it } from 'vitest'
import {
  ANSWER_MAX,
  BUSINESS_NAME_MAX,
  GOAL_MAX,
  callingNameOf,
  chatReducer,
  completedDraft,
  initialChatState,
  type ChatAction,
  type ChatContext,
  type ChatState,
} from './chat'
import { fakeProfile, fakeTemplates } from './fakeBriefs'

const ctx: ChatContext = { profile: fakeProfile, templates: fakeTemplates }
const reduce = chatReducer(ctx)

function run(...actions: ChatAction[]): ChatState {
  return actions.reduce(reduce, initialChatState(ctx))
}

const send = (text: string): ChatAction => ({ type: 'send', text })
const lastAgent = (s: ChatState) => [...s.messages].reverse().find((m) => m.from === 'agent')?.text ?? ''

describe('chat home (PC-1)', () => {
  it('greets the user by calling name and offers every template plus Custom call', () => {
    const s = initialChatState(ctx)
    expect(s.stage).toBe('home')
    expect(s.messages).toHaveLength(1)
    expect(s.messages[0].text).toContain('Alex')
    expect(s.chips.map((c) => c.label)).toEqual([
      'Restaurant: book a table',
      'Doctor: book an appointment',
      'Custom call',
    ])
  })

  it('gives every message a unique id', () => {
    const s = run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'), send('617 555 0122'))
    const ids = s.messages.map((m) => m.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('template flow', () => {
  it('uses the template as the goal and asks for the business and number first', () => {
    let s = run({ type: 'pick_template', templateKey: 'restaurant_booking' })
    expect(s.stage).toBe('collecting')
    expect(s.draft).toEqual({
      template_key: 'restaurant_booking',
      goal: 'Restaurant: book a table',
      answers: {},
      profile_additions: [],
    })
    expect(s.awaiting).toBe('business_name')
    expect(s.chips).toEqual([])

    s = reduce(s, send('Corner Table Bistro'))
    expect(s.awaiting).toBe('phone')
  })

  it('ignores an unknown template key', () => {
    const start = initialChatState(ctx)
    expect(reduce(start, { type: 'pick_template', templateKey: 'nope' })).toBe(start)
  })
})

describe('missing-context questions (PC-4)', () => {
  const restaurantAtQuestions = () =>
    run({ type: 'pick_template', templateKey: 'restaurant_booking' }, send('Corner Table Bistro'), send('(617) 555-0100'))

  it('says which profile fields it already has, then asks the first missing detail', () => {
    const s = restaurantAtQuestions()
    const texts = s.messages.map((m) => m.text)
    expect(texts).toContain("I already have your name and phone number from your profile, so I won't ask for those.")
    expect(s.awaiting).toBe('template_field')
    expect(s.fieldKey).toBe('party_size')
    expect(lastAgent(s)).toBe('How many people?')
  })

  it('asks one question at a time, in template order, then finishes', () => {
    let s = restaurantAtQuestions()
    s = reduce(s, send('6'))
    expect(s.fieldKey).toBe('day')
    s = reduce(s, send('Friday'))
    expect(s.fieldKey).toBe('time')
    s = reduce(s, send('7pm'))
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)).toEqual({
      template_key: 'restaurant_booking',
      goal: 'Restaurant: book a table',
      business_name: 'Corner Table Bistro',
      phone_e164: '+16175550100',
      answers: { party_size: '6', day: 'Friday', time: '7pm' },
      profile_additions: [],
    })
    expect(lastAgent(s)).toContain('(617) 555-0100')
  })

  it('never asks for a field the profile already has (doctor: name and DOB)', () => {
    let s = run({ type: 'pick_template', templateKey: 'doctor_booking' }, send('Back Bay Family Clinic'), send('617 555 0111'))
    const asked: string[] = []
    while (s.awaiting === 'template_field' && s.fieldKey) {
      asked.push(s.fieldKey)
      s = reduce(s, send(`answer for ${s.fieldKey}`))
    }
    expect(asked).toEqual(['provider_name', 'patient_status', 'reason'])
    expect(s.messages.map((m) => m.text)).toContain(
      "I already have your name and date of birth from your profile, so I won't ask for those.",
    )
  })

  it('asks for a profile field the profile lacks, and saves it as a profile field with default sharing', () => {
    const noDob: ChatContext = { ...ctx, profile: fakeProfile.filter((f) => f.key !== 'date_of_birth') }
    const r = chatReducer(noDob)
    let s = [
      { type: 'pick_template', templateKey: 'doctor_booking' } as const,
      send('Back Bay Family Clinic'),
      send('617 555 0111'),
    ].reduce(r, initialChatState(noDob))
    expect(s.fieldKey).toBe('date_of_birth')
    expect(s.messages.map((m) => m.text)).toContain(
      "I already have your name from your profile, so I won't ask for those.",
    )
    s = r(s, send('2004-03-15'))
    // The DOB goes to the profile (Ask me first by default), never into the call answers.
    expect(s.draft.profile_additions).toEqual([{ key: 'date_of_birth', value: '2004-03-15', sharing: 'ask_first' }])
    expect(s.draft.answers).toEqual({})
    expect(s.fieldKey).toBe('provider_name')
  })

  it('says nothing about the profile when it has none of the fields', () => {
    const empty: ChatContext = { ...ctx, profile: [] }
    const r = chatReducer(empty)
    const s = [
      { type: 'pick_template', templateKey: 'restaurant_booking' } as const,
      send('Corner Table Bistro'),
      send('617 555 0100'),
    ].reduce(r, initialChatState(empty))
    expect(s.messages.some((m) => m.text.startsWith('I already have'))).toBe(false)
    expect(s.fieldKey).toBe('calling_name')
  })

  it('rejects an answer that is too long and asks the same question again', () => {
    const s = reduce(restaurantAtQuestions(), send('x'.repeat(ANSWER_MAX + 1)))
    expect(s.fieldKey).toBe('party_size')
    expect(s.draft.answers).toEqual({})
  })

  it('asks no template questions for a custom call', () => {
    const s = run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'), send('617 555 0122'))
    expect(s.stage).toBe('draft_ready')
    expect(s.messages.some((m) => m.text.startsWith('I already have'))).toBe(false)
  })
})

describe('custom call flow (PC-3)', () => {
  it('asks for the goal, then the business, then the number', () => {
    let s = run({ type: 'pick_custom' })
    expect(s.awaiting).toBe('goal')
    expect(s.draft).toEqual({ template_key: null, answers: {}, profile_additions: [] })

    s = reduce(s, send('  Ask if they can hold a cake for pickup tomorrow  '))
    expect(s.draft.goal).toBe('Ask if they can hold a cake for pickup tomorrow')
    expect(s.awaiting).toBe('business_name')

    s = reduce(s, send('Sunrise Bakery'))
    s = reduce(s, send('+1 617 555 0122'))
    expect(completedDraft(s)).toEqual({
      template_key: null,
      goal: 'Ask if they can hold a cake for pickup tomorrow',
      business_name: 'Sunrise Bakery',
      phone_e164: '+16175550122',
      answers: {},
      profile_additions: [],
    })
  })

  it('treats typing at the home screen as a custom call goal', () => {
    const s = run(send('Ask if they have a table for 6 tonight'))
    expect(s.draft).toEqual({
      template_key: null,
      goal: 'Ask if they have a table for 6 tonight',
      answers: {},
      profile_additions: [],
    })
    expect(s.awaiting).toBe('business_name')
  })

  it('rejects a goal that is too long and keeps asking', () => {
    const s = run({ type: 'pick_custom' }, send('x'.repeat(GOAL_MAX + 1)))
    expect(s.awaiting).toBe('goal')
    expect(s.draft.goal).toBeUndefined()
  })

  it('rejects a business name that is too long and keeps asking', () => {
    const s = run({ type: 'pick_custom' }, send('Hold a cake'), send('B'.repeat(BUSINESS_NAME_MAX + 1)))
    expect(s.awaiting).toBe('business_name')
    expect(s.draft.business_name).toBeUndefined()
  })
})

describe('phone number in the chat (PC-7)', () => {
  const atPhone = () => run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'))

  it.each([
    ['911', 'emergency'],
    ['416 555 0123', 'US phone numbers'],
    ['12345', 'valid US phone number'],
  ])('rejects %j, explains why, and asks again', (raw, wording) => {
    const s = reduce(atPhone(), send(raw))
    expect(s.awaiting).toBe('phone')
    expect(s.stage).toBe('collecting')
    expect(s.draft.phone_e164).toBeUndefined()
    expect(lastAgent(s)).toContain(wording)
    // What the user typed still shows in the chat.
    expect(s.messages[s.messages.length - 2]).toMatchObject({ from: 'user', text: raw })
  })

  it('accepts a valid number after a rejected one', () => {
    let s = reduce(atPhone(), send('911'))
    s = reduce(s, send('617-555-0122'))
    expect(s.stage).toBe('draft_ready')
    expect(s.draft.phone_e164).toBe('+16175550122')
  })
})

describe('starting a new call any time (PC-1)', () => {
  it('drops an unfinished draft and goes back to the home chips', () => {
    const s = run({ type: 'pick_custom' }, send('Hold a cake'), { type: 'new_call' })
    expect(s.stage).toBe('home')
    expect(s.draft).toEqual({})
    expect(s.awaiting).toBeNull()
    expect(s.chips.map((c) => c.label)).toContain('Custom call')
  })

  it('offers a new call once a draft is ready, and keeps the chat history', () => {
    const ready = run(
      { type: 'pick_template', templateKey: 'doctor_booking' },
      send('Back Bay Family Clinic'),
      send('617 555 0111'),
      send('Dr. Lee'),
      send('Existing'),
      send('Check-up'),
    )
    expect(ready.chips.map((c) => c.label)).toEqual(['Start a new call'])
    const again = reduce(ready, { type: 'new_call' })
    expect(again.messages.length).toBeGreaterThan(ready.messages.length)
    expect(again.stage).toBe('home')
  })
})

describe('ignored input', () => {
  it('ignores empty or whitespace messages', () => {
    const start = initialChatState(ctx)
    expect(reduce(start, send('   '))).toBe(start)
  })

  it('ignores template and custom picks while a draft is in progress', () => {
    const mid = run({ type: 'pick_custom' })
    expect(reduce(mid, { type: 'pick_template', templateKey: 'restaurant_booking' })).toBe(mid)
    expect(reduce(mid, { type: 'pick_custom' })).toBe(mid)
  })

  it('points to "Start a new call" when the user types after the draft is ready', () => {
    const ready = run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'), send('617 555 0122'))
    const s = reduce(ready, send('hello?'))
    expect(lastAgent(s)).toContain('Start a new call')
    expect(completedDraft(s)).toEqual(completedDraft(ready))
  })

  it('has no completed draft before the questions are answered', () => {
    expect(completedDraft(run({ type: 'pick_custom' }, send('Hold a cake')))).toBeNull()
  })
})

describe('callingNameOf', () => {
  it('uses the calling name, then the full name, then a neutral fallback', () => {
    expect(callingNameOf(fakeProfile)).toBe('Alex')
    expect(callingNameOf([{ key: 'full_name', value: 'Alex Chen', sharing: 'share_freely' }])).toBe('Alex Chen')
    expect(callingNameOf([])).toBe('there')
  })
})
