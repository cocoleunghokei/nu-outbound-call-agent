import { transcriptRows, type TranscriptLine } from './transcript'

export interface TranscriptViewProps {
  lines: readonly TranscriptLine[]
  /** Shown as the business speaker's name */
  businessName: string
}

/** Full call transcript (PO-4), collapsed by default so the summary stays on top. */
export function TranscriptView({ lines, businessName }: TranscriptViewProps) {
  const rows = transcriptRows(lines, businessName)

  return (
    <details className="cr-transcript">
      <summary>Full transcript{rows.length > 0 ? ` (${rows.length} lines)` : ''}</summary>
      {rows.length === 0 ? (
        <p className="cr-muted">No transcript for this call. Nobody spoke, or the call didn’t connect.</p>
      ) : (
        <ol className="cr-lines">
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
    </details>
  )
}
