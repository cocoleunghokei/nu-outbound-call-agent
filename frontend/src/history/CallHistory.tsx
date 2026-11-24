import { useState } from 'react'
import { formatCallDate, outcomeDisplay } from './format'
import { DEFAULT_SORT, filterByStatus, nextSort, sortItems, statusOptions } from './table'
import type { SortKey, SortState } from './table'
import type { CallHistoryItem } from './types'
import './CallHistory.css'

export interface CallHistoryProps {
  /** Calls to list, in any order. The table opens newest first. */
  items: readonly CallHistoryItem[]
  /** Called with the call's id when the user opens a row. Omit to render rows as plain text. */
  onOpen?: (callId: string) => void
}

const COLUMNS: ReadonlyArray<{ key: SortKey; label: string }> = [
  { key: 'who', label: 'Calling who' },
  { key: 'task', label: 'Task' },
  { key: 'status', label: 'Status' },
  { key: 'date', label: 'Date' },
]

const ALL_STATUSES = ''

/**
 * Call history table (PO-6): one row per past call with business, task, status and date.
 * Click a column header to sort; the dropdown filters by status.
 */
export function CallHistory({ items, onOpen }: CallHistoryProps) {
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT)
  const [statusFilter, setStatusFilter] = useState<string>(ALL_STATUSES)

  const options = statusOptions(items)
  // A filter whose status disappeared (data refreshed) falls back to showing everything.
  const activeFilter = options.includes(statusFilter) ? statusFilter : null
  const rows = sortItems(filterByStatus(items, activeFilter), sort)

  return (
    <section className="ch" aria-labelledby="ch-title">
      <div className="ch-head">
        <h2 id="ch-title" className="ch-title">
          Call history
        </h2>
        {items.length > 0 && (
          <label className="ch-filter">
            <span className="ch-filter-label">Status</span>
            <select value={activeFilter ?? ALL_STATUSES} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value={ALL_STATUSES}>All</option>
              {options.map((label) => (
                <option key={label} value={label}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {items.length === 0 ? (
        <p className="ch-empty">No calls yet. Your past calls will show up here.</p>
      ) : rows.length === 0 ? (
        <p className="ch-empty">No calls match this status.</p>
      ) : (
        <div className="ch-table-wrap">
          <table className="ch-table">
            <thead>
              <tr>
                {COLUMNS.map((column) => (
                  <th
                    key={column.key}
                    scope="col"
                    aria-sort={sort.key === column.key ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button type="button" className="ch-sort" onClick={() => setSort(nextSort(sort, column.key))}>
                      {column.label}
                      <span className="ch-sort-arrow" aria-hidden="true">
                        {sort.key === column.key ? (sort.direction === 'asc' ? '▲' : '▼') : ''}
                      </span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <CallHistoryRow key={item.id} item={item} onOpen={onOpen} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function CallHistoryRow({ item, onOpen }: { item: CallHistoryItem; onOpen?: (callId: string) => void }) {
  const outcome = outcomeDisplay(item)

  return (
    <tr className={onOpen ? 'ch-tr ch-tr-open' : 'ch-tr'} onClick={onOpen ? () => onOpen(item.id) : undefined}>
      <td className="ch-cell-who">
        {onOpen ? (
          // The button is the keyboard and screen reader way in; a click anywhere on the row also opens the call.
          <button
            type="button"
            className="ch-open"
            onClick={(event) => {
              event.stopPropagation()
              onOpen(item.id)
            }}
          >
            {item.businessName}
          </button>
        ) : (
          <b className="ch-business">{item.businessName}</b>
        )}
      </td>
      <td className="ch-cell-task">{item.goal}</td>
      <td>
        <span className={`ch-badge ch-badge-${outcome.tone}`}>{outcome.label}</span>
      </td>
      <td className="ch-cell-date">
        <time dateTime={item.createdAt}>{formatCallDate(item.createdAt)}</time>
      </td>
    </tr>
  )
}
