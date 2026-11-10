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
} from './chat'
import { contractExampleBrief } from './contractExample'
import { fakeProfile, fakeTemplates } from './fakeBriefs'
import type { Brief, ProfileField } from './types'

const ctx: ChatContext = { profile: fakeProfile, templates: fakeTemplates }
const reduce = chatReducer(ctx)
const send = (text: string): ChatAction => ({ type: 'send', text })
const done = (label: string): ChatAction => ({ type: 'list_done', label })
const lastAgent = (s: ChatState) => [...s.messages].reverse().find((m) => m.from === 'agent')?.text ?? ''

/** A restaurant call that reaches the review card. */
const RESTAURANT: ChatAction[] = [
  { type: 'pick_template', templateKey: 'restaurant_booking' },
  send('Corner Table Bistro'),
  send('617 555 0100'),
  send('6'),
  send('Friday'),
  send('7pm'),
  send('Between 6:30pm and 8pm'),
  done("That's all"),
  done('No preferences'),
  send('Saturday same time'),
  done("That's all"),
  send('A deposit or fee'),
  done("That's all"),
]

/** A custom call that reaches the review card. */
const CUSTOM: ChatAction[] = [
  { type: 'pick_custom' },
  send('Ask if they can hold a cake for pickup tomorrow'),
  send('Sunrise Bakery'),
  send('617 555 0122'),
  done('No limits'),
  done('No preferences'),
  send('Any cake they have ready'),
  done("That's all"),
  done('Nothing else'),
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
    expect(reviewOf(run(RESTAURANT.slice(0, 5)), ctx)).toBeNull()
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

  it('template details: re-asks the call details without repeating the profile line', () => {
    let s = reduce(run(RESTAURANT), { type: 'edit', target: 'details' })
    expect(s.fieldKey).toBe('party_size')
    const announced = s.messages.filter((m) => m.text.startsWith('I already have')).length
    s = [send('4'), send('Saturday'), send('8pm')].reduce(reduce, s)
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)?.answers).toEqual({ party_size: '4', day: 'Saturday', time: '8pm' })
    expect(s.messages.filter((m) => m.text.startsWith('I already have')).length).toBe(announced)
  })

  it('who to call: re-asks the business and the number, with the number checked again', () => {
    let s = reduce(run(RESTAURANT), { type: 'edit', target: 'calling' })
    expect(s.awaiting).toBe('business_name')
    s = reduce(s, send('Harbor Grill'))
    s = reduce(s, send('911'))
    expect(s.awaiting).toBe('phone')
    s = reduce(s, send('617 555 0144'))
    expect(completedDraft(s)).toMatchObject({ business_name: 'Harbor Grill', phone_e164: '+16175550144' })
  })

  it('a list: clears and re-asks only that list, then returns to the card', () => {
    let s = reduce(run(RESTAURANT), { type: 'edit', target: 'fallbacks' })
    expect(s.listKey).toBe('fallbacks')
    expect(s.draft.fallbacks).toEqual([])
    s = [send('Sunday 7pm'), send('Friday 8:30pm'), done("That's all")].reduce(reduce, s)
    expect(s.stage).toBe('draft_ready')
    expect(completedDraft(s)).toMatchObject({
      constraints: [{ text: 'Between 6:30pm and 8pm' }],
      fallbacks: [{ text: 'Sunday 7pm' }, { text: 'Friday 8:30pm' }],
      never_agree: [{ text: 'A deposit or fee' }],
    })
  })

  it('ignores edits that do not apply, and edits outside the review', () => {
    const ready = run(RESTAURANT)
    expect(reduce(ready, { type: 'edit', target: 'goal' })).toBe(ready)
    const mid = run(RESTAURANT.slice(0, 3))
    expect(reduce(mid, { type: 'edit', target: 'constraints' })).toBe(mid)
  })
})

describe('Start (PC-6)', () => {
  it('starts the call, keeps the draft and the card, and offers a new call', () => {
    const ready = run(CUSTOM)
    const s = reduce(ready, { type: 'start' })
    expect(s.stage).toBe('started')
    expect(lastAgent(s)).toBe("Calling Sunrise Bakery now. I'll ask you here if I need anything.")
    expect(completedDraft(s)).toEqual(completedDraft(ready))
    expect(s.reviewAfterId).toBe(ready.reviewAfterId)
    expect(s.chips.map((c) => c.label)).toEqual(['Start a new call'])
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

  it('can start a new call afterwards', () => {
    const s = reduce(reduce(run(CUSTOM), { type: 'start' }), { type: 'new_call' })
    expect(s.stage).toBe('home')
    expect(s.reviewAfterId).toBeNull()
  })
})
