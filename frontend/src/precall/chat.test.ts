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
  splitListItems,
  type ChatAction,
  type ChatContext,
  type ChatState,
  type FormSubmission,
} from './chat'
import { SKIP_LABEL, type Clarification } from './clarify'
import { fakeProfile, fakeTemplates } from './fakeBriefs'

const ctx: ChatContext = { profile: fakeProfile, templates: fakeTemplates }
const reduce = chatReducer(ctx)

function run(...actions: ChatAction[]): ChatState {
  return actions.reduce(reduce, initialChatState(ctx))
}

const send = (text: string): ChatAction => ({ type: 'send', text })
const done = (label: string): ChatAction => ({ type: 'list_done', label })

/** Skip the three asked PC-5 lists (never-agree is pre-filled, not asked). The fallback list needs two taps: the chat nudges once. */
const SKIP_LISTS: ChatAction[] = [
  done('Nothing specific'),
  done('Nothing'),
  done('Nothing, ask me'),
  done('Nothing, ask me'),
]

/** A draft's empty PC-5 lists and bookkeeping, before any list is finished. */
const FRESH = { answers: {}, profile_additions: [], constraints: [], preferences: [], fallbacks: [], never_agree: [{ text: 'A deposit or fee' }], done_lists: ['never_agree'] }
/** The same, after the three asked lists were skipped. */
const SKIPPED = { ...FRESH, done_lists: ['never_agree', 'constraints', 'preferences', 'fallbacks'] }
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

const RESTAURANT_FORM: FormSubmission = {
  business_name: 'Corner Table Bistro',
  phone: '(617) 555-0100',
  answers: { party_size: '6', day: 'Friday', time: '7 PM' },
  profile_values: {},
  constraints: [],
  preferences: [],
  fallbacks: [],
  never_agree: ['A deposit or fee'],
}
const DOCTOR_FORM: FormSubmission = {
  business_name: 'Back Bay Family Clinic',
  phone: '617 555 0111',
  answers: { provider_name: 'Dr. Lee', patient_status: 'Existing', reason: 'Check-up' },
  profile_values: {},
  constraints: [],
  preferences: [],
  fallbacks: [],
  never_agree: ['A deposit or fee'],
}
const pickRestaurant: ChatAction = { type: 'pick_template', templateKey: 'restaurant_booking' }
const submit = (form: FormSubmission): ChatAction => ({ type: 'submit_form', form })

