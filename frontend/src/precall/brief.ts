/**
 * Turn a finished chat draft into a `brief.v1` brief (the Lotto to Coco
 * handoff) plus the `missing_fields` list for the `briefs` table.
 *
 * Privacy rule from PR #19: the brief holds field keys only, never profile
 * values. Profile values are referenced with `field_ref`, and an Ask-me-first
 * field is never referenced at all (the agent pings before sharing it, DC-6).
 * Only call details the user typed for this call go in as `literal`s.
 *
 * Pure function, so the backend brief builder (plan Step 9) can be checked
 * against the same tests.
 */
import type { CallDraft } from './chat'
import { missingTemplateFields, withProfileField } from './missing'
import type { Brief, CallTemplate, ProfileField, TaskVarFieldRef, TaskVarLiteral } from './types'

export interface BuiltBrief {
  brief: Brief
  /**
   * Required info that is still missing; Start stays blocked until it's empty.
   * An empty `fallbacks` list is never missing: PC-5 only prompts for one.
   */
  missing_fields: string[]
}

/** Opening task line for a custom call (tech doc DC-1). The goal fills `{goal_summary}`. */
export const CUSTOM_TASK_LINE = "I'm calling to {goal_summary}."

/** Profile keys every brief may share: the agent says the calling name first (DC-1) and gives a callback number. */
const BASE_SHARED_KEYS = ['calling_name', 'phone']

const PLACEHOLDER = /\{(\w+)\}/g

function placeholders(taskLine: string): string[] {
  return [...new Set([...taskLine.matchAll(PLACEHOLDER)].map((m) => m[1]))]
}

/** "Ask if they can hold a cake" -> "ask if they can hold a cake", so it reads after "I'm calling to". */
function goalSummary(goal: string): string {
  const trimmed = goal.trim().replace(/[.!?]+$/, '')
  return trimmed.charAt(0).toLowerCase() + trimmed.slice(1)
}

/**
 * Build the brief for a finished draft.
 *
 * - Template calls use the template's `task_line`; custom calls use
 *   `CUSTOM_TASK_LINE`. (Step 10's LLM will shorten long custom goals to
 *   the tech doc's 15 words; until then the goal is used as typed.)
 * - Each `{placeholder}` becomes a `literal` from the call answers, or a
 *   `field_ref` to a Share-freely profile field. Anything else is missing.
 * - `share_freely_keys` / `ask_first_keys`: the calling name, phone and the
 *   template's profile fields that the profile has, split by sharing setting.
 */
/** The user's profile plus the fields they filled in during the chat (ON-5 default sharing). */
export function profileWithAdditions(draft: CallDraft, profile: ProfileField[]): ProfileField[] {
  return draft.profile_additions.reduce((p, f) => withProfileField(p, f.key, f.value), profile)
}

export function buildBrief(draft: CallDraft, template: CallTemplate | undefined, profile: ProfileField[]): BuiltBrief {
  const fullProfile = profileWithAdditions(draft, profile)
  const byKey = new Map(fullProfile.filter((f) => f.value.trim() !== '').map((f) => [f.key, f]))
  const missing: string[] = []

  const taskLine = template ? template.task_line : CUSTOM_TASK_LINE
  const answers: Record<string, string> = template ? { ...draft.answers } : { goal_summary: goalSummary(draft.goal) }

  const taskVars: Record<string, TaskVarLiteral | TaskVarFieldRef> = {}
  for (const name of placeholders(taskLine)) {
    const answer = answers[name]?.trim()
    const field = byKey.get(name)
    if (answer) taskVars[name] = { literal: answer }
    else if (field && field.sharing === 'share_freely') taskVars[name] = { field_ref: name }
    else missing.push(name)
  }

  if (template) {
    for (const field of missingTemplateFields(template, fullProfile, draft.answers)) missing.push(field.key)
  }

  const shareKeys = [
    ...new Set([
      ...BASE_SHARED_KEYS,
      ...(template?.required_fields.filter((f) => f.source === 'profile').map((f) => f.key) ?? []),
    ]),
  ].filter((key) => byKey.has(key))
  // The calling name is required (ON-3): without it the opening line can't be said.
  if (!byKey.has('calling_name')) missing.push('calling_name')

  const brief: Brief = {
    goal: draft.goal,
    business_name: draft.business_name,
    phone_e164: draft.phone_e164,
    template_key: draft.template_key,
    task_line: taskLine,
    task_vars: taskVars,
    constraints: draft.constraints.map((i) => ({ text: i.text })),
    preferences: draft.preferences.map((i) => ({ text: i.text })),
    fallbacks: draft.fallbacks.map((i) => ({ text: i.text })),
    never_agree: draft.never_agree.map((i) => ({ text: i.text })),
    share_freely_keys: shareKeys.filter((key) => byKey.get(key)?.sharing === 'share_freely'),
    ask_first_keys: shareKeys.filter((key) => byKey.get(key)?.sharing === 'ask_first'),
  }
  return { brief, missing_fields: [...new Set(missing)] }
}

/** The DC-1 opening line, word for word as `FIRST_MESSAGE_TEMPLATE` in PR #19 `backend/app/voice/prompt_safety.py`. */
export const FIRST_MESSAGE_TEMPLATE = "Hi, I'm an AI assistant calling on behalf of {calling_name}. This call is transcribed. {task_line}"

/**
 * Preview the agent's first sentence for the review card (PC-6), the same way
 * the backend's `build_first_message` builds it: literals as typed,
 * `field_ref`s from the profile, and never an Ask-me-first value. Returns
 * null when something is missing, which also blocks Start.
 */
export function openingLine(brief: Brief, profile: ProfileField[]): string | null {
  const field = (key: string) => profile.find((f) => f.key === key && f.value.trim() !== '')
  const calling = field('calling_name')
  if (!calling) return null
  const values: Record<string, string> = {}
  for (const [name, spec] of Object.entries(brief.task_vars)) {
    if ('literal' in spec) {
      values[name] = spec.literal
      continue
    }
    const ref = field(spec.field_ref)
    if (!ref || ref.sharing === 'ask_first') return null
    values[name] = ref.value
  }
  let complete = true
  const taskLine = brief.task_line.replace(PLACEHOLDER, (_match, name: string) => {
    if (!(name in values)) complete = false
    return values[name] ?? ''
  })
  if (!complete) return null
  return FIRST_MESSAGE_TEMPLATE.replace('{calling_name}', calling.value).replace('{task_line}', taskLine)
}
