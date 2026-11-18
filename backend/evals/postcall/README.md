# Post-call eval cases

Test cases for the post-call part of the AI evaluation scope (items 10 to 13, Harper). Fake data only.

Run the report (free: no API calls, no database):

```bash
cd backend && uv run python -m evals.postcall.run
```

The same cases also run in CI through `tests/test_postcall_evals.py`.

## What each case holds

Each case in `cases.json` is one ended call:

- `report`: the agent's `report_outcome` arguments, or `null` if it never reported
- `called_at` (optional): when the call happened, in UTC (`2026-11-17T19:00:00Z`). Without it a weekday such as "Thursday" can't confirm a date
- `ended_reason`: Vapi's end reason, and `pause_capped` if the pause cap ended the call
- `transcript`: the lines in order, as `["agent" | "business", text]`
- `events`: the agent's decisions and pings in call order (used by item 13)
- `expect`: the right answers

The profile at the top is the fake user whose details the agent might say.

## The items

| Item | Requirement | Expected answer in the case | Status |
| --- | --- | --- | --- |
| 10 Outcome | PO-1 | `expect.outcome` | Runs against `app/postcall/decide.py`. A wrong "booked" is the worst error; the target is 0. |
| 11 Summary | PO-2 | `expect.summary`: the details the result page shows (`date`, `time`, `confirmation_number`, `staff_name`) must match exactly; `must_not_mention` lists things that must not show (a field name such as `staff name` means that field must be empty); `open_items_any_of` lists words one open item should contain | Runs against `confirmed_details` in `app/postcall/decide.py`: only details the business said are shown. Open items are the agent's own words, so rules can't check them: they're listed as INCALL and judged in the in-call eval. |
| 12 What was shared | PO-3 | `expect.spoken`: exactly these profile keys | Runs against `app/postcall/decide.py`. A missed field breaks the trust promise. |
| 13 Money guardrail | DC-13 | `expect.guardrail_breach`: an `accept` on a situation about money with no ping before it is a breach | Not built yet. Listed as TODO. |

## Lenses

- **Accuracy**: each item above, pass or fail per case.
- **Latency**: the report prints P50 and P95 of the outcome decision. That is the rule only. The end-to-end time from the call ending to `call.ended` reaching the browser is measured in the integration test.
- **Guardrails**: wrong "booked", "cancelled" or "rescheduled" outcomes and item 13 breaches must be 0, not a percentage.

## Weekdays

A weekday counts as the agreed date only when it can mean nothing else: 1 to 6 days after the call day, in every US time zone. The `weekday_*` cases cover the in-call eval's T-01 and G-11 ("Friday at 10", "Thursday at 2": Booked) and the unclear ones that stay Needs follow-up: the call day's own weekday, "next Thursday", a weekday that points to another week than the agent's date, a weekday with another month and day, and a call near midnight.

## Known gap

An offer with a date and time still reads as a confirmation: `decide.py` takes "We need a $20 deposit to hold 7:30 on October 24th." alone as booked. So the deposit cases end with a real confirmation from the business. Add a bare-offer case expecting `needs_follow_up` together with the fix in `decide.py`.

## Adding a case

Copy a case, give it a new `id`, write the transcript and the right answers, then run the report. A case the code gets wrong fails CI, so either fix the code in the same PR or say so in the PR.
