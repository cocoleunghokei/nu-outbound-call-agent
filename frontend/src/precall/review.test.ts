import { describe, expect, it } from 'vitest'
import { FIRST_MESSAGE_TEMPLATE, buildBrief, openingLine } from './brief'
import {
  canEdit,
  chatReducer,
  completedDraft,
  initialChatState,
  reviewOf,
  type ChatAction,
  type ChatContext,
  type ChatState,
  type FormSubmission,
} from './chat'
import { contractExampleBrief } from './contractExample'
import { fakeProfile, fakeTemplates } from './fakeBriefs'
import type { Brief, ProfileField } from './types'

const ctx: ChatContext = { profile: fakeProfile, templates: fakeTemplates }
const reduce = chatReducer(ctx)
const send = (text: string): ChatAction => ({ type: 'send', text })
const done = (label: string): ChatAction => ({ type: 'list_done', label })
const lastAgent = (s: ChatState) => [...s.messages].reverse().find((m) => m.from === 'agent')?.text ?? ''

const RESTAURANT_FORM: FormSubmission = {
  business_name: 'Corner Table Bistro',
  phone: '617 555 0100',
  answers: { party_size: '6', day: 'Friday', time: '7pm' },
  profile_values: {},
  constraints: ['Between 6:30pm and 8pm'],
  preferences: [],
  fallbacks: ['Saturday same time'],
  never_agree: ['A deposit or fee'],
}

/** A restaurant call that reaches the review card. */
const RESTAURANT: ChatAction[] = [
  { type: 'pick_template', templateKey: 'restaurant_booking' },
  { type: 'submit_form', form: RESTAURANT_FORM },
]

/** A custom call that reaches the review card. */
const CUSTOM: ChatAction[] = [
  { type: 'pick_custom' },
  send('Ask if they can hold a cake for pickup tomorrow'),
  send('Sunrise Bakery'),
  send('617 555 0122'),
  done('Nothing specific'),
  done('Nothing'),
  send('Any cake they have ready'),
  done("That's all"),
]

const run = (actions: ChatAction[], context: ChatContext = ctx) =>
  actions.reduce(chatReducer(context), initialChatState(context))

describe('openingLine (DC-1 preview on the review card)', () => {
  it('uses the exact first-message template from PR #19', () => {
    expect(FIRST_MESSAGE_TEMPLATE).toBe(
      "Hi, I'm an AI assistant calling on behalf of {calling_name}. This call is transcribed. {task_line}",
    )
  })

  it('fills the calling name and the task line from literals', () => {
    const review = reviewOf(run(RESTAURANT), ctx)
    expect(review && openingLine(review.brief, fakeProfile)).toBe(
      "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. I'd like to book a table for 6 on Friday at 7pm.",
    )
  })

  it('fills a field_ref from the profile (the contract example)', () => {
    expect(openingLine(contractExampleBrief, fakeProfile)).toBe(
      "Hi, I'm an AI assistant calling on behalf of Alex. This call is transcribed. Book a table for 4 under Alex.",
    )
  })

  it('returns null for an Ask-me-first field_ref, a missing calling name, or an unfilled placeholder', () => {
    const askFirstRef: Brief = { ...contractExampleBrief, task_vars: { party_size: { literal: '4' }, name: { field_ref: 'date_of_birth' } } }
    expect(openingLine(askFirstRef, fakeProfile)).toBeNull()
    const noName: ProfileField[] = fakeProfile.filter((f) => f.key !== 'calling_name')
    expect(openingLine(contractExampleBrief, noName)).toBeNull()
    expect(openingLine({ ...contractExampleBrief, task_vars: {} }, fakeProfile)).toBeNull()
  })
})

describe('review card state (PC-6)', () => {
  it('shows the card under the "plan" message with a brief that has nothing missing', () => {
    const s = run(RESTAURANT)
    expect(s.stage).toBe('draft_ready')
    const anchor = s.messages.find((m) => m.id === s.reviewAfterId)
    expect(anchor?.text).toBe("Here's the plan. Give it a quick look, then start the call.")
    const review = reviewOf(s, ctx)
    expect(review?.missing_fields).toEqual([])
    expect(review?.brief.fallbacks).toEqual([{ text: 'Saturday same time' }])
  })

  it('has no review before the chat is finished', () => {
    expect(reviewOf(run(RESTAURANT.slice(0, 1)), ctx)).toBeNull()
  })

  it('matches buildBrief on the completed draft', () => {
    const s = run(CUSTOM)
    const draft = completedDraft(s)
    expect(draft && reviewOf(s, ctx)).toEqual(draft && buildBrief(draft, undefined, fakeProfile))
  })
})

