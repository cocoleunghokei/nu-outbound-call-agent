import { describe, expect, it } from 'vitest'
import { speakerName, transcriptRows, type TranscriptLine } from './transcript'

const line = (seq: number, role: TranscriptLine['role'], text: string, startedAt: string | null): TranscriptLine => ({
  seq,
  role,
  text,
  startedAt,
})

describe('transcriptRows', () => {
  it('orders lines by seq, not by array order', () => {
    const rows = transcriptRows(
      [
        line(2, 'agent', 'Hi, calling for Alex.', '2026-11-09T18:00:03Z'),
        line(1, 'business', 'Juniper Ramen.', '2026-11-09T18:00:00Z'),
      ],
      'Juniper Ramen',
    )
    expect(rows.map((r) => r.seq)).toEqual([1, 2])
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
    expect(speakerName('operator' as TranscriptLine['role'], 'Shop')).toBe('Unknown')
  })
})
