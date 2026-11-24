/**
 * The questions the server could not settle when it read a brief (`clarifications` on every brief
 * answer, `backend/app/briefs/rules.py`), and what answering one does to the draft.
 *
 * "at 7pm": is 7pm the latest you would accept, or the only time that works? "before 3": the
 * morning or the evening? The server never guesses these. The chat asks before the call is placed.
 * An answer either settles the item with rules, or rewrites its text (which the server then reads
 * again on the next save). "Not sure" sets `rules: []`: the server never asks about that item
 * again, and the agent judges it from the words, as it always did.
 *
 * Pure, no network. The parser never throws: a malformed question is dropped, so a bad answer from
 * the server can never block Start.
 */
import type { BriefRule } from '../contracts/generated'

/** The lists a question can be about. Never-agree items have no rules. */
export type ClarifyList = 'constraints' | 'preferences' | 'fallbacks'

export interface ClarifyAnswer {
  label: string
  /** Settles the item with these rules, or null when the answer rewrites the text instead. */
  rules: BriefRule[] | null
  /** The item's new text, or null when the answer settles it with rules. */
  text: string | null
}

export interface Clarification {
  /** The item's text as typed: how the chat finds it in the draft. */
  item: string
  list: ClarifyList
  question: string
  answers: ClarifyAnswer[]
}

/** The reply that leaves the item to the agent's own judgment during the call. */
export const SKIP_LABEL = 'Not sure, decide on the call'

const LISTS: readonly string[] = ['constraints', 'preferences', 'fallbacks']
const RULE_KINDS: readonly string[] = ['date', 'time', 'party_size', 'person']
const MAX_RULES = 4

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

function parseAnswer(raw: unknown): ClarifyAnswer | null {
  if (!isRecord(raw) || typeof raw.label !== 'string' || raw.label.trim() === '') return null
  const { rules, text } = raw
  if (typeof text === 'string' && text.trim() !== '' && (rules === null || rules === undefined)) {
    return { label: raw.label, rules: null, text }
  }
  const ok =
    Array.isArray(rules) &&
    rules.length <= MAX_RULES &&
    rules.every((r) => isRecord(r) && typeof r.kind === 'string' && RULE_KINDS.includes(r.kind)) &&
    (text === null || text === undefined)
  return ok ? { label: raw.label, rules: rules as BriefRule[], text: null } : null
}

/** The valid questions in the server's `clarifications`; anything else is dropped. */
export function parseClarifications(raw: unknown): Clarification[] {
  if (!Array.isArray(raw)) return []
  const out: Clarification[] = []
  for (const entry of raw) {
    if (!isRecord(entry)) continue
    const { item, list, question, answers } = entry
    if (typeof item !== 'string' || item === '' || typeof list !== 'string' || !LISTS.includes(list)) continue
    if (typeof question !== 'string' || question === '' || !Array.isArray(answers)) continue
    const valid = answers.map(parseAnswer).filter((a): a is ClarifyAnswer => a !== null)
    if (valid.length > 0) out.push({ item, list: list as ClarifyList, question, answers: valid })
  }
  return out
}

/**
 * `items` with the question's item changed by the user's `choice`: an index into
 * `question.answers`, or `'skip'`. Returns null when the item isn't there any more (the draft
 * changed) or the choice isn't an answer.
 */
export function answerItems<T extends { text: string; rules?: BriefRule[] }>(
  items: readonly T[],
  question: Clarification,
  choice: number | 'skip',
): T[] | null {
  const at = items.findIndex((item) => item.text === question.item)
  if (at < 0) return null
  const answer = choice === 'skip' ? null : question.answers[choice]
  if (choice !== 'skip' && answer === undefined) return null
  return items.map((item, index) => {
    if (index !== at) return item
    if (answer === null) return { ...item, rules: [] }
    if (answer.text !== null) {
      const { rules: _read, ...rest } = item // new words: the server reads them again
      void _read
      return { ...rest, text: answer.text } as T
    }
    return { ...item, rules: answer.rules ?? [] }
  })
}

/** The label the user's tap is shown as in the chat. */
export function answerLabel(question: Clarification, choice: number | 'skip'): string {
  return choice === 'skip' ? SKIP_LABEL : (question.answers[choice]?.label ?? SKIP_LABEL)
}
