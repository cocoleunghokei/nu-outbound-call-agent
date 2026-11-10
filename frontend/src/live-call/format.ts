/** Small display helpers for the live-call screen. */

/** Formats a duration as mm:ss, e.g. 75 000 ms → "01:15". Negative values show as 00:00. */
export function formatClock(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

/** Up to two initials for the business avatar, e.g. "Brookline Family Medicine" → "BF". */
export function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0].toUpperCase())
  return letters.join('') || '?'
}

/** Shows a US E.164 number as (617) 555-0142; other numbers are shown as given. */
export function formatPhone(e164: string): string {
  const us = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164)
  return us ? `(${us[1]}) ${us[2]}-${us[3]}` : e164
}
