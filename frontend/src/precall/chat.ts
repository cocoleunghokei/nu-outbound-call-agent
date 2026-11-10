/**
 * Chat home logic (PC-1, PC-3, PC-4, PC-5): pick a template or a custom call,
 * give the business name, number and goal, answer only the questions the
 * profile can't already answer, then set limits, preferences, ranked backups
 * and never-agree items. Pure functions only, so the whole flow is
 * unit-tested without React. `ChatHome.tsx` just renders this state.
 *
 * The flow stops at `stage: 'draft_ready'`. Step 6 shows the review card
 * from there.
 */
import {
  describeProfileKeys,
  knownProfileFields,
  missingTemplateFields,
  withProfileField,
  type CallAnswers,
} from './missing'
import { checkPhone, formatUsPhone } from './phone'
import type { BriefConstraint, CallTemplate, ProfileField, TemplateField } from './types'

export type Speaker = 'agent' | 'user'

export interface ChatMessage {
  id: number
  from: Speaker
  text: string
}

/**
 * What the chat is waiting for the user to type. `template_field` uses
 * `ChatState.fieldKey`; `list_item` uses `ChatState.listKey`.
 */
export type Awaiting = 'goal' | 'business_name' | 'phone' | 'template_field' | 'list_item' | null

/** The four PC-5 lists, in the order the chat asks for them. Same names as in brief.v1. */
export const LIST_ORDER = ['constraints', 'preferences', 'fallbacks', 'never_agree'] as const
export type ListKey = (typeof LIST_ORDER)[number]

/** One item in any of the PC-5 lists: the brief.v1 `{ text }` shape. */
export type ListItem = BriefConstraint

/** What the user has told us about the call so far. Becomes part of the brief later. */
export interface CallDraft {
  template_key: string | null
  goal: string
  business_name: string
  phone_e164: string
  /** Template details for this call only (source "call"), by field key. */
  answers: CallAnswers
  /**
   * Profile fields the user filled in during this chat because the profile
   * lacked them. Saved to the profile with ON-5 default sharing (never put in
   * the brief as values). Sent to Steve's profile API once it exists.
   */
  profile_additions: ProfileField[]
  /** Hard limits the agent must stick to (PC-5). */
  constraints: ListItem[]
  /** Nice to have; missing one makes a decision Medium, not Low (DC-4). */
  preferences: ListItem[]
  /** Acceptable alternatives, best first (PC-5). */
  fallbacks: ListItem[]
  /** Things the agent must never agree to without asking (PC-5, DC-13). */
  never_agree: ListItem[]
  /** Which PC-5 lists the user has finished. */
  done_lists: ListKey[]
}

export type ChatAction =
  | { type: 'pick_template'; templateKey: string }
  | { type: 'pick_custom' }
  | { type: 'send'; text: string }
  | { type: 'new_call' }
  /** Finish the current PC-5 list. `label` is what the user tapped, shown as their reply. */
  | { type: 'list_done'; label: string }

/** A one-tap reply shown above the composer. */
export interface Chip {
  label: string
  action: ChatAction
  quiet?: boolean
}

export type Stage = 'home' | 'collecting' | 'draft_ready'

export interface ChatState {
  messages: ChatMessage[]
  chips: Chip[]
  awaiting: Awaiting
  /** The template field being asked, when `awaiting` is `template_field`. */
  fieldKey: string | null
  /** The PC-5 list being filled, when `awaiting` is `list_item`. */
  listKey: ListKey | null
  /** Whether the chat has already nudged the user to add a backup (PC-5 asks for at least one). */
  fallbackNudged: boolean
  /** Composer hint for what to type next. */
  placeholder: string
  stage: Stage
  draft: Partial<CallDraft>
  /** Whether the chat has said which profile fields it won't ask for, for this draft. */
  knownAnnounced: boolean
  nextId: number
}

/** What the chat needs from outside: the user's profile and the templates. */
export interface ChatContext {
  profile: ProfileField[]
  templates: CallTemplate[]
}

export const BUSINESS_NAME_MAX = 100
export const GOAL_MAX = 300
export const ANSWER_MAX = 200
/** Most items per PC-5 list, to keep the review card scannable in ~10 s (PC-6). */
export const LIST_MAX_ITEMS = 5
export const LIST_ITEM_MAX = 200

const DEFAULT_PLACEHOLDER = 'Send a message'

const PROMPTS: Record<'goal' | 'business_name' | 'phone', { text: string; placeholder: string }> = {
  goal: {
    text: 'Sure. Tell me what you need in plain words, like "Ask if they can hold a cake for pickup tomorrow."',
    placeholder: 'What should I call about?',
  },
  business_name: {
    text: "What's the name of the place I'm calling?",
    placeholder: 'Business name',
  },
  phone: {
    text: "What's their phone number?",
    placeholder: 'Phone number, e.g. (617) 555-0123',
  },
}

