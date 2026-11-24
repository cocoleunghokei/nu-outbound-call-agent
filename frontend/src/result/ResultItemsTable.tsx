import { approvalNote, formatCents, isChange, summaryLabel } from './format'
import type { ResultItem } from './types'

export interface ResultItemsTableProps {
  items: readonly ResultItem[]
  /** Index of the row whose transcript lines are highlighted, or null */
  selected: number | null
  /** The user asked to see a row in the transcript. Not called for a row with no evidence. */
  onShow: (index: number, seqs: readonly number[]) => void
}

/**
 * What the user asked for next to what they got (PO-2). Rows that differ are tinted and show the
 * original struck through; rows that match show a check. A row the transcript backs has a
 * "Show in transcript" button.
 */
export function ResultItemsTable({ items, selected, onShow }: ResultItemsTableProps) {
  if (items.length === 0) return null
  const anyChange = items.some(isChange)

  return (
    <section className="cr-group cr-items" aria-labelledby="cr-items-title">
      <div className="cr-items-head">
        <h3 id="cr-items-title">What you asked for vs. what you got</h3>
        <span className={anyChange ? 'cr-delta' : 'cr-delta cr-delta-ok'}>{summaryLabel(items)}</span>
      </div>
      <ul className="cr-rows">
        {items.map((item, index) => {
          const changed = isChange(item)
          const note = approvalNote(item)
          const classes = ['cr-row', changed ? 'cr-row-changed' : 'cr-row-ok', selected === index ? 'cr-row-sel' : '']
          return (
            <li key={`${item.label}-${index}`} className={classes.join(' ').trim()}>
              <span className="cr-row-what">{item.label}</span>
              <span className="cr-row-asked">
                <span className="cr-sr">Asked: </span>
                {item.asked === null ? <i>Not in your brief</i> : changed ? <s>{item.asked}</s> : item.asked}
              </span>
              <span className="cr-row-arrow" aria-hidden="true">
                →
              </span>
              <span className="cr-row-got">
                <span className="cr-sr">Got: </span>
                <b>{item.got}</b>
                {item.costCents !== null && <span className="cr-cost">{formatCents(item.costCents)}</span>}
                {item.reason && <small>{item.reason}</small>}
                {note && <small className={item.approvedBy === 'none' ? 'cr-row-warn' : ''}>{note}</small>}
                {!item.verified && <small className="cr-unverified">Not found in transcript</small>}
                {item.evidence.length > 0 && (
                  <button
                    type="button"
                    className="cr-show"
                    aria-pressed={selected === index}
                    onClick={() => onShow(index, item.evidence)}
                  >
                    Show in transcript
                  </button>
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
