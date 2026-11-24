/**
 * Chat home logic (PC-1, PC-3, PC-4, PC-5): pick a template or a custom call,
 * give the business name, number and goal, answer only the questions the
 * profile can't already answer, then ask for must-haves, nice-to-haves and what
 * the user is OK with instead. Never-agree items start as "a deposit or fee" and
 * are not asked; the review card can change them. Pure functions only, so the whole flow is
 * unit-tested without React. `ChatHome.tsx` just renders this state.
 *
 * At `stage: 'draft_ready'` the review card shows (PC-6). Each part can be
 * edited (the chat re-asks just that part, then shows the card again), and
 * Start moves to `stage: 'started'` once nothing is missing.
 */
import { buildBrief, type BuiltBrief } from './brief'
import { defaultSharing, missingTemplateFields, profileLabel, withProfileField, type CallAnswers } from './missing'
import { checkPhone } from './phone'
import type { BriefConstraint, CallTemplate, ProfileField } from './types'

export type Speaker = 'agent' | 'user'

export interface ChatMessage {
  id: number
  from: Speaker
  text: string
}

/**
 * What the chat is waiting for the user to type, for a custom call. `list_item` uses
 * `ChatState.listKey`. A template call has no typed questions: it uses the form (`stage: 'form'`).
 */
export type Awaiting = 'goal' | 'business_name' | 'phone' | 'list_item' | null

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
  /** Review the plan: the template form was filled in (see `FormSubmission`). */
  | { type: 'submit_form'; form: FormSubmission }
  | { type: 'send'; text: string }
  /** Start over: clears the chat back to the greeting, dropping any draft and the history. */
  | { type: 'restart' }
  /** Finish the current PC-5 list. `label` is what the user tapped, shown as their reply. */
  | { type: 'list_done'; label: string }
  /** Change one part of the call from the review card (PC-6). */
  | { type: 'edit'; target: EditTarget }
  /** Start the call from the review card (PC-6). Ignored while anything is missing. */
  | { type: 'start' }
  /**
   * The brief was saved but calling isn't connected yet (Coco's `POST /calls`
   * isn't deployed). Locks the review like Start, without claiming a call.
   */
  | { type: 'saved_not_connected' }
  /** The agent says something without changing the flow, e.g. an API error after Start. */
  | { type: 'note'; text: string }

/**
 * What the review card can send back to the chat: the custom goal, the
 * template details, the business and number, or one PC-5 list.
 */
export type EditTarget = 'goal' | 'details' | 'calling' | ListKey

/** A one-tap reply shown above the composer. */
export interface Chip {
  label: string
  action: ChatAction
  quiet?: boolean
}

/**
 * What the template form sends. Everything is raw: the chat validates it, so a bad number or a
 * missing answer comes back as `ChatState.formError` and nothing is lost.
 */
export interface FormSubmission {
  business_name: string
  /** The number as typed; checked like the custom call's number (PC-7). */
  phone: string
  /** Answers to the template's call fields, already in the words the agent says ("December 23", "7 PM"). */
  answers: CallAnswers
  /** Profile fields the profile lacked and the user filled in, by key. Saved with ON-5 default sharing. */
  profile_values: Record<string, string>
  constraints: string[]
  preferences: string[]
  fallbacks: string[]
  never_agree: string[]
}

export type Stage = 'home' | 'collecting' | 'form' | 'draft_ready' | 'started'

export interface ChatState {
  messages: ChatMessage[]
  chips: Chip[]
  awaiting: Awaiting
  /** The PC-5 list being filled, when `awaiting` is `list_item`. */
  listKey: ListKey | null
  /** Whether the chat has already nudged the user to add a backup (PC-5 asks for at least one). */
  fallbackNudged: boolean
  /** The message the review card shows under, once the draft is ready (PC-6). */
  reviewAfterId: number | null
  /** Composer hint for what to type next. */
  placeholder: string
  stage: Stage
  /** How the review ended once `stage` is `started`: a call placed, or only saved. */
  startOutcome: 'calling' | 'saved' | null
  draft: Partial<CallDraft>
  /** Why the template form could not be reviewed yet, shown under the form. Null when fine. */
  formError: string | null
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
    ask: 'What must be true for this to work? For example, "before 3pm" or "vegetarian options".',
    again: () => 'Got it. Anything else that must be true?',
    none: 'Nothing specific',
    placeholder: 'A must-have, e.g. before 3pm',
  },
  preferences: {
    ask: 'Anything that would be nice to have, but is not required? For example, "a window seat".',
    again: () => 'Noted. Anything else that would be nice?',
    none: 'Nothing',
    placeholder: 'Something you would like',
  },
  fallbacks: {
    ask: "If a must-have isn't possible, what are you OK with instead? I'll take a match without bothering you.",
    again: () => 'Saved. Anything else you would be OK with?',
    none: 'Nothing, ask me',
    placeholder: 'Something you are OK with, e.g. Friday morning',
  },
  never_agree: {
    ask: "Anything I should never agree to?",
    again: () => 'Got it. Anything else I should never agree to?',
    none: 'Nothing else',
    placeholder: 'Something I should never agree to',
    suggestions: ['A deposit or fee'],
  },
}