describe('template form (PC-2, PC-4)', () => {
  it('opens the form for a template: the template is the goal and nothing is typed in the chat', () => {
    const s = run(pickRestaurant)
    expect(s.stage).toBe('form')
    expect(s.awaiting).toBeNull()
    expect(s.chips).toEqual([])
    expect(s.formError).toBeNull()
    expect(s.draft).toEqual({
      template_key: 'restaurant_booking',
      goal: 'Restaurant: book a table',
      ...FRESH,
      done_lists: [],
    })
    expect(completedDraft(s)).toBeNull()
  })

  it('ignores an unknown template key', () => {
    const start = initialChatState(ctx)
    expect(reduce(start, { type: 'pick_template', templateKey: 'nope' })).toBe(start)
  })

  it('goes to the review card once the form is valid, with the number checked and normalised', () => {
    const s = run(pickRestaurant, submit(RESTAURANT_FORM))
    expect(s.stage).toBe('draft_ready')
    expect(s.formError).toBeNull()
    expect(completedDraft(s)).toEqual({
      template_key: 'restaurant_booking',
      goal: 'Restaurant: book a table',
      business_name: 'Corner Table Bistro',
      phone_e164: '+16175550100',
      ...SKIPPED,
      done_lists: ['constraints', 'preferences', 'fallbacks', 'never_agree'],
      answers: { party_size: '6', day: 'Friday', time: '7 PM' },
    })
    expect(s.messages.map((m) => m.text).slice(-2)).toEqual([
      'Review plan',
      "Here's the plan. Give it a quick look, then start the call.",
    ])
    expect(s.reviewAfterId).toBe(s.messages[s.messages.length - 1].id)
  })

  it('keeps the three lists, tidied: trimmed, no blanks, no repeats in any case, at most LIST_MAX_ITEMS', () => {
    const s = run(
      pickRestaurant,
      submit({
        ...RESTAURANT_FORM,
        constraints: ['  Vegetarian options ', '', 'vegetarian OPTIONS'],
        preferences: ['Window seat'],
        fallbacks: Array.from({ length: LIST_MAX_ITEMS + 2 }, (_, i) => `Backup ${i + 1}`),
      }),
    )
    const d = completedDraft(s)
    expect(d?.constraints).toEqual([{ text: 'Vegetarian options' }])
    expect(d?.preferences).toEqual([{ text: 'Window seat' }])
    expect(d?.fallbacks).toHaveLength(LIST_MAX_ITEMS)
    expect(d?.fallbacks[0]).toEqual({ text: 'Backup 1' })
  })

  it('starts the must-haves with the template defaults (PC-2)', () => {
    const withDefaults: ChatContext = {
      ...ctx,
      templates: fakeTemplates.map((t) =>
        t.key === 'restaurant_booking' ? { ...t, default_constraints: [{ text: 'Same day only' }] } : t,
      ),
    }
    const s = chatReducer(withDefaults)(initialChatState(withDefaults), pickRestaurant)
    expect(s.draft.constraints).toEqual([{ text: 'Same day only' }])
  })

  it.each([
    ['no business name', { business_name: '   ' }, 'Enter the name of the place to call.'],
    ['a business name that is too long', { business_name: 'B'.repeat(BUSINESS_NAME_MAX + 1) }, 'That name is too long'],
    ['an emergency number', { phone: '911' }, "can't call emergency numbers"],
    ['no number', { phone: '' }, 'Enter the phone number to call.'],
    ['a missing detail', { answers: { party_size: '6', day: 'Friday', time: '' } }, 'Still needed: time.'],
    ['two missing details', { answers: { party_size: '6' } }, 'Still needed: day, time.'],
    ['an answer that is too long', { answers: { ...RESTAURANT_FORM.answers, day: 'x'.repeat(ANSWER_MAX + 1) } }, 'under 200 characters'],
    ['a list item that is too long', { preferences: ['x'.repeat(LIST_ITEM_MAX + 1)] }, 'Keep each item under'],
  ])('stays on the form with an error for %s', (_label, change, message) => {
    const before = run(pickRestaurant)
    const s = reduce(before, submit({ ...RESTAURANT_FORM, ...change }))
    expect(s.stage).toBe('form')
    expect(s.formError).toContain(message)
    expect(s.messages).toEqual(before.messages)
    expect(completedDraft(s)).toBeNull()
  })

  it('clears the error once the form is valid', () => {
    const bad = reduce(run(pickRestaurant), submit({ ...RESTAURANT_FORM, phone: '911' }))
    const s = reduce(bad, submit(RESTAURANT_FORM))
    expect(s.formError).toBeNull()
    expect(s.stage).toBe('draft_ready')
  })

  it('never asks for a profile field the profile already has (doctor: name and DOB)', () => {
    const s = run({ type: 'pick_template', templateKey: 'doctor_booking' }, submit(DOCTOR_FORM))
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)?.profile_additions).toEqual([])
    expect(completedDraft(s)?.answers).toEqual(DOCTOR_FORM.answers)
  })

  it('needs a profile field the profile lacks, and saves it as a profile field with default sharing', () => {
    const noDob: ChatContext = { ...ctx, profile: fakeProfile.filter((f) => f.key !== 'date_of_birth') }
    const r = chatReducer(noDob)
    const opened = r(initialChatState(noDob), { type: 'pick_template', templateKey: 'doctor_booking' })
    const blocked = r(opened, submit(DOCTOR_FORM))
    expect(blocked.formError).toBe('Still needed: date of birth.')
    const s = r(blocked, submit({ ...DOCTOR_FORM, profile_values: { date_of_birth: ' 2004-03-15 ' } }))
    // The DOB goes to the profile (Ask me first by default), never into the call answers.
    expect(completedDraft(s)?.profile_additions).toEqual([{ key: 'date_of_birth', value: '2004-03-15', sharing: 'ask_first' }])
    expect(completedDraft(s)?.answers).toEqual(DOCTOR_FORM.answers)
  })

  it('ignores a submit outside the form', () => {
    const start = initialChatState(ctx)
    expect(reduce(start, submit(RESTAURANT_FORM))).toBe(start)
    const custom = run({ type: 'pick_custom' })
    expect(reduce(custom, submit(RESTAURANT_FORM))).toBe(custom)
  })

  it('points back at the form when the user types while it is open', () => {
    const s = reduce(run(pickRestaurant), send('hello?'))
    expect(s.stage).toBe('form')
    expect(lastAgent(s)).toBe('Fill in the form above, then tap Review plan.')
  })

  it('asks no template questions for a custom call', () => {
    const s = run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'), send('617 555 0122'))
    expect(s.listKey).toBe('constraints')
  })
})