interface ListPrompt {
  ask: string
  again: (count: number) => string
  /** Chip to finish the list before anything was added. */
  none: string
  placeholder: string
  /** One-tap suggestions that add an item. */
  suggestions?: string[]
}

const LIST_PROMPTS: Record<ListKey, ListPrompt> = {
  constraints: {
    ask: 'Any limits I must stick to? For example, "before 3pm" or "not on Monday".',
    again: () => 'Got it. Any other limits?',
    none: 'No limits',
    placeholder: 'A limit, e.g. before 3pm',
  },
  preferences: {
    ask: 'Anything you would like but can live without? For example, "a window seat".',
    again: () => 'Noted. Anything else you would like?',
    none: 'No preferences',
    placeholder: 'Something you would like',
  },
  fallbacks: {
    ask: "If they can't do that, what should I accept instead? I'll take a match without bothering you.",
    again: (n) => `Saved as backup #${n}. Another backup in case that's taken too?`,
    none: 'No backup, ask me',
    placeholder: 'A backup, e.g. Friday morning',
  },
  never_agree: {
    ask: "Anything I should never agree to? I'll always check with you before any deposit or fee.",
    again: () => 'Got it. Anything else I should never agree to?',
    none: 'Nothing else',
    placeholder: 'Something I should never agree to',
    suggestions: ['A deposit or fee'],
  },
}

const FALLBACK_NUDGE =
  "Without a backup, I'll have to ping you mid-call if they can't do your first choice. Want to add one?"

/** The name the agent uses for the user (ON-3); falls back to the full name. */
export function callingNameOf(profile: ProfileField[]): string {
  const pick = (key: string) => profile.find((f) => f.key === key)?.value.trim()
  return pick('calling_name') || pick('full_name') || 'there'
}

function homeChips(ctx: ChatContext): Chip[] {
  return [
    ...ctx.templates.map((t) => ({
      label: t.label,
      action: { type: 'pick_template', templateKey: t.key } as const,
    })),
    { label: 'Custom call', action: { type: 'pick_custom' } as const },
  ]
}

const NEW_CALL_CHIP: Chip = { label: 'Start a new call', action: { type: 'new_call' }, quiet: true }

function say(state: ChatState, from: Speaker, text: string): ChatState {
  return {
    ...state,
    messages: [...state.messages, { id: state.nextId, from, text }],
    nextId: state.nextId + 1,
  }
}

function waitFor(state: ChatState, field: 'goal' | 'business_name' | 'phone'): ChatState {
  const prompt = PROMPTS[field]
  return {
    ...say(state, 'agent', prompt.text),
    awaiting: field,
    fieldKey: null,
    listKey: null,
    placeholder: prompt.placeholder,
    chips: [],
  }
}

function waitForTemplateField(state: ChatState, field: TemplateField): ChatState {
  return {
    ...say(state, 'agent', field.question),
    awaiting: 'template_field',
    fieldKey: field.key,
    listKey: null,
    placeholder: 'Type your answer',
    chips: [],
  }
}

/** Chips while filling a list: suggestions not yet added, then the finish chip. */
function listChips(key: ListKey, items: ListItem[]): Chip[] {
  const prompt = LIST_PROMPTS[key]
  const added = new Set(items.map((i) => i.text.toLowerCase()))
  const suggestions = (prompt.suggestions ?? [])
    .filter((text) => !added.has(text.toLowerCase()))
    .map((text) => ({ label: text, action: { type: 'send', text } as const }))
  const finish = items.length === 0 ? prompt.none : "That's all"
  return [...suggestions, { label: finish, action: { type: 'list_done', label: finish }, quiet: true }]
}

/** Ask for a PC-5 list: `text` is the question or the "anything else?" follow-up. */
function waitForListItem(state: ChatState, key: ListKey, text: string): ChatState {
  const items = state.draft[key] ?? []
  return {
    ...say(state, 'agent', text),
    awaiting: 'list_item',
    fieldKey: null,
    listKey: key,
    placeholder: LIST_PROMPTS[key].placeholder,
    chips: listChips(key, items),
  }
}

function showHome(state: ChatState, ctx: ChatContext, text: string): ChatState {
  return {
    ...say(state, 'agent', text),
    chips: homeChips(ctx),
    awaiting: null,
    fieldKey: null,
    listKey: null,
    fallbackNudged: false,
    placeholder: DEFAULT_PLACEHOLDER,
    stage: 'home',
    draft: {},
    knownAnnounced: false,
  }
}

