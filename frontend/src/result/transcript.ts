import type { TranscriptRole } from '../contracts/generated'
import { formatClock } from '../live-call/format'

/**
 * One final transcript line of a call (PO-4).
 *
 * Built from a `transcript.final` call event: `seq` comes from the event envelope,
 * the rest from its payload (`contracts/payloads/transcript.final.v1.json`).
 * Partial lines are never stored, so only finals appear here.
 */
export interface TranscriptLine {
  /** Event order within the call, gap-free */
  seq: number
  /** "unknown" for a role this version doesn't know yet */
  role: TranscriptRole | 'unknown'
  text: string
  /** When the speaker started, ISO 8601, or null if the platform didn't say */
  startedAt: string | null
}

/** A transcript line ready to show. */
export interface TranscriptRow {
  seq: number
  role: TranscriptLine['role']
  /** Who spoke, e.g. "Assistant", the business name, or "You" */
  speaker: string
  text: string
  /** Time since the first timed line, e.g. "01:15", or null if this line has no time */
  clock: string | null
}

/** Lines from the same speaker this close together (ms) are one spoken turn. */
export const TURN_GAP_MS = 5000

/**
 * Turns stored transcript lines into display rows, with a speaker name and a
 * clock relative to the first timed line. Lines with only whitespace are left out.
 * The speech-to-text service often splits one sentence across several lines, so
 * consecutive lines from the same speaker that start within `TURN_GAP_MS` of each
 * other are joined into one row (it keeps the first line's `seq` and clock).
 * Lines without a readable time are never joined.
 *
 * Keeps the order it is given: the backend returns lines in spoken order
 * (`occurred_at`, then `seq`). `seq` is arrival order, so sorting by it would put
 * a reply that arrived early before the question it answers.
 */
export function transcriptRows(lines: readonly TranscriptLine[], businessName: string): TranscriptRow[] {
  const ordered = lines.filter((line) => line.text.trim() !== '')
  const start = firstTime(ordered)

  const rows: TranscriptRow[] = []
  let previous: { role: TranscriptLine['role']; time: number } | null = null

  for (const line of ordered) {
    const time = line.startedAt === null ? NaN : Date.parse(line.startedAt)
    const text = line.text.trim()
    const joinsPrevious =
      previous !== null &&
      !Number.isNaN(time) &&
      previous.role === line.role &&
      time - previous.time >= 0 &&
      time - previous.time <= TURN_GAP_MS

    if (joinsPrevious) {
      const last = rows[rows.length - 1]
      last.text = `${last.text} ${text}`
    } else {
      rows.push({
        seq: line.seq,
        role: line.role,
        speaker: speakerName(line.role, businessName),
        text,
        clock: start === null || Number.isNaN(time) ? null : formatClock(time - start),
      })
    }
    previous = Number.isNaN(time) ? null : { role: line.role, time }
  }
  return rows
}

/** Display name for a speaker. Unknown roles show "Unknown" rather than failing. */
export function speakerName(role: TranscriptLine['role'], businessName: string): string {
  switch (role) {
    case 'agent':
      return 'Assistant'
    case 'business':
      return businessName.trim() || 'Business'
    case 'user':
      return 'You'
    default:
      return 'Unknown'
  }
}

/** Earliest readable start time among the lines, in ms, or null if none has one. */
function firstTime(lines: readonly TranscriptLine[]): number | null {
  let earliest: number | null = null
  for (const line of lines) {
    if (line.startedAt === null) continue
    const time = Date.parse(line.startedAt)
    if (Number.isNaN(time)) continue
    if (earliest === null || time < earliest) earliest = time
  }
  return earliest
}
