import { describe, expect, it } from 'vitest'
import { speakerName, transcriptRows, type TranscriptLine } from './transcript'

const line = (seq: number, role: TranscriptLine['role'], text: string, startedAt: string | null): TranscriptLine => ({
  seq,
  role,
  text,
  startedAt,
})

describe('transcriptRows', () => {
  it('keeps the spoken order from the server, even when arrival order (seq) differs', () => {
    // The question was spoken first but its webhook arrived second (seq 2).
    const rows = transcriptRows(
      [
        line(2, 'agent', 'Do you have a table for 2?', '2026-11-09T18:00:01Z'),
        line(1, 'business', 'Yes, we have a table.', '2026-11-09T18:00:09Z'),
      ],
      'Juniper Ramen',
    )
    expect(rows.map((r) => r.text)).toEqual(['Do you have a table for 2?', 'Yes, we have a table.'])
    expect(rows.map((r) => r.clock)).toEqual(['00:00', '00:08'])
  })

  it('shows each line’s time since the first timed line', () => {
    const rows = transcriptRows(
      [
        line(1, 'business', 'Hello.', '2026-11-09T18:00:00Z'),
        line(2, 'agent', 'Hi.', '2026-11-09T18:01:15Z'),
      ],
      'Shop',
    )
    expect(rows.map((r) => r.clock)).toEqual(['00:00', '01:15'])
  })

  it('leaves the clock empty for lines with no or unreadable time', () => {
    const rows = transcriptRows(
      [
        line(1, 'business', 'Hello.', null),
        line(2, 'agent', 'Hi.', '2026-11-09T18:00:05Z'),
        line(3, 'business', 'Sure.', 'not a time'),
      ],
      'Shop',
    )
    expect(rows.map((r) => r.clock)).toEqual([null, '00:00', null])
  })

  it('has no clocks at all when no line is timed', () => {
    const rows = transcriptRows([line(1, 'business', 'Hello.', null)], 'Shop')
    expect(rows[0].clock).toBeNull()
  })

  it('drops blank lines and trims text', () => {
    const rows = transcriptRows(
      [line(1, 'business', '   ', null), line(2, 'agent', '  Hi there.  ', null)],
      'Shop',
    )
    expect(rows).toHaveLength(1)
    expect(rows[0].text).toBe('Hi there.')
  })

  it('returns an empty list for an empty transcript', () => {
    expect(transcriptRows([], 'Shop')).toEqual([])
  })

  it('does not change the input array', () => {
    const input = [line(2, 'agent', 'b', null), line(1, 'business', 'a', null)]
    transcriptRows(input, 'Shop')
    expect(input.map((l) => l.seq)).toEqual([2, 1])
  })
})

describe('speakerName', () => {
  it('names each known role', () => {
    expect(speakerName('agent', 'Juniper Ramen')).toBe('Assistant')
    expect(speakerName('business', 'Juniper Ramen')).toBe('Juniper Ramen')
    expect(speakerName('user', 'Juniper Ramen')).toBe('You')
  })

  it('falls back to "Business" when the business name is blank', () => {
    expect(speakerName('business', '  ')).toBe('Business')
  })

  it('shows "Unknown" for a role this version does not know', () => {
    expect(speakerName('unknown', 'Shop')).toBe('Unknown')
    expect(speakerName('operator' as TranscriptLine['role'], 'Shop')).toBe('Unknown')
  })
})
