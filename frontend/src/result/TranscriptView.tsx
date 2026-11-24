import { transcriptRows, type TranscriptLine } from './transcript'

export interface TranscriptViewProps {
  lines: readonly TranscriptLine[]
  /** Shown as the business speaker's name */
  businessName: string
}

/**
 * Call transcript (PO-4). Always visible: beside the summary on wide screens, below it on
 * narrow ones. Fragments of one sentence are joined into one turn by `transcriptRows`.
 */
export function TranscriptView({ lines, businessName }: TranscriptViewProps) {
  const rows = transcriptRows(lines, businessName)

  return (
    <section className="cr-transcript" aria-labelledby="cr-transcript-title">
      <header className="cr-transcript-head">
        <h3 id="cr-transcript-title">Transcript</h3>
        {rows.length > 0 && <span className="cr-muted">{rows.length} turns</span>}
      </header>
      {rows.length === 0 ? (
        <p className="cr-muted cr-transcript-empty">
          No transcript for this call. Nobody spoke, or the call didn’t connect.
        </p>
      ) : (
        <ol className="cr-lines" tabIndex={0} aria-label="Transcript lines">
          {rows.map((row) => (
            <li key={row.seq} className={`cr-line cr-line-${row.role}`}>
              <span className="cr-who">
                {row.speaker}
                {row.clock && <time className="cr-clock">{row.clock}</time>}
              </span>
              <span className="cr-said">{row.text}</span>
            </li>
          ))}
        </ol>
      )}
      <p className="cr-note">Audio is never stored. Only the transcript is kept.</p>
    </section>
  )
}
