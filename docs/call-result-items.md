# Call result items (asked vs. got): proposal for the post-call owner

2026-11-24 · Status: proposal, no contract or backend change until agreed. Touches PO-1, PO-2 (`docs/requirements.md`).

**Goal:** let the result page show, for each thing the user asked for, what the business actually agreed to, as a table:

| | Asked | Got |
|---|---|---|
| Cake | Mochi cake | Marshmallow cake. Mochi isn't available. |
| Pickup | Wed by 10:00 AM | Wed 12:00 PM. 10 AM wasn't possible. |
| Ice pack | Included | +$3.00. They wouldn't waive it; the user approved. |
| Topper, candles, knife | Included | All included |

Design reference: `frontend/public/prototype/call-detail.html`. The frontend already ships the layout, the one-line decision rows and the merged transcript; the table is the one part it can't build from today's data.

## 1. What the page gets today, and why it isn't enough

`GET /calls/{call_id}/result` (`backend/app/postcall/history.py`) returns:

| Field | Shape | Problem for a table |
|---|---|---|
| `details` | `date`, `time`, `confirmation_number`, `staff_name` | Only for booked, cancelled or rescheduled, and only date and time. Nothing for a cake type or a fee. |
| `open_items` | `string[]` | Free text. No label, no "asked", no way to tell a changed item from a to-do. |
| `decisions` | `situation`, `action`, `reason`, `field_key?`, `missed_preference?` | Written as they happen, not as a final answer. They don't say which brief item they concern. Several decisions can describe one change. |
| `shared_fields` | `field_key`, `state` | Unrelated. |

The brief (`brief.v1`) has what was asked (`task_vars`, `constraints`, `preferences`, `fallbacks`), but nothing connects it to what happened.

## 2. Proposal: `result_items` on `report_outcome`

Add one optional array to the existing `report_outcome` tool args (`contracts/tools/report_outcome.args.v1.json`). It is additive and optional, so it stays `v1` and old calls keep working. The agent fills it once, at the end of the call, in the same place it already reports `open_items`.

```jsonc
"result_items": [
  {
    "ref":    { "kind": "preference", "index": 0 },   // which brief item this is about
    "label":  "Cake",                                  // 1-3 words, shown as the row name
    "got":    "Marshmallow cake",                      // what was agreed, plain words, max 80 chars
    "status": "changed",                               // as_asked | changed | not_met | extra
    "reason": "Mochi isn't available.",                // max 120 chars, shown under "got"
    "approved_by": "user",                             // brief | user | none
    "cost_cents": null,                                // integer, only for money
    "business_said": "We have all of those except mochi. Is marshmallow okay?"  // their words, copied
  }
],
"follow_up": "Call to confirm the cake is nut-free."   // optional, max 200 chars
```

The agent can't see transcript line numbers, so it quotes the business's own words in `business_said` (the same approach as `rule_evidence` in `report_decision`). The server finds that quote in the transcript and turns it into `evidence`, the line numbers, in the API response. The contract is in `contracts/tools/report_outcome.args.v1.json`.

### Field rules

