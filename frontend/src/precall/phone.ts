/**
 * Phone number check for the number the agent will dial (PC-7).
 *
 * PRD rule: US numbers only, and never 911 or other emergency numbers.
 * `+1` is the whole North American Numbering Plan (NANP), so `+1` alone is
 * not enough: Canada and about 20 Caribbean countries share it. A number is
 * accepted only if its area code is a US one (US territories included).
 *
 * The backend runs the same rules again (plan Step 8), so keep this file the
 * single source of truth for the browser and mirror it on the server.
 */

/** Why a number was rejected. */
export type PhoneError =
  | 'empty'
  | 'emergency'
  | 'short_code'
  | 'not_us'
  | 'premium'
  | 'personal'
  | 'invalid'

export type PhoneCheck =
  | { ok: true; e164: string }
  | { ok: false; reason: PhoneError; message: string }

const MESSAGES: Record<PhoneError, string> = {
  empty: 'Enter the phone number to call.',
  emergency: "We can't call emergency numbers. For an emergency, dial 911 yourself.",
  short_code: "We can't call short service numbers like 311 or 411. Enter the business's full 10-digit number.",
  not_us: 'Only US phone numbers are supported for now.',
  premium: "We can't call premium-rate (900) numbers.",
  personal: "This looks like a personal or non-business number. We only call businesses.",
  invalid: "That doesn't look like a valid US phone number. Use 10 digits, like (617) 555-0123.",
}

/** Emergency numbers, checked on the raw input before anything else. 112 and 999 reach 911 on most US phones. */
const EMERGENCY_SHORT_CODES = new Set(['911', '112', '999'])

/**
 * NANP area codes that are not in the US. Canada first, then the Caribbean
 * and Bermuda. US territories (340, 670, 671, 684, 787, 939) stay allowed.
 * Source: NANPA area code list; check it when a new code goes live.
 */
const NON_US_AREA_CODES = new Set([
  // Canada (geographic and non-geographic 600, 622)
  '204', '226', '236', '249', '250', '257', '263', '289', '306', '343', '354',
  '365', '367', '368', '382', '387', '403', '416', '418', '428', '431', '437',
  '438', '450', '460', '468', '474', '506', '514', '519', '548', '579', '581',
  '584', '587', '600', '604', '613', '622', '639', '647', '672', '683', '705',
  '709', '742', '753', '778', '780', '782', '807', '819', '825', '867', '873',
  '879', '902', '905', '942',
  // Caribbean and Bermuda
  '242', '246', '264', '268', '284', '345', '441', '473', '649', '658', '664',
  '721', '758', '767', '784', '809', '829', '849', '868', '869', '876',
])

/** Premium-rate area code. */
const PREMIUM_AREA_CODES = new Set(['900'])

/**
 * Non-geographic codes for personal or special services: 5XX personal
 * communications and 700 carrier services, plus 710 (US government).
 * Calls to personal numbers are out of scope in the PRD.
 */
const PERSONAL_OR_SPECIAL_AREA_CODES = new Set([
  '500', '521', '522', '523', '524', '525', '526', '527', '528', '529', '532',
  '533', '535', '538', '542', '543', '544', '545', '546', '547', '549', '550',
  '552', '553', '554', '556', '558', '566', '569', '577', '578', '588', '589',
  '700', '710',
])

/** Characters people type in a phone number that we can ignore. */
const SEPARATORS = /[\s().\-‐-―]/g

function fail(reason: PhoneError): PhoneCheck {
  return { ok: false, reason, message: MESSAGES[reason] }
}

/** True for codes of the form N11 (211, 311, ... 911), which are never area codes or exchanges. */
function isN11(code: string): boolean {
  return /^[2-9]11$/.test(code)
}

/**
 * Check a phone number typed by the user and return it as E.164 (`+1XXXXXXXXXX`).
 *
 * Accepts `6175550123`, `(617) 555-0123`, `617.555.0123`, `1 617 555 0123`
 * and `+1 617 555 0123`. Rejects emergency and short service numbers,
 * non-US numbers (including Canada and the Caribbean), premium and personal
 * codes, and anything that isn't a valid NANP number.
 */
export function checkPhone(raw: string): PhoneCheck {
  const trimmed = raw.trim()
  if (trimmed === '') return fail('empty')

  const compact = trimmed.replace(SEPARATORS, '')

  // Short codes first: "911" has 3 digits and would never normalise to E.164.
  if (EMERGENCY_SHORT_CODES.has(compact)) return fail('emergency')
  if (isN11(compact)) return fail('short_code')

  let digits: string
  if (compact.startsWith('+')) {
    if (!/^\+\d+$/.test(compact)) return fail('invalid')
    if (!compact.startsWith('+1')) return fail('not_us')
    digits = compact.slice(2)
  } else if (compact.startsWith('011')) {
    // US international dialling prefix: a number in another country.
    return fail('not_us')
  } else {
    if (!/^\d+$/.test(compact)) return fail('invalid')
    digits = compact.length === 11 && compact.startsWith('1') ? compact.slice(1) : compact
  }

  if (digits.length !== 10) return fail('invalid')

  const areaCode = digits.slice(0, 3)
  const exchange = digits.slice(3, 6)

  // NANP shape: area code and exchange both start with 2-9 and are never N11.
  if (!/^[2-9]/.test(areaCode) || isN11(areaCode)) return fail('invalid')
  if (!/^[2-9]/.test(exchange) || isN11(exchange)) return fail('invalid')

  if (NON_US_AREA_CODES.has(areaCode)) return fail('not_us')
  if (PREMIUM_AREA_CODES.has(areaCode)) return fail('premium')
  if (PERSONAL_OR_SPECIAL_AREA_CODES.has(areaCode)) return fail('personal')

  return { ok: true, e164: `+1${digits}` }
}

/** Show an E.164 US number as `(617) 555-0123`. Returns the input unchanged if it isn't one. */
export function formatUsPhone(e164: string): string {
  const match = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164)
  return match ? `(${match[1]}) ${match[2]}-${match[3]}` : e164
}
