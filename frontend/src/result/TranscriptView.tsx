import { useEffect, useRef } from 'react'
import { transcriptRows, type TranscriptLine } from './transcript'

export interface TranscriptViewProps {
  lines: readonly TranscriptLine[]
  /** Shown as the business speaker's name */
  businessName: string
  /** `seq` of lines to highlight (a result row's evidence). The first highlighted turn scrolls into view. */
  highlight?: readonly number[]
}

/**
 * Call transcript (PO-4). Always visible: beside the summary on wide screens, below it on
 * narrow ones. Fragments of one sentence are joined into one turn by `transcriptRows`.
 */
export function TranscriptView({ lines, businessName, highlight = [] }: TranscriptViewProps) {
  const rows = transcriptRows(lines, businessName)
  const marked = new Set(highlight)
  const list = useRef<HTMLOListElement>(null)

  // Scroll to the first highlighted turn when the highlight changes. The key is the seq list,
  // so picking the same row again does not re-run it.
  const key = highlight.join(',')
  useEffect(() => {
    if (key === '') return
    list.current?.querySelector('.cr-line-hl')?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [key])

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
        <ol className="cr-lines" tabIndex={0} aria-label="Transcript lines" ref={list}>
          {rows.map((row) => {
            const hl = row.seqs.some((seq) => marked.has(seq))
            return (
              <li key={row.seq} className={`cr-line cr-line-${row.role}${hl ? ' cr-line-hl' : ''}`}>
                <span className="cr-who">
                  {row.speaker}
                  {row.clock && <time className="cr-clock">{row.clock}</time>}
                </span>
                <span className="cr-said">{row.text}</span>
              </li>
            )
          })}
        </ol>
      )}
      <p className="cr-note">Audio is never stored. Only the transcript is kept.</p>
    </section>
  )
}