| Field | Rule |
|---|---|
| `ref.kind` | `task_var` (`key`), `constraint`, `preference` or `fallback` (`index` into the brief's array), or `extra` for something the business added that the brief never mentioned (the ice pack fee). `extra` has no `index`. |
| `label` | Written by the agent. |
| `asked` | **Not written by the agent.** The API fills it from the stored brief using `ref`, so the "Asked" column is always the user's own words. `null` for `extra`. |
| `got` | Written by the agent: what was agreed, not what was offered. |
| `status` | `as_asked` (matches), `changed` (a different but accepted result), `not_met` (not agreed, and the user must act), `extra` (new cost or condition). The UI counts every status except `as_asked` as a "change". Open enum, like `outcome`: the UI shows an unknown value as `changed`. |
| `approved_by` | `brief`: the agent used a fallback or rule the user wrote. `user`: the user answered a ping. `none`: nobody approved it (should come with `not_met`). |
| `cost_cents` | Integer cents for a fee or price, otherwise `null`. Lets the page show "+$3.00" and a total without parsing text. |
| `business_said` | The business's own words supporting the row, max 200 chars. Optional in the tool call. The server matches it to transcript lines; that match is the verification below. |
| `evidence` | **API only, never written by the agent.** `seq` values of the transcript lines the server matched. Powers "click a row to jump to the line". |
| Limits | At most 12 items. `label` and `got` are required. |

### Checks on the server (the same idea as `shown_details`)

The model's summary is a claim, so the backend verifies what it can before the page shows it, and drops or downgrades what it can't:

1. **`business_said`** must be found in a business turn of this call's transcript (compared the way `rule_evidence` is). If it is, the matching `seq` values become `evidence`; if it isn't, `evidence` is empty. An item with no evidence left is unverified: shown marked if `as_asked`, otherwise hidden (see open question 2).
2. **`cost_cents`** must match an amount a business turn actually said. Otherwise it is dropped. This extends the existing money gate in `gates.py`.
3. **`status` against the brief's rules.** When the referenced item has `rules` (date and time rules in `brief-rule.v1`) and the agreed `details.date` or `details.time` break them, force `changed`, whatever the model said. In the example, "by 10 AM" is `latest: "10:00"` and the agreed time is 12:00, so the pickup row is `changed` even if the model had written `as_asked`.
4. **`approved_by: "user"`** requires a ping reply from the user in this call. Otherwise it becomes `none`.
5. Items for refused or ungated reports are skipped, the same way `latest_report` already skips a refused `report_outcome`.

### API response

`GET /calls/{call_id}/result` gets two new fields, both optional, and the existing ones stay exactly as they are:

```jsonc
"result_items": [
  { "label": "Cake", "asked": "Mochi cake", "got": "Marshmallow cake",
    "status": "changed", "reason": "Mochi isn't available.",
    "approved_by": "user", "cost_cents": null, "evidence": [4, 5] }
],
"follow_up": null
```

`follow_up` (string or null) says in one sentence what the user has to do, for example "Call to confirm the cake is nut-free." The `needs_follow_up` badge doesn't say this today, so users can't tell what the follow-up is. `null` means nothing is needed.

`open_items` stays for things the user must act on. `result_items` is what the call changed. Both can be shown.

## 3. The example call (coco resto, Nov 24)

```json
[
  { "label": "Cake", "asked": "Mochi cake", "got": "Marshmallow cake", "status": "changed",
    "reason": "Mochi isn't available.", "approved_by": "user", "cost_cents": null, "business_said": "…" },
  { "label": "Pickup", "asked": "Wed by 10:00 AM", "got": "Wed 12:00 PM", "status": "changed",
    "reason": "10 AM wasn't possible.", "approved_by": "brief", "cost_cents": null, "evidence": [11, 24] },
  { "label": "Ice pack", "asked": null, "got": "+$3.00", "status": "extra",
    "reason": "They wouldn't waive the fee.", "approved_by": "user", "cost_cents": 300, "evidence": [13, 15, 17] },
  { "label": "Topper, candles, knife", "asked": "Included", "got": "All included", "status": "as_asked",
    "reason": null, "approved_by": "brief", "cost_cents": null, "evidence": [5] }
]
```

The `evidence` numbers are illustrative; the server fills them from `business_said`. The UI headline "3 changes · +$3" comes from counting the non-`as_asked` rows and summing `cost_cents`.

## 4. Changes needed

| Where | Change |
|---|---|
| `contracts/tools/report_outcome.args.v1.json` | Add `result_items`. Add valid and invalid examples. Run `make -C contracts gen`. |
| `backend/app/calls/prompt.py` | Tell the agent to fill `result_items` when it reports the outcome, and to use `extra` for anything the brief didn't mention. |
| `backend/app/postcall/history.py` | Join each item to the brief for `asked`, run the checks above, return `result_items` and `follow_up`. Tests next to `tests/test_postcall_history.py`. |
| `backend/evals/postcall` | Add a check that `status` and `got` agree with the transcript. |
| `frontend/src/result/` | Add `items` and `followUp` to `CallResult`, render the table when `items` is non-empty, and fall back to the current layout for older calls. |
| `docs/requirements.md` | Extend PO-2 to name this. |

## 5. Open questions

1. **Who fills it: decided, the agent, inside `report_outcome`.** It is already the final, gated answer, so no separate post-call job is needed.
2. **Unverified items.** An item is unverified when, the server can't find the agent's `business_said` words in any business turn, so no transcript line backs it. Example: the agent reports

   ```json
   { "label": "Delivery", "got": "Free delivery", "status": "changed",
     "reason": "They waived the fee.", "approved_by": "brief", "business_said": "Delivery is on us." }
   ```

   but nobody mentioned delivery in the 47-line call, so the quote matches no line and `evidence` is `[]`. The page can then:

   | Option | What the user sees | Trade-off |
   |---|---|---|
   | Hide it | Nothing. The row is gone. | Safest: no claim without proof. A real change might be missing from the table. |
   | Show it, marked | `Delivery  →  Free delivery` with a gray "Not found in transcript" tag, no jump link | Complete, and the user can judge. A wrong claim is still on screen. |

   **Decided:** an unverified item with `status` `as_asked` is shown with the gray "Not found in transcript" tag. An unverified item with any other status (`changed`, `not_met`, `extra`) is hidden, because a false claim of a change, a fee or a waived cost costs the user more. The server applies this rule before sending `result_items`, so the frontend never receives a hidden item. Add a test for each status.
3. **Total cost.** Is a sum of `cost_cents` enough, or does the page need a currency? The product is US-only today, so USD is assumed.
4. **Separate data bug.** On the Nov 24 call the page said "Nothing was shared" although the agent said the name and phone number aloud. `shared_fields` seems not to record spoken disclosures. It is independent of this proposal and worth its own issue.
