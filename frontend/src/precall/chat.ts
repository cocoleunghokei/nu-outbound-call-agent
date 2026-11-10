/**
 * Chat home logic (PC-1, PC-3, PC-4): pick a template or a custom call, give
 * the business name, number and goal, then answer only the questions the
 * profile can't already answer. Pure functions only, so the whole flow is
 * unit-tested without React. `ChatHome.tsx` just renders this state.
 *
 * The flow stops at `stage: 'draft_ready'`. Step 5 continues from there with
 * constraints and fallbacks.
 */
import {
  describeProfileKeys,
  knownProfileFields,
  missingTemplateFields,
  withProfileField,
  type CallAnswers,
} from './missing'
import { checkPhone, formatUsPhone } from './phone'
import type { CallTemplate, ProfileField, TemplateField } from './types'

export type Speaker = 'agent' | 'user'

export interface ChatMessage {
  id: number
  from: Speaker
  text: string
}

/** What the chat is waiting for the user to type. `template_field` uses `ChatState.fieldKey`. */
export type Awaiting = 'goal' | 'business_name' | 'phone' | 'template_field' | null

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
}

export type ChatAction =
  | { type: 'pick_template'; templateKey: string }
  | { type: 'pick_custom' }
  | { type: 'send'; text: string }
  | { type: 'new_call' }

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
    placeholder: prompt.placeholder,
    chips: [],
  }
}

function waitForTemplateField(state: ChatState, field: TemplateField): ChatState {
  return {
    ...say(state, 'agent', field.question),
    awaiting: 'template_field',
    fieldKey: field.key,
    placeholder: 'Type your answer',
    chips: [],
  }
}

function showHome(state: ChatState, ctx: ChatContext, text: string): ChatState {
  return {
    ...say(state, 'agent', text),
    chips: homeChips(ctx),
    awaiting: null,
    fieldKey: null,
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
    placeholder: DEFAULT_PLACEHOLDER,
    chips: [NEW_CALL_CHIP],
  }
}

function startDraft(state: ChatState, ctx: ChatContext, userText: string, draft: Partial<CallDraft>): ChatState {
  return askNext(
    {
      ...say(state, 'user', userText),
      stage: 'collecting',
      draft: { answers: {}, profile_additions: [], ...draft },
      knownAnnounced: false,
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
        return startDraft(state, ctx, template.label, { template_key: template.key, goal: template.label })
      }
      case 'pick_custom':
        if (state.stage !== 'home') return state
        return startDraft(state, ctx, 'Custom call', { template_key: null })
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
  }
}
