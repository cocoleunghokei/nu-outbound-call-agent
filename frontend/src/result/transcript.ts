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
  role: TranscriptRole
  text: string
  /** When the speaker started, ISO 8601, or null if the platform didn't say */
  startedAt: string | null
}

/** A transcript line ready to show. */
export interface TranscriptRow {
  seq: number
  role: TranscriptRole
  /** Who spoke, e.g. "Assistant", the business name, or "You" */
  speaker: string
  text: string
  /** Time since the first timed line, e.g. "01:15", or null if this line has no time */
  clock: string | null
}

/**
 * Turns stored transcript lines into display rows: in call order (by `seq`),
 * with a speaker name and a clock relative to the first timed line.
 * Lines with only whitespace are left out.
 */
export function transcriptRows(lines: readonly TranscriptLine[], businessName: string): TranscriptRow[] {
  const ordered = [...lines].filter((line) => line.text.trim() !== '').sort((a, b) => a.seq - b.seq)
  const start = firstTime(ordered)

  return ordered.map((line) => {
    const time = line.startedAt === null ? NaN : Date.parse(line.startedAt)
    return {
      seq: line.seq,
      role: line.role,
      speaker: speakerName(line.role, businessName),
      text: line.text.trim(),
      clock: start === null || Number.isNaN(time) ? null : formatClock(time - start),
    }
  })
}

/** Display name for a speaker. Unknown roles show "Unknown" rather than failing. */
export function speakerName(role: TranscriptRole, businessName: string): string {
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
