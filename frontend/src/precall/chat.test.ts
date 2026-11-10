import { describe, expect, it } from 'vitest'
import {
  ANSWER_MAX,
  BUSINESS_NAME_MAX,
  GOAL_MAX,
  LIST_ITEM_MAX,
  LIST_MAX_ITEMS,
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
const done = (label: string): ChatAction => ({ type: 'list_done', label })

/** Skip all four PC-5 lists. The fallback list needs two taps: the chat nudges once. */
const SKIP_LISTS: ChatAction[] = [
  done('No limits'),
  done('No preferences'),
  done('No backup, ask me'),
  done('No backup, ask me'),
  done('Nothing else'),
]

/** A draft's empty PC-5 lists and bookkeeping, before any list is finished. */
const FRESH = { answers: {}, profile_additions: [], constraints: [], preferences: [], fallbacks: [], never_agree: [], done_lists: [] }
/** The same, after all four lists were skipped. */
const SKIPPED = { ...FRESH, done_lists: ['constraints', 'preferences', 'fallbacks', 'never_agree'] }
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
    const s = run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'), send('617 555 0122'), ...SKIP_LISTS)
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
      ...FRESH,
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
    // All details answered: on to the PC-5 lists.
    expect(s.awaiting).toBe('list_item')
    expect(s.listKey).toBe('constraints')
    s = SKIP_LISTS.reduce(reduce, s)
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)).toEqual({
      template_key: 'restaurant_booking',
      goal: 'Restaurant: book a table',
      business_name: 'Corner Table Bistro',
      phone_e164: '+16175550100',
      ...SKIPPED,
      answers: { party_size: '6', day: 'Friday', time: '7pm' },
    })
    expect(lastAgent(s)).toBe("Here's the plan. Give it a quick look, then start the call.")
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
    expect(s.listKey).toBe('constraints')
    expect(s.messages.some((m) => m.text.startsWith('I already have'))).toBe(false)
  })
})