/** Pre-filled never-agree items; the chat does not ask for these. */
const DEFAULT_NEVER_AGREE: ListItem[] = [{ text: 'A deposit or fee' }]

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
    listKey: null,
    placeholder: prompt.placeholder,
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
    listKey: null,
    fallbackNudged: false,
    reviewAfterId: null,
    placeholder: DEFAULT_PLACEHOLDER,
    stage: 'home',
    startOutcome: null,
    draft: {},
    formError: null,
  }
}

/** The first screen a returning (onboarded) user sees: the chat home (PC-1). */
export function initialChatState(ctx: ChatContext): ChatState {
  const empty: ChatState = {
    messages: [],
    chips: [],
    awaiting: null,
    listKey: null,
    fallbackNudged: false,
    reviewAfterId: null,
    placeholder: DEFAULT_PLACEHOLDER,
    stage: 'home',
    startOutcome: null,
    draft: {},
    formError: null,
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

/** Ask for the next thing the draft is missing, or finish this part of the flow. */
function askNext(state: ChatState): ChatState {
  const d = state.draft
  if (!d.goal) return waitFor(state, 'goal')
  if (!d.business_name) return waitFor(state, 'business_name')
  if (!d.phone_e164) return waitFor(state, 'phone')

  // PC-5: must-haves, nice-to-haves and backups are asked; never-agree is pre-filled (its row can still be edited).
  const nextList = LIST_ORDER.find((key) => !(d.done_lists ?? []).includes(key))
  if (nextList) {
    const prefilled = d[nextList] ?? []
    // Template defaults (PC-2) are already in the list: say so instead of asking from scratch.
    const text =
      prefilled.length > 0
        ? `I've added the usual must-haves for this call: ${prefilled.map((i) => i.text).join('; ')}. Anything else that must be true?`
        : LIST_PROMPTS[nextList].ask
    return waitForListItem(state, nextList, text)
  }

  const done = say(state, 'agent', "Here's the plan. Give it a quick look, then start the call.")
  return {
    ...done,
    reviewAfterId: done.nextId - 1,
    stage: 'draft_ready',
    awaiting: null,
    listKey: null,
    placeholder: DEFAULT_PLACEHOLDER,
    chips: [],
  }
}

const FORM_PLACEHOLDER = 'Fill in the form above'

/** Show the template form (stage "form"): nothing is typed in the chat for a template call. */
function openForm(state: ChatState, template: CallTemplate): ChatState {
  const withUser = say({ ...state, chips: [] }, 'user', template.label)
  return {
    ...say(withUser, 'agent', "Sure. Fill this in and I'll show you the plan before I dial."),
    stage: 'form',
    awaiting: null,
    listKey: null,
    chips: [],
    placeholder: FORM_PLACEHOLDER,
    formError: null,
    startOutcome: null,
    draft: {
      template_key: template.key,
      goal: template.label,
      answers: {},
      profile_additions: [],
      // PC-2: each template's default constraints start the list; the user can add more.
      constraints: template.default_constraints.map((c) => ({ text: c.text })).slice(0, LIST_MAX_ITEMS),
      preferences: [],
      fallbacks: [],
      never_agree: DEFAULT_NEVER_AGREE.map((item) => ({ ...item })),
      done_lists: [],
    },
  }
}

/** A short name for a template field in "Still needed: ..." ("party_size" -> "party size"). */
function fieldName(key: string): string {
  return profileLabel(key).toLowerCase()
}

/** Trim, drop blanks and repeats (any case), keep at most `LIST_MAX_ITEMS`. Null if an item is too long. */
function cleanList(items: string[]): ListItem[] | null {
  const seen = new Set<string>()
  const out: ListItem[] = []
  for (const raw of items) {
    const text = raw.trim()
    if (text === '' || seen.has(text.toLowerCase())) continue
    if (text.length > LIST_ITEM_MAX) return null
    seen.add(text.toLowerCase())
    out.push({ text })
  }
  return out.slice(0, LIST_MAX_ITEMS)
}

/**
 * Validate the template form and move to the review card (PC-6), or stay on the form with
 * `formError` set. Checks the business name, the number (PC-7, same rules as a custom call),
 * every field the template still needs (PC-4) and the length of each answer.
 */
function submitForm(state: ChatState, ctx: ChatContext, form: FormSubmission): ChatState {
  const template = templateOf(ctx, state.draft.template_key)
  if (state.stage !== 'form' || !template) return state
  const fail = (message: string): ChatState => ({ ...state, formError: message })

  const businessName = form.business_name.trim()
  if (businessName === '') return fail('Enter the name of the place to call.')
  if (businessName.length > BUSINESS_NAME_MAX) return fail(`That name is too long. Keep it under ${BUSINESS_NAME_MAX} characters.`)
  const phone = checkPhone(form.phone)
  if (!phone.ok) return fail(phone.message)

  const answers: CallAnswers = {}
  for (const [key, value] of Object.entries(form.answers)) {
    const text = value.trim()
    if (text.length > ANSWER_MAX) return fail(`Keep each answer under ${ANSWER_MAX} characters.`)
    if (text !== '') answers[key] = text
  }
  const profileAdditions: ProfileField[] = Object.entries(form.profile_values)
    .map(([key, value]) => ({ key, value: value.trim(), sharing: defaultSharing(key) }))
    .filter((f) => f.value !== '')
  if (profileAdditions.some((f) => f.value.length > ANSWER_MAX)) return fail(`Keep each answer under ${ANSWER_MAX} characters.`)

  const profile = profileAdditions.reduce((p, f) => withProfileField(p, f.key, f.value), ctx.profile)
  const missing = missingTemplateFields(template, profile, answers)
  if (missing.length > 0) return fail(`Still needed: ${missing.map((f) => fieldName(f.key)).join(', ')}.`)

  const lists = {
    constraints: cleanList(form.constraints),
    preferences: cleanList(form.preferences),
    fallbacks: cleanList(form.fallbacks),
    never_agree: cleanList(form.never_agree),
  }
  if (lists.constraints === null || lists.preferences === null || lists.fallbacks === null || lists.never_agree === null) {
    return fail(`Keep each item under ${LIST_ITEM_MAX} characters.`)
  }

  const withUser = say({ ...state, chips: [] }, 'user', 'Review plan')
  const done = say(withUser, 'agent', "Here's the plan. Give it a quick look, then start the call.")
  return {
    ...done,
    reviewAfterId: done.nextId - 1,
    stage: 'draft_ready',
    awaiting: null,
    listKey: null,
    placeholder: DEFAULT_PLACEHOLDER,
    formError: null,
    chips: [],
    draft: {
      ...state.draft,
      business_name: businessName,
      phone_e164: phone.e164,
      answers,
      profile_additions: profileAdditions,
      constraints: lists.constraints,
      preferences: lists.preferences,
      fallbacks: lists.fallbacks,
      never_agree: lists.never_agree,
      done_lists: [...LIST_ORDER],
    },
  }
}

function startDraft(state: ChatState, userText: string, draft: Partial<CallDraft>): ChatState {
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
        // Not asked: the agent already checks before any payment, so "a deposit or fee" is a
        // default the user can change from the review card.
        never_agree: DEFAULT_NEVER_AGREE.map((item) => ({ ...item })),
        done_lists: ['never_agree'],
        ...draft,
      },
      formError: null,
      fallbackNudged: false,
      chips: [],
    },
  )
}

