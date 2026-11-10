import { formatCallDate, outcomeDisplay, sortNewestFirst } from './format'
import type { CallHistoryItem } from './types'
import './CallHistory.css'

export interface CallHistoryProps {
  /** Calls to list, in any order. They are shown newest first. */
  items: readonly CallHistoryItem[]
  /** Called with the call's id when the user opens a row. Omit to render rows as plain text. */
  onOpen?: (callId: string) => void
}

/** Call history list (PO-6): one row per past call with business, goal, outcome and date. */
export function CallHistory({ items, onOpen }: CallHistoryProps) {
  const sorted = sortNewestFirst(items)

  return (
    <section className="ch" aria-labelledby="ch-title">
      <h2 id="ch-title" className="ch-title">
        Call history
      </h2>
      {sorted.length === 0 ? (
        <p className="ch-empty">No calls yet. Your past calls will show up here.</p>
      ) : (
        <ul className="ch-list">
          {sorted.map((item) => (
            <li key={item.id}>
              <CallHistoryRow item={item} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function CallHistoryRow({ item, onOpen }: { item: CallHistoryItem; onOpen?: (callId: string) => void }) {
  const outcome = outcomeDisplay(item)
  const content = (
    <>
      <span className="ch-icon" aria-hidden="true">
        <PhoneIcon />
      </span>
      <span className="ch-text">
        <b className="ch-business">{item.businessName}</b>
        <span className="ch-goal">{item.goal}</span>
        <span className="ch-meta">
          <span className={`ch-badge ch-badge-${outcome.tone}`}>{outcome.label}</span>
          <time dateTime={item.createdAt}>{formatCallDate(item.createdAt)}</time>
        </span>
      </span>
    </>
  )

  if (!onOpen) return <div className="ch-row">{content}</div>
  return (
    <button type="button" className="ch-row ch-row-button" onClick={() => onOpen(item.id)}>
      {content}
    </button>
  )
}

function PhoneIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z" />
    </svg>
  )
}