describe('constraints and fallbacks (PC-5)', () => {
  /** A custom call that has just reached the first PC-5 question. */
  const atLists = () => run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'), send('617 555 0122'))

  it('asks must-haves, nice-to-haves and what the user is OK with, in that order', () => {
    let s = atLists()
    const asked: string[] = []
    for (let i = 0; i < 10 && s.awaiting === 'list_item' && s.listKey; i++) {
      if (asked[asked.length - 1] !== s.listKey) asked.push(s.listKey)
      s = reduce(s, s.chips[s.chips.length - 1].action)
    }
    expect(asked).toEqual(['constraints', 'preferences', 'fallbacks'])
    expect(s.stage).toBe('draft_ready')
  })

  it('collects several items per list, in order, with a follow-up each time', () => {
    let s = reduce(atLists(), send('Pickup before 6pm'))
    expect(lastAgent(s)).toBe('Got it. Anything else that must be true?')
    expect(s.chips.map((c) => c.label)).toEqual(["That's all"])
    s = reduce(s, send('Not on Sunday'))
    expect(s.draft.constraints).toEqual([{ text: 'Pickup before 6pm' }, { text: 'Not on Sunday' }])
    s = reduce(s, done("That's all"))
    expect(s.listKey).toBe('preferences')
    expect(s.chips.map((c) => c.label)).toEqual(['Nothing'])
  })

  it('keeps what the user is OK with in the order given', () => {
    let s = [done('Nothing specific'), done('Nothing')].reduce(reduce, atLists())
    expect(s.listKey).toBe('fallbacks')
    s = reduce(s, send('Any cake they have ready'))
    expect(lastAgent(s)).toBe('Saved. Anything else you would be OK with?')
    s = reduce(s, send('Cupcakes'))
        expect(s.draft.fallbacks).toEqual([{ text: 'Any cake they have ready' }, { text: 'Cupcakes' }])
  })

  it('nudges once when the user skips backups, then accepts a second "no"', () => {
    let s = [done('Nothing specific'), done('Nothing')].reduce(reduce, atLists())
    s = reduce(s, done('Nothing, ask me'))
    expect(s.listKey).toBe('fallbacks')
    expect(s.fallbackNudged).toBe(true)
    expect(lastAgent(s)).toContain("I'll have to ping you mid-call")
    s = reduce(s, done('Nothing, ask me'))
    expect(s.stage).toBe('draft_ready')
    expect(s.draft.fallbacks).toEqual([])
  })

  it('accepts a backup typed after the nudge', () => {
    let s = [done('Nothing specific'), done('Nothing'), done('Nothing, ask me')].reduce(reduce, atLists())
    s = reduce(s, send('Tomorrow morning instead'))
    expect(s.draft.fallbacks).toEqual([{ text: 'Tomorrow morning instead' }])
    expect(s.listKey).toBe('fallbacks')
  })

  it('does not nudge when at least one backup was given', () => {
    let s = [done('Nothing specific'), done('Nothing'), send('Cupcakes')].reduce(reduce, atLists())
    s = reduce(s, done("That's all"))
    expect(s.stage).toBe('draft_ready')
    expect(s.fallbackNudged).toBe(false)
  })

  it('pre-fills "A deposit or fee" as never-agree and does not ask about it', () => {
    const s = [done('Nothing specific'), done('Nothing'), send('Cupcakes'), done("That's all")].reduce(reduce, atLists())
    expect(s.stage).toBe('draft_ready')
    expect(s.messages.some((m) => m.text.includes('never agree'))).toBe(false)
    expect(completedDraft(s)).toMatchObject({
      constraints: [],
      preferences: [],
      fallbacks: [{ text: 'Cupcakes' }],
      never_agree: [{ text: 'A deposit or fee' }],
    })
  })

  it('splits a long message into one item per question or sentence', () => {
    let s = [done('Nothing specific'), done('Nothing')].reduce(reduce, atLists())
    s = reduce(
      s,
      send(
        'is there another branch for pick up if this store is not available? is there deliveyr options? tea flavoured cake if possible',
      ),
    )
    expect(s.draft.fallbacks).toEqual([
      { text: 'is there another branch for pick up if this store is not available?' },
      { text: 'is there deliveyr options?' },
      { text: 'tea flavoured cake if possible' },
    ])
    expect(lastAgent(s)).toBe('I saved that as 3 items. Saved. Anything else you would be OK with?')
  })

  it('keeps a message with commas as one item', () => {
    const s = reduce(atLists(), send('include happy birthday cake topper, candles, knife'))
    expect(s.draft.constraints).toEqual([{ text: 'include happy birthday cake topper, candles, knife' }])
    expect(lastAgent(s)).toBe('Got it. Anything else that must be true?')
  })

  it('skips repeats inside a split message and keeps at most the limit', () => {
    let s = reduce(atLists(), send('Before 6pm'))
    s = reduce(s, send('before 6PM; a; b; c; d; e; f'))
    expect(s.draft.constraints?.map((i) => i.text)).toEqual(['Before 6pm', 'a', 'b', 'c', 'd'])
    expect(s.messages.map((m) => m.text)).toContain(
      `That's ${LIST_MAX_ITEMS}, the most I can keep track of here. I left out the last 2.`,
    )
    expect(s.listKey).toBe('preferences')
  })

  it('asks again when every part of a split message was already added', () => {
    let s = reduce(atLists(), send('Before 6pm; Not on Sunday'))
    s = reduce(s, send('not on sunday; before 6pm'))
    expect(s.draft.constraints).toHaveLength(2)
    expect(lastAgent(s)).toBe('You already added that. Anything else?')
  })

  it('rejects a message when any part is too long', () => {
    const s = reduce(atLists(), send(`Before 6pm; ${'x'.repeat(LIST_ITEM_MAX + 1)}`))
    expect(s.draft.constraints).toEqual([])
    expect(lastAgent(s)).toBe(`Could you keep that under ${LIST_ITEM_MAX} characters?`)
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

  it('ignores "list_done" when no list is being asked', () => {
    const start = initialChatState(ctx)
    expect(reduce(start, done('Nothing specific'))).toBe(start)
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

describe('restarting any time (PC-1)', () => {
  it('drops an unfinished draft and goes back to the home chips', () => {
    const s = run({ type: 'pick_custom' }, send('Hold a cake'), { type: 'restart' })
    expect(s.stage).toBe('home')
    expect(s.draft).toEqual({})
    expect(s.awaiting).toBeNull()
    expect(s.chips.map((c) => c.label)).toContain('Custom call')
  })

  it('clears the chat back to the greeting once a draft is ready, so the old form does not pile up', () => {
    const ready = run({ type: 'pick_template', templateKey: 'doctor_booking' }, submit(DOCTOR_FORM))
    expect(ready.chips).toEqual([])
    const again = reduce(ready, { type: 'restart' })
    expect(again).toEqual(initialChatState(ctx))
    expect(again.messages.length).toBeLessThan(ready.messages.length)
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

  it('points to the "Restart" button when the user types after the draft is ready', () => {
    const ready = run(
      { type: 'pick_custom' },
      send('Hold a cake'),
      send('Sunrise Bakery'),
      send('617 555 0122'),
      ...SKIP_LISTS,
    )
    expect(ready.stage).toBe('draft_ready')
    const s = reduce(ready, send('hello?'))
    expect(lastAgent(s)).toContain('Restart')
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


describe('splitListItems', () => {
  it('splits after a question mark, an exclamation mark, a semicolon and a sentence end', () => {
    expect(splitListItems('One? Two! Three; Four. Five')).toEqual(['One?', 'Two!', 'Three', 'Four.', 'Five'])
  })

  it('splits at line breaks and drops bullets and numbers', () => {
    expect(splitListItems('- tea cake\n• another branch\r\n1) delivery\n2. no nuts')).toEqual([
      'tea cake',
      'another branch',
      'delivery',
      'no nuts',
    ])
  })

  it('does not split at commas, "and", abbreviations, times or dates', () => {
    for (const text of [
      'topper, candles and a knife',
      'see Dr. Rivera on Tuesday',
      'pickup at 6 p.m. Friday works',
      'on Dec. 23 at 7:30 pm',
      'price under $3.50 is fine',
      'is it open 24/7?',
    ]) {
      expect(splitListItems(text)).toEqual([text])
    }
  })

  it('keeps "??" together and ignores empty pieces', () => {
    expect(splitListItems('really?? ok')).toEqual(['really??', 'ok'])
    expect(splitListItems(' ; ;; \n ')).toEqual([])
    expect(splitListItems('')).toEqual([])
  })
})


describe("the server's questions before Start", () => {
  const AT_7PM: Clarification = {
    item: 'at 7pm',
    list: 'constraints',
    question: 'You wrote "at 7pm". Is 7pm the latest you would accept, or the only time that works?',
    answers: [
      { label: '7pm or earlier', rules: [{ kind: 'time', latest: '19:00' }], text: null },
      { label: 'Only 7pm', rules: [{ kind: 'time', earliest: '19:00', latest: '19:00' }], text: null },
    ],
  }
  const BEFORE_3: Clarification = {
    item: 'Cupcakes before 3',
    list: 'fallbacks',
    question: 'You wrote "Cupcakes before 3". Did you mean before 3 in the morning or in the evening?',
    answers: [
      { label: 'Morning (am)', rules: null, text: 'Cupcakes before 3am' },
      { label: 'Afternoon or evening (pm)', rules: null, text: 'Cupcakes before 3pm' },
    ],
  }

  /** A custom call at the review card, with "at 7pm" as a must-have and "Cupcakes before 3" as a backup. */
  const atReview = () =>
    [
      send('at 7pm'),
      done("That's all"),
      done('Nothing'),
      send('Cupcakes before 3'),
      done("That's all"),
    ].reduce(reduce, run({ type: 'pick_custom' }, send('Hold a cake'), send('Sunrise Bakery'), send('617 555 0122')))

  const answer = (choice: number | 'skip'): ChatAction => ({ type: 'clarify_answer', choice })

  it('asks the first question with one chip per answer and a not-sure chip', () => {
    const review = atReview()
    expect(review.stage).toBe('draft_ready')
    const s = reduce(review, { type: 'clarify', questions: [AT_7PM, BEFORE_3] })
    expect(lastAgent(s)).toBe(`Before I call, one thing I want to check. ${AT_7PM.question}`)
    expect(s.chips.map((c) => c.label)).toEqual(['7pm or earlier', 'Only 7pm', SKIP_LABEL])
    expect(s.clarify).toHaveLength(2)
  })

  it('an answer settles the item with its rules and asks the next question', () => {
    let s = reduce(atReview(), { type: 'clarify', questions: [AT_7PM, BEFORE_3] })
    s = reduce(s, answer(0))
    expect(s.draft.constraints).toEqual([{ text: 'at 7pm', rules: [{ kind: 'time', latest: '19:00' }] }])
    expect(s.messages.some((m) => m.from === 'user' && m.text === '7pm or earlier')).toBe(true)
    expect(lastAgent(s)).toBe(BEFORE_3.question) // no lead-in the second time
    expect(s.clarify).toEqual([BEFORE_3])
  })

  it('an am or pm answer rewrites the item, and the draft shows the new words', () => {
    let s = reduce(atReview(), { type: 'clarify', questions: [BEFORE_3] })
    s = reduce(s, answer(1))
    expect(s.draft.fallbacks).toEqual([{ text: 'Cupcakes before 3pm' }])
  })

  it('"not sure" marks the item decided, with no rules, and keeps its words', () => {
    let s = reduce(atReview(), { type: 'clarify', questions: [AT_7PM] })
    s = reduce(s, answer('skip'))
    expect(s.draft.constraints).toEqual([{ text: 'at 7pm', rules: [] }])
    expect(s.messages.some((m) => m.from === 'user' && m.text === SKIP_LABEL)).toBe(true)
  })

  it('after the last answer it says to tap Start and clears the chips and the queue', () => {
    let s = reduce(atReview(), { type: 'clarify', questions: [AT_7PM] })
    s = reduce(s, answer(1))
    expect(lastAgent(s)).toBe("Thanks, that's all I needed. Tap Start when you're ready.")
    expect(s.chips).toEqual([])
    expect(s.clarify).toEqual([])
    expect(s.stage).toBe('draft_ready')
  })

  it('Start does nothing while a question is waiting, and works again after', () => {
    const asking = reduce(atReview(), { type: 'clarify', questions: [AT_7PM] })
    expect(reduce(asking, { type: 'start' })).toBe(asking)
    const answered = reduce(asking, answer(0))
    expect(reduce(answered, { type: 'start' }).stage).toBe('started')
  })

  it('carries the chosen rules into the brief the server is sent', () => {
    let s = reduce(atReview(), { type: 'clarify', questions: [AT_7PM] })
    s = reduce(s, answer(1))
    const brief = completedDraft(s)
    expect(brief?.constraints).toEqual([
      { text: 'at 7pm', rules: [{ kind: 'time', earliest: '19:00', latest: '19:00' }] },
    ])
  })

  it('drops a question about an item the user already changed, and says so if none are left', () => {
    const gone: Clarification = { ...AT_7PM, item: 'something I deleted' }
    const s = reduce(atReview(), { type: 'clarify', questions: [gone] })
    expect(s.clarify).toEqual([])
    expect(lastAgent(s)).toContain("couldn't match a question to your plan")
    const some = reduce(atReview(), { type: 'clarify', questions: [gone, AT_7PM] })
    expect(some.clarify).toEqual([AT_7PM])
  })

  it('ignores questions and answers outside the review, and an answer with no question', () => {
    const home = initialChatState(ctx)
    expect(reduce(home, { type: 'clarify', questions: [AT_7PM] })).toBe(home)
    const review = atReview()
    expect(reduce(review, answer(0))).toBe(review)
  })

  it('editing the plan drops the questions, since the server will read it again', () => {
    const asking = reduce(atReview(), { type: 'clarify', questions: [AT_7PM, BEFORE_3] })
    const editing = reduce(asking, { type: 'edit', target: 'constraints' })
    expect(editing.clarify).toEqual([])
  })

  it('a new call starts with no questions', () => {
    const asking = reduce(atReview(), { type: 'clarify', questions: [AT_7PM] })
    expect(reduce(asking, { type: 'new_call' }).clarify).toEqual([])
  })
})
