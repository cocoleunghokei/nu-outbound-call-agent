"""Server-side check of the number the agent will dial (PC-7).

Mirrors ``frontend/src/precall/phone.ts`` rule for rule, so the browser and the server
agree. PRD rule: US numbers only, and never 911 or other emergency numbers. ``+1`` is the
whole North American Numbering Plan, so a number is accepted only with a US area code
(US territories included). Coco's ``POST /calls`` runs this again before dialing.
"""

import re
from dataclasses import dataclass
from typing import Literal

PhoneError = Literal["empty", "emergency", "short_code", "not_us", "premium", "personal", "invalid"]

MESSAGES: dict[PhoneError, str] = {
    "empty": "Enter the phone number to call.",
    "emergency": "We can't call emergency numbers. For an emergency, dial 911 yourself.",
    "short_code": (
        "We can't call short service numbers like 311 or 411. "
        "Enter the business's full 10-digit number."
    ),
    "not_us": "Only US phone numbers are supported for now.",
    "premium": "We can't call premium-rate (900) numbers.",
    "personal": "This looks like a personal or non-business number. We only call businesses.",
    "invalid": (
        "That doesn't look like a valid US phone number. Use 10 digits, like (617) 555-0123."
    ),
}

# Emergency numbers, checked on the raw input first. 112 and 999 reach 911 on most US phones.
EMERGENCY_SHORT_CODES = frozenset({"911", "112", "999"})

# NANP area codes outside the US: Canada (incl. non-geographic 600, 622), then the Caribbean
# and Bermuda. US territories (340, 670, 671, 684, 787, 939) stay allowed.
NON_US_AREA_CODES = frozenset(
    """
    204 226 236 249 250 257 263 289 306 343 354 365 367 368 382 387 403 416 418 428 431 437
    438 450 460 468 474 506 514 519 548 579 581 584 587 600 604 613 622 639 647 672 683 705
    709 742 753 778 780 782 807 819 825 867 873 879 902 905 942
    242 246 264 268 284 345 441 473 649 658 664 721 758 767 784 809 829 849 868 869 876
    """.split()
)

PREMIUM_AREA_CODES = frozenset({"900"})

# Non-geographic personal or special services: 5XX personal communications, 700 carrier
# services, 710 US government. Calls to personal numbers are out of scope in the PRD.
PERSONAL_OR_SPECIAL_AREA_CODES = frozenset(
    """
    500 521 522 523 524 525 526 527 528 529 532 533 535 538 542 543 544 545 546 547 549 550
    552 553 554 556 558 566 569 577 578 588 589 700 710
    """.split()
)

_SEPARATORS = re.compile(r"[\s().\-‐-―]")
_N11 = re.compile(r"^[2-9]11$")


@dataclass(frozen=True)
class PhoneCheck:
    """The result of ``check_phone``: ``e164`` when accepted, else a reason and a message."""

    e164: str | None
    reason: PhoneError | None = None

    @property
    def ok(self) -> bool:
        """True when the number can be dialed."""
        return self.e164 is not None

    @property
    def message(self) -> str:
        """A plain sentence for the user; empty when the number is fine."""
        return MESSAGES[self.reason] if self.reason else ""


def _fail(reason: PhoneError) -> PhoneCheck:
    return PhoneCheck(e164=None, reason=reason)


def check_phone(raw: str) -> PhoneCheck:
    """Check a number as typed (or an E.164 string) and return it as ``+1XXXXXXXXXX``."""
    trimmed = raw.strip()
    if not trimmed:
        return _fail("empty")
    compact = _SEPARATORS.sub("", trimmed)

    # Short codes first: "911" has 3 digits and would never normalise to E.164.
    if compact in EMERGENCY_SHORT_CODES:
        return _fail("emergency")
    if _N11.match(compact):
        return _fail("short_code")

    if compact.startswith("+"):
        if not re.fullmatch(r"\+\d+", compact):
            return _fail("invalid")
        if not compact.startswith("+1"):
            return _fail("not_us")
        digits = compact[2:]
    elif compact.startswith("011"):
        # US international dialling prefix: a number in another country.
        return _fail("not_us")
    else:
        if not compact.isdigit():
            return _fail("invalid")
        digits = compact[1:] if len(compact) == 11 and compact.startswith("1") else compact

    if len(digits) != 10:
        return _fail("invalid")
    area_code, exchange = digits[:3], digits[3:6]
    # NANP shape: area code and exchange both start with 2-9 and are never N11.
    if area_code[0] in "01" or _N11.match(area_code):
        return _fail("invalid")
    if exchange[0] in "01" or _N11.match(exchange):
        return _fail("invalid")
    if area_code in NON_US_AREA_CODES:
        return _fail("not_us")
    if area_code in PREMIUM_AREA_CODES:
        return _fail("premium")
    if area_code in PERSONAL_OR_SPECIAL_AREA_CODES:
        return _fail("personal")
    return PhoneCheck(e164=f"+1{digits}")
