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
- `ended_reason`: Vapi's end reason, and `pause_capped` if the pause cap ended the call
- `transcript`: the lines in order, as `["agent" | "business", text]`
- `events`: the agent's decisions and pings in call order (used by item 13)
- `expect`: the right answers

The profile at the top is the fake user whose details the agent might say.

## The items

| Item | Requirement | Expected answer in the case | Status |
| --- | --- | --- | --- |
| 10 Outcome | PO-1 | `expect.outcome` | Runs against `app/postcall/decide.py`. A wrong "booked" is the worst error; the target is 0. |
| 11 Summary | PO-2 | `expect.summary`: `date`, `time`, `confirmation_number`, `staff_name` must match exactly; `open_items_any_of` lists words one open item should contain; `must_not_mention` lists things that would be made up | Not built yet (the summary job). Listed as TODO. |
| 12 What was shared | PO-3 | `expect.spoken`: exactly these profile keys | Runs against `app/postcall/decide.py`. A missed field breaks the trust promise. |
| 13 Money guardrail | DC-13 | `expect.guardrail_breach`: an `accept` on a situation about money with no ping before it is a breach | Not built yet. Listed as TODO. |

## Lenses

- **Accuracy**: each item above, pass or fail per case.
- **Latency**: the report prints P50 and P95 of the outcome decision. That is the rule only. The end-to-end time from the call ending to `call.ended` reaching the browser is measured in the integration test.
- **Guardrails**: wrong "booked" outcomes and item 13 breaches must be 0, not a percentage.

## Adding a case

Copy a case, give it a new `id`, write the transcript and the right answers, then run the report. A case the code gets wrong fails CI, so either fix the code in the same PR or say so in the PR.
