import { outcomeDisplay } from './format'
import type { CallHistoryItem } from './types'

/** The sortable columns of the call history table. */
export type SortKey = 'who' | 'task' | 'status' | 'date'

export type SortDirection = 'asc' | 'desc'

export interface SortState {
  key: SortKey
  direction: SortDirection
}

/** Newest call first, which is how the table opens. */
export const DEFAULT_SORT: SortState = { key: 'date', direction: 'desc' }

/**
 * The sort after the user clicks a column header.
 *
 * Clicking the active column flips its direction. Clicking another column
 * switches to it: dates start newest first, text columns start A to Z.
 */
export function nextSort(current: SortState, clicked: SortKey): SortState {
  if (current.key === clicked) {
    return { key: clicked, direction: current.direction === 'asc' ? 'desc' : 'asc' }
  }
  return { key: clicked, direction: clicked === 'date' ? 'desc' : 'asc' }
}

/** Milliseconds since 1970, or null when the date can't be read. */
function timeOf(iso: string): number | null {
  const time = Date.parse(iso)
  return Number.isNaN(time) ? null : time
}

/** Text of a call's status as shown in the table (the outcome badge label). */
export function statusLabel(item: Pick<CallHistoryItem, 'outcome' | 'endedAt'>): string {
  return outcomeDisplay(item).label
}

/** Distinct status labels present in `items`, A to Z, for the filter dropdown. */
export function statusOptions(items: readonly CallHistoryItem[]): string[] {
  return [...new Set(items.map(statusLabel))].sort((a, b) => a.localeCompare(b))
}

/** Calls whose status label equals `label`. A null label means no filter. Returns a new array. */
export function filterByStatus(items: readonly CallHistoryItem[], label: string | null): CallHistoryItem[] {
  if (label === null) return [...items]
  return items.filter((item) => statusLabel(item) === label)
}

function compareText(a: string, b: string): number {
  return a.localeCompare(b, 'en', { sensitivity: 'base' })
}

/** Compare by creation time; a call with an unreadable date is always last, in either direction. */
function compareDates(a: CallHistoryItem, b: CallHistoryItem, direction: SortDirection): number {
  const timeA = timeOf(a.createdAt)
  const timeB = timeOf(b.createdAt)
  if (timeA === null && timeB === null) return 0
  if (timeA === null) return 1
  if (timeB === null) return -1
  return direction === 'asc' ? timeA - timeB : timeB - timeA
}

function compareByKey(a: CallHistoryItem, b: CallHistoryItem, sort: SortState): number {
  const sign = sort.direction === 'asc' ? 1 : -1
  switch (sort.key) {
    case 'date':
      return compareDates(a, b, sort.direction)
    case 'who':
      return sign * compareText(a.businessName, b.businessName)
    case 'task':
      return sign * compareText(a.goal, b.goal)
    case 'status':
      return sign * compareText(statusLabel(a), statusLabel(b))
  }
}

/** Sorts a copy of `items`. Ties fall back to newest first so the order is stable. */
export function sortItems(items: readonly CallHistoryItem[], sort: SortState): CallHistoryItem[] {
  return [...items].sort((a, b) => {
    const result = compareByKey(a, b, sort)
    return result !== 0 ? result : compareDates(a, b, 'desc')
  })
}