/** Mark a PC-5 list as finished and move on. */
function finishList(state: ChatState, key: ListKey): ChatState {
  const done = [...(state.draft.done_lists ?? []), key]
  return askNext({ ...state, draft: { ...state.draft, done_lists: done } })
}

/** Add one item to the current PC-5 list. */
function addListItem(state: ChatState, key: ListKey, text: string): ChatState {
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
    return finishList(say(withItem, 'agent', `That's ${LIST_MAX_ITEMS}, the most I can keep track of here.`), key)
  }
  return waitForListItem(withItem, key, LIST_PROMPTS[key].again(updated.length))
}

/** What the user says in the chat when they tap Edit on a review row. */
const EDIT_LABELS: Record<EditTarget, string> = {
  goal: 'Change the goal',
  details: 'Change the details',
  calling: 'Change who to call',
  constraints: 'Change my must-haves',
  preferences: 'Change my nice-to-haves',
  fallbacks: 'Change what I am OK with',
  never_agree: 'Change what not to agree to',
}

/**
 * Clear one part of the draft so `askNext` asks for it again. When it's
 * answered, every other part is still done, so the review card comes back.
 * Lists are cleared and asked again from the start (at most 5 items each).
 */
function editDraft(draft: Partial<CallDraft>, target: EditTarget): Partial<CallDraft> {
  switch (target) {
    case 'goal':
      return { ...draft, goal: undefined }
    case 'details':
      return { ...draft, answers: {} }
    case 'calling':
      return { ...draft, business_name: undefined, phone_e164: undefined }
    default:
      return { ...draft, [target]: [], done_lists: (draft.done_lists ?? []).filter((k) => k !== target) }
  }
}

