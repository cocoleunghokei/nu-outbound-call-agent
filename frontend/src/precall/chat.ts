/**
 * Chat home logic (PC-1, PC-3): pick a template or a custom call, then give
 * the business name, number and goal. Pure functions only, so the whole flow
 * is unit-tested without React. `ChatHome.tsx` just renders this state.
 *
 * Step 3 stops once the call draft has a business, a checked number and a
 * goal. Step 4 continues from `stage: 'draft_ready'` with the missing-info
 * questions.
 */
import { checkPhone, formatUsPhone } from './phone'
import type { CallTemplate } from './types'

export type Speaker = 'agent' | 'user'

export interface ChatMessage {
  id: number
  from: Speaker
  text: string
}

/** What the chat is waiting for the user to type. */
export type Awaiting = 'goal' | 'business_name' | 'phone' | null

/** What the user has told us about the call so far. Becomes part of the brief later. */
export interface CallDraft {
  template_key: string | null
  goal: string
  business_name: string
  phone_e164: string
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
  /** Composer hint for what to type next. */
  placeholder: string
  stage: Stage
  draft: Partial<CallDraft>
  nextId: number
}

/** What the chat needs from outside: the user's calling name and the templates. */
export interface ChatContext {
  callingName: string
  templates: CallTemplate[]
}

export const BUSINESS_NAME_MAX = 100
export const GOAL_MAX = 300

const DEFAULT_PLACEHOLDER = 'Send a message'

const PROMPTS: Record<Exclude<Awaiting, null>, { text: string; placeholder: string }> = {
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

function waitFor(state: ChatState, field: Exclude<Awaiting, null>): ChatState {
  const prompt = PROMPTS[field]
  return { ...say(state, 'agent', prompt.text), awaiting: field, placeholder: prompt.placeholder, chips: [] }
}

function showHome(state: ChatState, ctx: ChatContext, text: string): ChatState {
  return {
    ...say(state, 'agent', text),
    chips: homeChips(ctx),
    awaiting: null,
    placeholder: DEFAULT_PLACEHOLDER,
    stage: 'home',
    draft: {},
  }
}

/** The first screen a returning (onboarded) user sees: the chat home (PC-1). */
export function initialChatState(ctx: ChatContext): ChatState {
  const empty: ChatState = {
    messages: [],
    chips: [],
    awaiting: null,
    placeholder: DEFAULT_PLACEHOLDER,
    stage: 'home',
    draft: {},
    nextId: 1,
  }
  return showHome(empty, ctx, `Hi ${ctx.callingName}! Who should I call? Pick a template or start a custom call.`)
}

/** Ask for the next thing the draft is missing, or finish Step 3's part of the flow. */
function askNext(state: ChatState): ChatState {
  const d = state.draft
  if (!d.goal) return waitFor(state, 'goal')
  if (!d.business_name) return waitFor(state, 'business_name')
  if (!d.phone_e164) return waitFor(state, 'phone')
  const done = say(
    state,
    'agent',
    `Got it. I'll call ${d.business_name} at ${formatUsPhone(d.phone_e164)} to: ${d.goal}`,
  )
  return { ...done, stage: 'draft_ready', awaiting: null, placeholder: DEFAULT_PLACEHOLDER, chips: [NEW_CALL_CHIP] }
}

function startDraft(state: ChatState, userText: string, draft: Partial<CallDraft>): ChatState {
  return askNext({ ...say(state, 'user', userText), stage: 'collecting', draft, chips: [] })
}

/** Handle what the user typed for the field the chat is waiting on. */
function answer(state: ChatState, text: string): ChatState {
  const withUser = say(state, 'user', text)
  switch (state.awaiting) {
    case 'goal':
      if (text.length > GOAL_MAX) {
        return say(withUser, 'agent', `Could you say that in under ${GOAL_MAX} characters?`)
      }
      return askNext({ ...withUser, draft: { ...state.draft, goal: text } })
    case 'business_name':
      if (text.length > BUSINESS_NAME_MAX) {
        return say(withUser, 'agent', `That name is too long. Keep it under ${BUSINESS_NAME_MAX} characters.`)
      }
      return askNext({ ...withUser, draft: { ...state.draft, business_name: text } })
    case 'phone': {
      // PC-7: checked here, and again by the backend.
      const result = checkPhone(text)
      if (!result.ok) return say(withUser, 'agent', result.message)
      return askNext({ ...withUser, draft: { ...state.draft, phone_e164: result.e164 } })
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
        const template = ctx.templates.find((t) => t.key === action.templateKey)
        if (!template || state.stage !== 'home') return state
        return startDraft(state, template.label, { template_key: template.key, goal: template.label })
      }
      case 'pick_custom':
        if (state.stage !== 'home') return state
        return startDraft(state, 'Custom call', { template_key: null })
      case 'new_call':
        // PC-1: the user can start a new call at any time; an unfinished draft is dropped.
        return showHome({ ...say(state, 'user', 'Start a new call'), chips: [] }, ctx, 'Sure. Who should I call?')
      case 'send': {
        const text = action.text.trim()
        if (text === '') return state
        if (state.awaiting) return answer(state, text)
        if (state.stage === 'home') {
          // Typing at the home screen starts a custom call with that goal (PC-3).
          return startDraft(state, text, { template_key: null, goal: text.slice(0, GOAL_MAX) })
        }
        return say(say(state, 'user', text), 'agent', 'Tap "Start a new call" to set up another call.')
      }
    }
  }
}

/** The finished draft, once Step 3's questions are answered. */
export function completedDraft(state: ChatState): CallDraft | null {
  const d = state.draft
  if (state.stage !== 'draft_ready' || !d.goal || !d.business_name || !d.phone_e164) return null
  return { template_key: d.template_key ?? null, goal: d.goal, business_name: d.business_name, phone_e164: d.phone_e164 }
}