/** The first screen a returning (onboarded) user sees: the chat home (PC-1). */
export function initialChatState(ctx: ChatContext): ChatState {
  const empty: ChatState = {
    messages: [],
    chips: [],
    awaiting: null,
    fieldKey: null,
    listKey: null,
    fallbackNudged: false,
    placeholder: DEFAULT_PLACEHOLDER,
    stage: 'home',
    draft: {},
    knownAnnounced: false,
    nextId: 1,
  }
  return showHome(
    empty,
    ctx,
    `Hi ${callingNameOf(ctx.profile)}! Who should I call? Pick a template or start a custom call.`,
  )
}

function templateOf(ctx: ChatContext, key: string | null | undefined): CallTemplate | undefined {
  return key ? ctx.templates.find((t) => t.key === key) : undefined
}

/** The user's profile plus anything they filled in during this chat. */
function effectiveProfile(ctx: ChatContext, draft: Partial<CallDraft>): ProfileField[] {
  return (draft.profile_additions ?? []).reduce(
    (profile, f) => withProfileField(profile, f.key, f.value),
    ctx.profile,
  )
}

/** Ask for the next thing the draft is missing, or finish this part of the flow. */
function askNext(state: ChatState, ctx: ChatContext): ChatState {
  const d = state.draft
  if (!d.goal) return waitFor(state, 'goal')
  if (!d.business_name) return waitFor(state, 'business_name')
  if (!d.phone_e164) return waitFor(state, 'phone')

  // PC-4: ask only for template details the profile and earlier answers don't cover.
  const template = templateOf(ctx, d.template_key)
  if (template) {
    const profile = effectiveProfile(ctx, d)
    let next = state
    if (!state.knownAnnounced) {
      const known = knownProfileFields(template, ctx.profile)
      if (known.length > 0) {
        next = say(next, 'agent', `I already have ${describeProfileKeys(known)} from your profile, so I won't ask for those.`)
      }
      next = { ...next, knownAnnounced: true }
    }
    const missing = missingTemplateFields(template, profile, d.answers ?? {})
    if (missing.length > 0) return waitForTemplateField(next, missing[0])
    state = next
  }

  // PC-5: limits, preferences, ranked backups and never-agree items, in that order.
  const nextList = LIST_ORDER.find((key) => !(d.done_lists ?? []).includes(key))
  if (nextList) {
    const prefilled = d[nextList] ?? []
    // Template defaults (PC-2) are already in the list: say so instead of asking from scratch.
    const text =
      prefilled.length > 0
        ? `I've added the usual limits for this call: ${prefilled.map((i) => i.text).join('; ')}. Any other limits?`
        : LIST_PROMPTS[nextList].ask
    return waitForListItem(state, nextList, text)
  }

  const done = say(
    state,
    'agent',
    `Got it. I'll call ${d.business_name} at ${formatUsPhone(d.phone_e164)} to: ${d.goal}`,
  )
  return {
    ...done,
    stage: 'draft_ready',
    awaiting: null,
    fieldKey: null,
    listKey: null,
    placeholder: DEFAULT_PLACEHOLDER,
    chips: [NEW_CALL_CHIP],
  }
}

function startDraft(state: ChatState, ctx: ChatContext, userText: string, draft: Partial<CallDraft>): ChatState {
  return askNext(
    {
      ...say(state, 'user', userText),
      stage: 'collecting',
      draft: {
        answers: {},
        profile_additions: [],
        constraints: [],
        preferences: [],
        fallbacks: [],
        never_agree: [],
        done_lists: [],
        ...draft,
      },
      knownAnnounced: false,
      fallbackNudged: false,
      chips: [],
    },
    ctx,
  )
}

/** Store the answer to a template question: on the profile for profile fields, else for this call. */
function storeTemplateAnswer(ctx: ChatContext, draft: Partial<CallDraft>, key: string, value: string): Partial<CallDraft> {
  const field = templateOf(ctx, draft.template_key)?.required_fields.find((f) => f.key === key)
  if (field?.source === 'profile') {
    return { ...draft, profile_additions: withProfileField(draft.profile_additions ?? [], key, value) }
  }
  return { ...draft, answers: { ...(draft.answers ?? {}), [key]: value } }
}

/** Mark a PC-5 list as finished and move on. */
function finishList(state: ChatState, ctx: ChatContext, key: ListKey): ChatState {
  const done = [...(state.draft.done_lists ?? []), key]
  return askNext({ ...state, draft: { ...state.draft, done_lists: done } }, ctx)
}