describe('constraints and fallbacks (PC-5)', () => {
  /** A custom call that has just reached the first PC-5 question. */
  const atLists = () => run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'), send('617 555 0122'))

  it('asks limits, preferences, backups and never-agree items, in that order', () => {
    let s = atLists()
    const asked: string[] = []
    for (let i = 0; i < 10 && s.awaiting === 'list_item' && s.listKey; i++) {
      if (asked[asked.length - 1] !== s.listKey) asked.push(s.listKey)
      s = reduce(s, s.chips[s.chips.length - 1].action)
    }
    expect(asked).toEqual(['constraints', 'preferences', 'fallbacks', 'never_agree'])
    expect(s.stage).toBe('draft_ready')
  })

  it('collects several items per list, in order, with a follow-up each time', () => {
    let s = reduce(atLists(), send('Pickup before 6pm'))
    expect(lastAgent(s)).toBe('Got it. Any other limits?')
    expect(s.chips.map((c) => c.label)).toEqual(["That's all"])
    s = reduce(s, send('Not on Sunday'))
    expect(s.draft.constraints).toEqual([{ text: 'Pickup before 6pm' }, { text: 'Not on Sunday' }])
    s = reduce(s, done("That's all"))
    expect(s.listKey).toBe('preferences')
    expect(s.chips.map((c) => c.label)).toEqual(['No preferences'])
  })

  it('keeps backups ranked best first and numbers them', () => {
    let s = [done('No limits'), done('No preferences')].reduce(reduce, atLists())
    expect(s.listKey).toBe('fallbacks')
    s = reduce(s, send('Any cake they have ready'))
    expect(lastAgent(s)).toBe("Saved as backup #1. Another backup in case that's taken too?")
    s = reduce(s, send('Cupcakes'))
    expect(lastAgent(s)).toContain('backup #2')
    expect(s.draft.fallbacks).toEqual([{ text: 'Any cake they have ready' }, { text: 'Cupcakes' }])
  })

  it('nudges once when the user skips backups, then accepts a second "no"', () => {
    let s = [done('No limits'), done('No preferences')].reduce(reduce, atLists())
    s = reduce(s, done('No backup, ask me'))
    expect(s.listKey).toBe('fallbacks')
    expect(s.fallbackNudged).toBe(true)
    expect(lastAgent(s)).toContain("I'll have to ping you mid-call")
    s = reduce(s, done('No backup, ask me'))
    expect(s.listKey).toBe('never_agree')
    expect(s.draft.fallbacks).toEqual([])
  })

  it('accepts a backup typed after the nudge', () => {
    let s = [done('No limits'), done('No preferences'), done('No backup, ask me')].reduce(reduce, atLists())
    s = reduce(s, send('Tomorrow morning instead'))
    expect(s.draft.fallbacks).toEqual([{ text: 'Tomorrow morning instead' }])
    expect(s.listKey).toBe('fallbacks')
  })

  it('does not nudge when at least one backup was given', () => {
    let s = [done('No limits'), done('No preferences'), send('Cupcakes')].reduce(reduce, atLists())
    s = reduce(s, done("That's all"))
    expect(s.listKey).toBe('never_agree')
    expect(s.fallbackNudged).toBe(false)
  })

  it('offers "A deposit or fee" as a one-tap never-agree item, and hides it once added', () => {
    let s = [done('No limits'), done('No preferences'), send('Cupcakes'), done("That's all")].reduce(reduce, atLists())
    expect(s.listKey).toBe('never_agree')
    expect(s.chips.map((c) => c.label)).toEqual(['A deposit or fee', 'Nothing else'])
    s = reduce(s, s.chips[0].action)
    expect(s.draft.never_agree).toEqual([{ text: 'A deposit or fee' }])
    expect(s.chips.map((c) => c.label)).toEqual(["That's all"])
    s = reduce(s, done("That's all"))
    expect(completedDraft(s)).toMatchObject({
      constraints: [],
      preferences: [],
      fallbacks: [{ text: 'Cupcakes' }],
      never_agree: [{ text: 'A deposit or fee' }],
    })
  })

  it('ignores a duplicate item (any case) and asks again', () => {
    let s = reduce(atLists(), send('Before 6pm'))
    s = reduce(s, send('before 6PM'))
    expect(s.draft.constraints).toEqual([{ text: 'Before 6pm' }])
    expect(lastAgent(s)).toBe('You already added that. Anything else?')
  })

  it('rejects an item that is too long', () => {
    const s = reduce(atLists(), send('x'.repeat(LIST_ITEM_MAX + 1)))
    expect(s.draft.constraints).toEqual([])
    expect(s.listKey).toBe('constraints')
  })

  it(`stops a list at ${LIST_MAX_ITEMS} items and moves on`, () => {
    let s = atLists()
    for (let i = 1; i <= LIST_MAX_ITEMS; i++) s = reduce(s, send(`Limit ${i}`))
    expect(s.draft.constraints).toHaveLength(LIST_MAX_ITEMS)
    expect(s.listKey).toBe('preferences')
  })

  it('starts the limits with the template defaults and says so (PC-2)', () => {
    const withDefaults: ChatContext = {
      ...ctx,
      templates: fakeTemplates.map((t) =>
        t.key === 'restaurant_booking' ? { ...t, default_constraints: [{ text: 'Same day only' }] } : t,
      ),
    }
    const r = chatReducer(withDefaults)
    const s = [
      { type: 'pick_template', templateKey: 'restaurant_booking' } as const,
      send('Corner Table Bistro'),
      send('617 555 0100'),
      send('6'),
      send('Friday'),
      send('7pm'),
    ].reduce(r, initialChatState(withDefaults))
    expect(s.listKey).toBe('constraints')
    expect(s.draft.constraints).toEqual([{ text: 'Same day only' }])
    expect(lastAgent(s)).toBe("I've added the usual limits for this call: Same day only. Any other limits?")
    expect(s.chips.map((c) => c.label)).toEqual(["That's all"])
  })

  it('ignores "list_done" when no list is being asked', () => {
    const start = initialChatState(ctx)
    expect(reduce(start, done('No limits'))).toBe(start)
  })
})

describe('custom call flow (PC-3)', () => {
  it('asks for the goal, then the business, then the number', () => {
    let s = run({ type: 'pick_custom' })
    expect(s.awaiting).toBe('goal')
    expect(s.draft).toEqual({ template_key: null, ...FRESH })

    s = reduce(s, send('  Ask if they can hold a cake for pickup tomorrow  '))
    expect(s.draft.goal).toBe('Ask if they can hold a cake for pickup tomorrow')
    expect(s.awaiting).toBe('business_name')

    s = reduce(s, send('Sunrise Bakery'))
    s = reduce(s, send('+1 617 555 0122'))
    s = SKIP_LISTS.reduce(reduce, s)
    expect(completedDraft(s)).toEqual({
      template_key: null,
      goal: 'Ask if they can hold a cake for pickup tomorrow',
      business_name: 'Sunrise Bakery',
      phone_e164: '+16175550122',
      ...SKIPPED,
    })
  })

  it('treats typing at the home screen as a custom call goal', () => {
    const s = run(send('Ask if they have a table for 6 tonight'))
    expect(s.draft).toEqual({
      template_key: null,
      goal: 'Ask if they have a table for 6 tonight',
      ...FRESH,
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
    expect(s.listKey).toBe('constraints')
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
      ...SKIP_LISTS,
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
    const ready = run(
      { type: 'pick_custom' },
      send('Hold a cake'),
      send('Sunrise Bakery'),
      send('617 555 0122'),
      ...SKIP_LISTS,
    )
    expect(ready.stage).toBe('draft_ready')
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
