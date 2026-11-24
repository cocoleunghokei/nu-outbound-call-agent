import { describe, expect, it } from 'vitest'
import type { CallHistoryItem } from './types'
import { DEFAULT_SORT, filterByStatus, nextSort, sortItems, statusOptions } from './table'

function call(id: string, overrides: Partial<CallHistoryItem>): CallHistoryItem {
  return {
    id,
    businessName: 'Test Cafe',
    phoneE164: '+16175550100',
    goal: 'Book a table',
    templateKey: null,
    status: 'ended',
    outcome: 'booked',
    createdAt: '2026-10-20T10:00:00Z',
    endedAt: '2026-10-20T10:02:00Z',
    ...overrides,
  }
}

const calls: CallHistoryItem[] = [
  call('a', { businessName: 'Juniper Ramen', goal: 'Book for 2', outcome: 'booked', createdAt: '2026-10-22T12:00:00Z' }),
  call('b', { businessName: 'back bay dental', goal: 'Cleaning', outcome: 'refused_ai', createdAt: '2026-10-20T10:00:00Z' }),
  call('c', { businessName: 'Tatte', goal: 'Hold a table', outcome: 'needs_follow_up', createdAt: '2026-10-26T18:00:00Z' }),
]

const ids = (items: readonly CallHistoryItem[]) => items.map((item) => item.id)

describe('nextSort', () => {
  it('flips direction when the active column is clicked again', () => {
    expect(nextSort(DEFAULT_SORT, 'date')).toEqual({ key: 'date', direction: 'asc' })
    expect(nextSort({ key: 'date', direction: 'asc' }, 'date')).toEqual({ key: 'date', direction: 'desc' })
  })

  it('starts dates newest first and text columns A to Z', () => {
    expect(nextSort({ key: 'who', direction: 'desc' }, 'date')).toEqual({ key: 'date', direction: 'desc' })
    expect(nextSort(DEFAULT_SORT, 'who')).toEqual({ key: 'who', direction: 'asc' })
  })
})

describe('sortItems', () => {
  it('sorts newest first by default', () => {
    expect(ids(sortItems(calls, DEFAULT_SORT))).toEqual(['c', 'a', 'b'])
  })

  it('sorts oldest first', () => {
    expect(ids(sortItems(calls, { key: 'date', direction: 'asc' }))).toEqual(['b', 'a', 'c'])
  })

  it('sorts by business name ignoring case', () => {
    expect(ids(sortItems(calls, { key: 'who', direction: 'asc' }))).toEqual(['b', 'a', 'c'])
    expect(ids(sortItems(calls, { key: 'who', direction: 'desc' }))).toEqual(['c', 'a', 'b'])
  })

  it('sorts by task text', () => {
    expect(ids(sortItems(calls, { key: 'task', direction: 'asc' }))).toEqual(['a', 'b', 'c'])
  })

  it('sorts by the status label shown in the table', () => {
    // Labels: Booked, Failed: Refused AI, Needs follow-up
    expect(ids(sortItems(calls, { key: 'status', direction: 'asc' }))).toEqual(['a', 'b', 'c'])
  })

  it('breaks ties newest first', () => {
    const same = [
      call('old', { businessName: 'Same', createdAt: '2026-10-01T00:00:00Z' }),
      call('new', { businessName: 'Same', createdAt: '2026-10-02T00:00:00Z' }),
    ]
    expect(ids(sortItems(same, { key: 'who', direction: 'asc' }))).toEqual(['new', 'old'])
  })

  it('keeps a call with an unreadable date last in both directions', () => {
    const withBad = [...calls, call('bad', { createdAt: 'not a date' })]
    expect(ids(sortItems(withBad, { key: 'date', direction: 'desc' }))[3]).toBe('bad')
    expect(ids(sortItems(withBad, { key: 'date', direction: 'asc' }))[3]).toBe('bad')
  })

  it('does not change the input array', () => {
    const before = ids(calls)
    sortItems(calls, { key: 'who', direction: 'desc' })
    expect(ids(calls)).toEqual(before)
  })
})

describe('statusOptions and filterByStatus', () => {
  it('lists each status label once, A to Z', () => {
    const more = [...calls, call('d', { outcome: 'booked' })]
    expect(statusOptions(more)).toEqual(['Booked', 'Failed: Refused AI', 'Needs follow-up'])
  })

  it('includes In progress for a running call', () => {
    const running = call('r', { outcome: null, endedAt: null })
    expect(statusOptions([running])).toEqual(['In progress'])
  })

  it('keeps only the chosen status', () => {
    expect(ids(filterByStatus(calls, 'Needs follow-up'))).toEqual(['c'])
  })

  it('returns every call when no status is chosen', () => {
    expect(ids(filterByStatus(calls, null))).toEqual(['a', 'b', 'c'])
  })
})
