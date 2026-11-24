/**
 * Helpers for the template form: which input a template field gets, how a picked
 * date or time becomes the words the agent says, and the one-tap suggestions for
 * the three lists. Pure functions, so the form's rules are unit-tested without React.
 *
 * Templates (`templates.json`) only say what each field is called and which profile
 * or call it comes from. How a field is entered is decided here, by key, so a new
 * template needs no change to the template format. A key that is not listed below
 * is a plain text box.
 */

export type FieldWidget = 'stepper' | 'date' | 'time' | 'text'

const WIDGETS: Record<string, FieldWidget> = {
  party_size: 'stepper',
  day: 'date',
  time: 'time',
}

/** The input a template field is entered with. */
export function widgetFor(key: string): FieldWidget {
  return WIDGETS[key] ?? 'text'
}

export const STEPPER_DEFAULT = '2'
export const STEPPER_MIN = 1
export const STEPPER_MAX = 20

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

/**
 * The words the agent says for what the user picked or typed.
 * A date input (`2026-12-23`) becomes "December 23", a time input (`19:00`) becomes "7 PM"
 * and `19:30` becomes "7:30 PM". An invalid date or time becomes an empty string, which
 * counts as not answered.
 */
export function formatAnswer(widget: FieldWidget, raw: string): string {
  const value = raw.trim()
  if (widget === 'date') {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
    if (!m) return ''
    const month = Number(m[2])
    const day = Number(m[3])
    if (month < 1 || month > 12 || day < 1 || day > 31) return ''
    return `${MONTHS[month - 1]} ${day}`
  }
  if (widget === 'time') {
    const m = /^(\d{2}):(\d{2})$/.exec(value)
    if (!m) return ''
    const hour = Number(m[1])
    const minute = m[2]
    if (hour > 23 || Number(minute) > 59) return ''
    const suffix = hour >= 12 ? 'PM' : 'AM'
    const hour12 = hour % 12 || 12
    return minute === '00' ? `${hour12} ${suffix}` : `${hour12}:${minute} ${suffix}`
  }
  return value
}

/**
 * The reverse of `formatAnswer`, to refill an input when the user edits a plan.
 * "December 23" becomes `2026-12-23` (in `year`) and "7:30 PM" becomes `19:30`. Text it
 * cannot read gives an empty string, so the input shows empty and the user picks again.
 */
export function parseAnswer(widget: FieldWidget, text: string, year: number = new Date().getFullYear()): string {
  const value = text.trim()
  if (widget === 'date') {
    const m = /^([A-Za-z]+) (\d{1,2})$/.exec(value)
    if (!m) return ''
    const month = MONTHS.findIndex((name) => name.toLowerCase() === m[1].toLowerCase()) + 1
    const day = Number(m[2])
    if (month === 0 || day < 1 || day > 31) return ''
    return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
  }
  if (widget === 'time') {
    const m = /^(\d{1,2})(?::(\d{2}))? (AM|PM)$/.exec(value)
    if (!m) return ''
    const hour12 = Number(m[1])
    if (hour12 < 1 || hour12 > 12) return ''
    const hour = (hour12 % 12) + (m[3] === 'PM' ? 12 : 0)
    return `${String(hour).padStart(2, '0')}:${m[2] ?? '00'}`
  }
  return value
}

/** One-tap suggestions for the three lists, for a template. */
export interface ListSuggestions {
  constraints: string[]
  preferences: string[]
  fallbacks: string[]
}

const NO_SUGGESTIONS: ListSuggestions = { constraints: [], preferences: [], fallbacks: [] }

const SUGGESTIONS: Record<string, ListSuggestions> = {
  restaurant_booking: {
    constraints: ['Vegetarian options', 'Wheelchair accessible', 'Allergy-safe menu'],
    preferences: ['Window seat', 'Quiet area', 'Outdoor seating'],
    fallbacks: ['Within an hour of that time', 'Bar seating', 'Another day'],
  },
  doctor_booking: {
    constraints: ['Before 3pm', 'In person, not video', 'Not on Monday'],
    preferences: ['Morning appointment', 'Close to a bus stop'],
    fallbacks: ['Any morning that week', 'A different doctor at the same clinic', 'Next week'],
  },
}

export function suggestionsFor(templateKey: string): ListSuggestions {
  return SUGGESTIONS[templateKey] ?? NO_SUGGESTIONS
}