/** Add one item to the current PC-5 list. */
function addListItem(state: ChatState, ctx: ChatContext, key: ListKey, text: string): ChatState {
  if (text.length > LIST_ITEM_MAX) {
    return say(state, 'agent', `Could you keep that under ${LIST_ITEM_MAX} characters?`)
  }
  const items = state.draft[key] ?? []
  if (items.some((i) => i.text.toLowerCase() === text.toLowerCase())) {
    return waitForListItem(state, key, 'You already added that. Anything else?')
  }
  const updated = [...items, { text }]
  const withItem = { ...state, draft: { ...state.draft, [key]: updated } }
  if (updated.length >= LIST_MAX_ITEMS) {
    return finishList(say(withItem, 'agent', `That's ${LIST_MAX_ITEMS}, the most I can keep track of here.`), ctx, key)
  }
  return waitForListItem(withItem, key, LIST_PROMPTS[key].again(updated.length))
}

/** Handle what the user typed for the field the chat is waiting on. */
function answer(state: ChatState, ctx: ChatContext, text: string): ChatState {
  const withUser = say(state, 'user', text)
  switch (state.awaiting) {
    case 'goal':
      if (text.length > GOAL_MAX) {
        return say(withUser, 'agent', `Could you say that in under ${GOAL_MAX} characters?`)
      }
      return askNext({ ...withUser, draft: { ...state.draft, goal: text } }, ctx)
    case 'business_name':
      if (text.length > BUSINESS_NAME_MAX) {
        return say(withUser, 'agent', `That name is too long. Keep it under ${BUSINESS_NAME_MAX} characters.`)
      }
      return askNext({ ...withUser, draft: { ...state.draft, business_name: text } }, ctx)
    case 'phone': {
      // PC-7: checked here, and again by the backend.
      const result = checkPhone(text)
      if (!result.ok) return say(withUser, 'agent', result.message)
      return askNext({ ...withUser, draft: { ...state.draft, phone_e164: result.e164 } }, ctx)
    }
    case 'template_field': {
      if (!state.fieldKey) return withUser
      if (text.length > ANSWER_MAX) {
        return say(withUser, 'agent', `Could you keep that under ${ANSWER_MAX} characters?`)
      }
      return askNext({ ...withUser, draft: storeTemplateAnswer(ctx, state.draft, state.fieldKey, text) }, ctx)
    }
    case 'list_item':
      if (!state.listKey) return withUser
      return addListItem(withUser, ctx, state.listKey, text)
    default:
      return withUser
  }
}

/** The single entry point: apply one user action to the chat state. */
export function chatReducer(ctx: ChatContext) {
  return (state: ChatState, action: ChatAction): ChatState => {
    switch (action.type) {
      case 'pick_template': {
        const template = templateOf(ctx, action.templateKey)
        if (!template || state.stage !== 'home') return state
        return startDraft(state, ctx, template.label, {
          template_key: template.key,
          goal: template.label,
          // PC-2: each template's default constraints start the list; the user can add more.
          constraints: template.default_constraints.map((c) => ({ text: c.text })).slice(0, LIST_MAX_ITEMS),
        })
      }
      case 'pick_custom':
        if (state.stage !== 'home') return state
        return startDraft(state, ctx, 'Custom call', { template_key: null })
      case 'list_done': {
        const key = state.listKey
        if (state.awaiting !== 'list_item' || !key) return state
        const withUser = say({ ...state, chips: [] }, 'user', action.label)
        // PC-5: prompt for at least one backup, once. A second "no" is accepted.
        if (key === 'fallbacks' && (state.draft.fallbacks ?? []).length === 0 && !state.fallbackNudged) {
          return { ...waitForListItem(withUser, key, FALLBACK_NUDGE), fallbackNudged: true }
        }
        return finishList(withUser, ctx, key)
      }
      case 'new_call':
        // PC-1: the user can start a new call at any time; an unfinished draft is dropped.
        return showHome({ ...say(state, 'user', 'Start a new call'), chips: [] }, ctx, 'Sure. Who should I call?')
      case 'send': {
        const text = action.text.trim()
        if (text === '') return state
        if (state.awaiting) return answer(state, ctx, text)
        if (state.stage === 'home') {
          // Typing at the home screen starts a custom call with that goal (PC-3).
          return startDraft(state, ctx, text, { template_key: null, goal: text.slice(0, GOAL_MAX) })
        }
        return say(say(state, 'user', text), 'agent', 'Tap "Start a new call" to set up another call.')
      }
    }
  }
}

/** The finished draft, once all the questions so far are answered. */
export function completedDraft(state: ChatState): CallDraft | null {
  const d = state.draft
  if (state.stage !== 'draft_ready' || !d.goal || !d.business_name || !d.phone_e164) return null
  return {
    template_key: d.template_key ?? null,
    goal: d.goal,
    business_name: d.business_name,
    phone_e164: d.phone_e164,
    answers: d.answers ?? {},
    profile_additions: d.profile_additions ?? [],
    constraints: d.constraints ?? [],
    preferences: d.preferences ?? [],
    fallbacks: d.fallbacks ?? [],
    never_agree: d.never_agree ?? [],
    done_lists: d.done_lists ?? [],
  }
}
