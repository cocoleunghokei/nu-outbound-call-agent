/**
 * Data shapes for the pre-call flow (PC-1 to PC-7).
 *
 * The `Brief` types below are copied from the generated contract types in
 * PR #19 (`frontend/src/contracts/generated.ts` on `feat/backend-foundation`),
 * with the same names and fields. Once #19 merges, delete the copies and
 * import from `../contracts/generated` instead. Do not add fields to the copied
 * types that the contracts do not have.
 *
 * `ProfileField`, `BriefRecord` and `CallTemplate` are local shapes for the
 * fake data. `ProfileField` and `BriefRecord` mirror the `profile_fields` and
 * `briefs` tables in PR #19. `CallTemplate` is a draft until the template
 * format is agreed with Harper (plan Step 7).
 */

// ---- Copied from PR #19 generated contracts (brief.v1) ----

export type ShareFreelyKey = string
export type AskFirstKey = string

/**
 * The reviewed brief (Lotto to Coco handoff). Field keys only, never profile
 * values; values come from call_profile_snapshots at call start.
 */
export interface Brief {
  goal: string
  business_name: string
  phone_e164: string
  template_key: string | null
  /** Template; {name} placeholders resolve from task_vars. Max 120 chars. */
  task_line: string
  task_vars: {
    [k: string]: TaskVarLiteral | TaskVarFieldRef
  }
  constraints: BriefConstraint[]
  preferences: BriefPreference[]
  /** Ranked, best first */
  fallbacks: BriefFallback[]
  never_agree: BriefNeverAgree[]
  share_freely_keys: ShareFreelyKey[]
  ask_first_keys: AskFirstKey[]
}
export interface TaskVarLiteral {
  literal: string
}
export interface TaskVarFieldRef {
  field_ref: string
}
export interface BriefConstraint {
  text: string
}
export interface BriefPreference {
  text: string
}
export interface BriefFallback {
  text: string
}
export interface BriefNeverAgree {
  text: string
}

// ---- Local shapes (not contracts) ----

/** The two sharing settings from ON-5. */
export type Sharing = 'share_freely' | 'ask_first'

/** One row of `profile_fields` (PR #19 migration). */
export interface ProfileField {
  key: string
  value: string
  sharing: Sharing
}

/** One row of `briefs` (PR #19 migration). `missing_fields` lives here, not in the brief. */
export interface BriefRecord {
  id: string
  goal: string
  business_name: string
  phone_e164: string
  template_key: string | null
  brief: Brief
  /** Keys of required info the user still has to give before the call (PC-4). */
  missing_fields: string[]
  created_at: string
}

/** A required piece of info a template needs, and how the chat asks for it. */
export interface TemplateField {
  key: string
  /** Where the value comes from: the user's profile, or asked in the chat for this call. */
  source: 'profile' | 'call'
  question: string
}

/** Draft template shape (PC-2 is Harper's; to agree in plan Step 7). */
export interface CallTemplate {
  key: string
  label: string
  /** Becomes the brief's `task_line`. */
  task_line: string
  required_fields: TemplateField[]
  /** Questions the business is likely to ask, so we can gather answers up front. */
  likely_questions: string[]
  default_constraints: BriefConstraint[]
}