describe('editing from the review card', () => {
  it('only offers Goal for custom calls and Details for template calls', () => {
    expect(canEdit(completedDraft(run(CUSTOM)) ?? {}, 'goal')).toBe(true)
    expect(canEdit(completedDraft(run(CUSTOM)) ?? {}, 'details')).toBe(false)
    expect(canEdit(completedDraft(run(RESTAURANT)) ?? {}, 'goal')).toBe(false)
    expect(canEdit(completedDraft(run(RESTAURANT)) ?? {}, 'details')).toBe(true)
  })

  it('custom goal: re-asks only the goal, keeps everything else, and returns to the card', () => {
    let s = reduce(run(CUSTOM), { type: 'edit', target: 'goal' })
    expect(s.stage).toBe('collecting')
    expect(s.awaiting).toBe('goal')
    expect(s.messages[s.messages.length - 2]).toMatchObject({ from: 'user', text: 'Change the goal' })
    s = reduce(s, send('Ask if they have cupcakes'))
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)).toMatchObject({
      goal: 'Ask if they have cupcakes',
      business_name: 'Sunrise Bakery',
      fallbacks: [{ text: 'Any cake they have ready' }],
    })
  })

  it('template details: Edit reopens the form with everything filled in, and the review comes back on submit', () => {
    let s = reduce(run(RESTAURANT), { type: 'edit', target: 'details' })
    expect(s.stage).toBe('form')
    expect(completedDraft(s)).toBeNull()
    expect(s.draft.answers).toEqual(RESTAURANT_FORM.answers)
    expect(s.draft.business_name).toBe('Corner Table Bistro')
    s = reduce(s, { type: 'submit_form', form: { ...RESTAURANT_FORM, answers: { party_size: '4', day: 'Saturday', time: '8pm' } } })
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)?.answers).toEqual({ party_size: '4', day: 'Saturday', time: '8pm' })
    expect(s.messages.filter((m) => m.text === "Here's the plan. Give it a quick look, then start the call.")).toHaveLength(2)
  })

  it.each(['calling', 'constraints', 'preferences', 'fallbacks', 'never_agree'] as const)(
    'template call: editing %s also reopens the form',
    (target) => {
      const s = reduce(run(RESTAURANT), { type: 'edit', target })
      expect(s.stage).toBe('form')
      expect(s.awaiting).toBeNull()
      expect(s.draft.fallbacks).toEqual([{ text: 'Saturday same time' }])
    },
  )

  it('who to call: re-asks the business and the number, with the number checked again', () => {
    let s = reduce(run(CUSTOM), { type: 'edit', target: 'calling' })
    expect(s.awaiting).toBe('business_name')
    s = reduce(s, send('Harbor Grill'))
    s = reduce(s, send('911'))
    expect(s.awaiting).toBe('phone')
    s = reduce(s, send('617 555 0144'))
    expect(completedDraft(s)).toMatchObject({ business_name: 'Harbor Grill', phone_e164: '+16175550144' })
  })

  it('custom call, a list: clears and re-asks only that list, then returns to the card', () => {
    let s = reduce(run(CUSTOM), { type: 'edit', target: 'fallbacks' })
    expect(s.listKey).toBe('fallbacks')
    expect(s.draft.fallbacks).toEqual([])
    s = [send('Sunday 7pm'), send('Friday 8:30pm'), done("That's all")].reduce(reduce, s)
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)).toMatchObject({
      constraints: [],
      fallbacks: [{ text: 'Sunday 7pm' }, { text: 'Friday 8:30pm' }],
      never_agree: [{ text: 'A deposit or fee' }],
    })
  })

  it('custom call: never-agree starts as "A deposit or fee" and can still be changed from the card', () => {
    const ready = run(CUSTOM)
    expect(completedDraft(ready)?.never_agree).toEqual([{ text: 'A deposit or fee' }])
    let s = reduce(ready, { type: 'edit', target: 'never_agree' })
    expect(s.listKey).toBe('never_agree')
    s = [send('Paying in advance'), done("That's all")].reduce(reduce, s)
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)?.never_agree).toEqual([{ text: 'Paying in advance' }])
  })

  it('ignores edits that do not apply, and edits outside the review', () => {
    const ready = run(RESTAURANT)
    expect(reduce(ready, { type: 'edit', target: 'goal' })).toBe(ready)
    const mid = run(RESTAURANT.slice(0, 1))
    expect(reduce(mid, { type: 'edit', target: 'constraints' })).toBe(mid)
  })
})

describe('Start (PC-6)', () => {
  it('starts the call, keeps the draft and the card, with no new-call chip', () => {
    const ready = run(CUSTOM)
    const s = reduce(ready, { type: 'start' })
    expect(s.stage).toBe('started')
    expect(lastAgent(s)).toBe("Calling Sunrise Bakery now. I'll ask you here if I need anything.")
    expect(completedDraft(s)).toEqual(completedDraft(ready))
    expect(s.reviewAfterId).toBe(ready.reviewAfterId)
    expect(s.chips).toEqual([])
  })

  it('ignores Start twice, and edits after Start', () => {
    const started = reduce(run(CUSTOM), { type: 'start' })
    expect(reduce(started, { type: 'start' })).toBe(started)
    expect(reduce(started, { type: 'edit', target: 'goal' })).toBe(started)
  })

  it('stays blocked while something is missing (no calling name in the profile)', () => {
    const noName: ChatContext = { ...ctx, profile: fakeProfile.filter((f) => f.key !== 'calling_name') }
    const ready = run(CUSTOM, noName)
    expect(ready.stage).toBe('draft_ready')
    expect(reviewOf(ready, noName)?.missing_fields).toContain('calling_name')
    expect(chatReducer(noName)(ready, { type: 'start' })).toBe(ready)
  })

  it('ignores Start before the review', () => {
    const mid = run(CUSTOM.slice(0, 2))
    expect(reduce(mid, { type: 'start' })).toBe(mid)
  })

  it('can restart afterwards, and the chat is cleared', () => {
    const s = reduce(reduce(run(CUSTOM), { type: 'start' }), { type: 'restart' })
    expect(s.stage).toBe('home')
    expect(s.reviewAfterId).toBeNull()
  })
})