/** Whether a review row can be edited for this draft: a template's goal is the template itself. */
export function canEdit(draft: Partial<CallDraft>, target: EditTarget): boolean {
  if (target === 'goal') return draft.template_key === null
  if (target === 'details') return draft.template_key !== null && draft.template_key !== undefined
  return true
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
    case 'list_item':
      if (!state.listKey) return withUser
      return addListItem(withUser, state.listKey, text)
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
        return openForm(state, template)
      }
      case 'submit_form':
        return submitForm(state, ctx, action.form)
      case 'pick_custom':
        if (state.stage !== 'home') return state
        return startDraft(state, 'Custom call', { template_key: null })
      case 'list_done': {
        const key = state.listKey
        if (state.awaiting !== 'list_item' || !key) return state
        const withUser = say({ ...state, chips: [] }, 'user', action.label)
        // PC-5: prompt for at least one backup, once. A second "no" is accepted.
        if (key === 'fallbacks' && (state.draft.fallbacks ?? []).length === 0 && !state.fallbackNudged) {
          return { ...waitForListItem(withUser, key, FALLBACK_NUDGE), fallbackNudged: true }
        }
        return finishList(withUser, key)
      }
      case 'edit': {
        if (state.stage !== 'draft_ready' || !canEdit(state.draft, action.target)) return state
        const withUser = say({ ...state, chips: [] }, 'user', EDIT_LABELS[action.target])
        // A template call is edited in the form, with everything already filled in.
        if (state.draft.template_key) return { ...withUser, stage: 'form', formError: null, placeholder: FORM_PLACEHOLDER }
        return askNext({ ...withUser, stage: 'collecting', draft: editDraft(state.draft, action.target) })
      }
      case 'start': {
        const review = reviewOf(state, ctx)
        if (state.stage !== 'draft_ready' || !review || review.missing_fields.length > 0) return state
        const started = say(
          say({ ...state, chips: [] }, 'user', 'Start call'),
          'agent',
          `Calling ${review.brief.business_name} now. I'll ask you here if I need anything.`,
        )
        return { ...started, stage: 'started', startOutcome: 'calling', chips: [] }
      }
      case 'saved_not_connected': {
        const review = reviewOf(state, ctx)
        if (state.stage !== 'draft_ready' || !review || review.missing_fields.length > 0) return state
        const saved = say(
          say({ ...state, chips: [] }, 'user', 'Start call'),
          'agent',
          "Saved your brief. Calling isn't connected yet, so I haven't dialed.",
        )
        return { ...saved, stage: 'started', startOutcome: 'saved', chips: [] }
      }
      case 'note': {
        const text = action.text.trim()
        return text === '' ? state : say(state, 'agent', text)
      }
      case 'restart':
        // PC-1: the user can start over at any time. The chat goes back to the greeting, so a
        // finished call's form and review do not pile up above the next one.
        return initialChatState(ctx)
      case 'send': {
        const text = action.text.trim()
        if (text === '') return state
        if (state.awaiting) return answer(state, text)
        if (state.stage === 'home') {
          // Typing at the home screen starts a custom call with that goal (PC-3).
          return startDraft(state, text, { template_key: null, goal: text.slice(0, GOAL_MAX) })
        }
        if (state.stage === 'form') {
          return say(say(state, 'user', text), 'agent', 'Fill in the form above, then tap Review plan.')
        }
        return say(say(state, 'user', text), 'agent', 'Tap "Restart" at the top to set up another call.')
      }
    }
  }
}

/** The finished draft, once all the questions so far are answered. */
export function completedDraft(state: ChatState): CallDraft | null {
  const d = state.draft
  if (state.stage !== 'draft_ready' && state.stage !== 'started') return null
  if (!d.goal || !d.business_name || !d.phone_e164) return null
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

/**
 * The brief behind the review card (PC-6), built from the finished draft.
 * Null until the chat reaches the review. `missing_fields` must be empty to Start.
 */
export function reviewOf(state: ChatState, ctx: ChatContext): BuiltBrief | null {
  const draft = completedDraft(state)
  if (!draft) return null
  return buildBrief(draft, templateOf(ctx, draft.template_key), ctx.profile)
}
