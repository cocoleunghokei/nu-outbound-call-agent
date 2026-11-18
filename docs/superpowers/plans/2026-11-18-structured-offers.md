# Structured Offers Plan: the model reports what it heard, Python decides if it fits

> **Status:** PR 1 (contracts) in progress on `claude/structured-offers`, stacked on `claude/sweet-mccarthy-xekz3h`. Owner and final reviewer: Coco, including the pre-call parts (brief format, review card).

## Why

The in-call eval (`backend/evals/incall/`) has run eight times on `deepseek-chat`. Prompt fixes and gates removed most failures. One class keeps coming back, because it asks the model to **compare**, which a fast, cheap model does unreliably:

| Case | The business said | The model decided | Correct |
| --- | --- | --- | --- |
| C4-05 | "Thursday at 3pm exactly" | "before 3pm… only if inclusive", so accept | 3pm is not before 3pm: counter, then ping |
| C4-04 | "Friday at 2pm?" | Matches the fallback "Friday morning (before noon)" | 2pm is not morning: counter, then ping |
| C4-06 | "Thursday 2pm, but with Dr. Patel" | Counter, or ping | Dr. Rivera is a typed preference: accept, flag it |

Today the gates in `backend/app/calls/gates.py` can't catch these. They read the model's own `dimensions` (`meets_constraints`, `matches_fallback`), so when the model judges wrong, the gate agrees with it.

**The idea:** the model only writes down the offer it heard (`date: 2026-11-19, time: 15:00`). Python compares that against rules stored as data (`latest: 14:59`) and decides whether it fits. Writing down "3pm" is easy even for a small model; comparing is where it slips.

## Today vs. this plan

| | Today | With this plan |
| --- | --- | --- |
| Brief rules | Text only: `{"text": "Before 3pm"}` | Text plus optional data: `{"text": "Before 3pm", "rules": [{"kind": "time", "latest": "14:59"}]}` |
| Tool call carries | The model's verdict (`meets_constraints: true`) | The offer heard (`offer: {date, time, party_size, person}`), plus the verdict, kept for debugging |
| Who decides fits / breaks | The model | Python, whenever the rule and the offer are both structured |
| Gates check | The model's self-report | The real offer against the real rules |
| Rules Python can't read ("a quiet table") | Model | Model, unchanged |
| Pause on the phone | — | Unchanged: the comparison is local, under 1 ms, with no extra model call |

## Design

### 1. Rules on brief items (contract: `contracts/brief.v1.json`)

Each `constraints`, `preferences` and `fallbacks` item gets an optional `rules` array. **All** rules in one item must hold for the item to match. An item without `rules` stays text-only and is judged by the model as today, so old briefs keep working.

```json
{ "text": "Saturday 21 November, between 6:30pm and 8pm",
  "rules": [ { "kind": "date", "on": "2026-11-21" },
             { "kind": "time", "earliest": "18:30", "latest": "20:00" } ] }
```

Rule kinds (first version):

| Kind | Fields | Example text |
| --- | --- | --- |
| `date` | `on`, or `earliest` / `latest` (YYYY-MM-DD, inclusive) | "On Thursday 19 November" |
| `time` | `earliest` and/or `latest` (HH:MM, 24-hour, inclusive) | "Before 3pm" → `latest: 14:59`; "morning (before noon)" → `latest: 11:59` |
| `party_size` | `exactly`, or `min` / `max` | "Party of 6" |
| `person` | `name` | "With Dr. Rivera" |

Money stays a gate on its own (`money_gate`), so there's no price rule in the first version. The schema is `contracts/brief-rule.v1.json`. "At least one bound" is not in the schema, because `anyOf` makes the generated Python and TypeScript types unreadable. A rule with no bound counts as **unknown** in scoring instead.

### 2. The offer on tool calls (contracts: `contracts/tools/report_decision.args.v1.json`, `ask_user.args.v1.json`)

An optional `offer` object holds only what the business said, normalised. Tool schemas go to the model exactly as written (`load_schema` doesn't resolve `$ref`), so `offer` is written out in full in both files, and a test keeps the two copies identical:

```json
"offer": { "date": "2026-11-19", "time": "15:00", "party_size": 6, "person": "Dr. Rivera" }
```

The model fills a field only when the business said it, or when it's clear from the call ("Thursday" during a call about 19 November). A field it isn't sure of stays out, and that rule counts as **unknown**. `dimensions` stays in the contract and keeps being logged, so we can measure how often the model and Python disagree.

### 3. Scoring in Python (new: `backend/app/calls/scoring.py`)

A pure function of `(brief rules, offer) -> verdict`, with no database and no network:

- For each structured constraint, preference and fallback: **pass**, **fail** or **unknown** (the offer doesn't say, or the item has no rules).
- Any hard constraint **fails** → it fits only if every rule of some fallback **passes** (High, `take_fallback`); otherwise Low.
- Every constraint **passes** → High, or Medium if a structured preference **fails** (that preference becomes `missed_preference`).
- Anything **unknown** → Python has no opinion, and the model's own `dimensions` decide, exactly as today.

### 4. Gates use the verdict (`backend/app/calls/gates.py`, `decisions.py`, `ask.py`)

| Action | Refused when Python says |
| --- | --- |
| `report_decision accept` | A constraint fails and no fallback passes, and the user didn't say yes (replaces the `dimensions` check in `low_offer_gate`) |
| `report_decision take_fallback` | The named fallback doesn't pass (catches C4-04) |
| `ask_user` about the offer | Every constraint passes, with no money and no ask-first field (extends `needless_ping_gate`) |

The refusal names the reason, for example "Not done: 15:00 is after the latest time the user accepts (14:59). Counter once, or call ask_user." Gate texts are then no longer fixed strings: the eval's `refused_calls` must match refusals by a shared prefix ("Not done:"), not by exact text.

The eval runner (`backend/evals/incall/play.py`) calls the same functions, as it does for today's gates.

### 5. The prompt (`backend/app/calls/prompt.py`)

- Checks 3 and 4 in `<deciding>` change from "does it meet / match" to "fill `offer` with exactly what they said; the system checks it against the rules and tells you if an action doesn't fit".
- The three tiers stay, for unstructured rules.
- The prompt gets shorter, and tool labels in `backend/app/voice/tools.py` must stay in step with it.

### 6. Getting rules into the brief (pre-call: `backend/app/briefs/`, `frontend/src/precall/`)

- **Deterministic parser first** (new `backend/app/briefs/rules.py`). It covers the common shapes: dates ("Thursday 19 November", against the call's date), times ("before 3pm", "after 6", "between 6:30pm and 8pm", "morning", "afternoon"), party size, and "with Dr. X". `app/postcall/decide.py` already parses times and dates and can be reused.
- **Model fallback later**, for text the parser can't read. It follows the `app/briefs/llm.py` pattern (JSON mode, strict validation). If validation fails, the item stays text-only. A brief is never blocked by a rule it can't parse.
- **The review card** (`frontend/src/precall/ReviewCard.tsx`) keeps its tags as they are. Under each tag, a small grey line shows how it will be checked (decided 2026-11-18). To fix a misread, the user edits the text, which is parsed again. The parsed line itself is read-only.

  ```
  Must     ( Before 3pm )
             ✓ Latest start 2:59pm
           ( A quiet table, not near the kitchen )
             ~ I'll judge this one myself
  ```

## Delivery (one PR each, in this order)

The during-call work is tested first on hand-written rules in the eval briefs, so we can measure the gain before touching pre-call.

1. **`feat(contracts)`: rules and offer.** Optional `rules` on brief items, optional `offer` on `report_decision` and `ask_user`, then `make -C contracts gen`. Backward compatible: no existing brief or call changes.
2. **`feat(backend)`: scoring and gates.** `scoring.py` with unit tests for every boundary (15:00 vs `latest 14:59`, 11:59 vs 12:00, a missing field counts as unknown). The gates use it, and the eval runner mirrors it.
3. **`feat(backend)`: prompt and eval.**
   - The prompt asks for `offer`.
   - Eval briefs A and B in `cases.json` get hand-written `rules`.
   - New check: `offer` matches what the business said.
   - New boundary cases: exactly 3pm, "half past two", "noon", "Friday" said without a date.
   - Success means C4-04 and C4-05 have no Hard failures in 3 runs, nothing else regresses, and latency is unchanged.
4. **`feat(backend, frontend)`: rules in real briefs** (with Lotto). The parser in `app/briefs/rules.py` runs in the builder, and the review card shows the parsed rules. Live calls only gain from this PR on, because before it real briefs have no `rules`, so behaviour is unchanged.
5. **Later:** a model fallback for text the parser can't read, and PC-9 (calendar). Free calendar slots map straight onto `time` and `date` rules.

## Alternatives considered

| Option | Why not (for now) |
| --- | --- |
| A `check_offer` tool the model calls before deciding | One more model round trip on every decision turn, about 1–2 s of extra silence on the phone |
| A second "decider" model, or a router | Same latency cost, double the model cost, and a second prompt to keep in step |
| A stronger model | Possible, and worth measuring with `--model`, but it doesn't make the comparisons exact. Code does |
| More prompt wording | Tried for eight runs. The comparison errors stayed |

## Risks

- **The model mishears or mis-normalises the offer** ("half two" written as 02:30). Boundary eval cases measure this, and any "unknown" field falls back to today's behaviour.
- **The parser misreads a rule.** The review card shows it, and a rule it can't parse stays text-only.
- **Relative dates** ("tomorrow", "next Friday"). They're resolved against the call's local date, which the eval already pins (`today` in `cases.json`).
- **Two sources of truth.** If the model's `dimensions` and Python disagree, Python wins for structured rules. Both are logged in `agent_scores`, so disagreements can be counted.

## Open questions

1. ~~Does the user see the parsed rules?~~ **Decided:** yes, as a grey line under each tag, read-only (see section 6).
2. ~~Lotto's agreement on the brief format?~~ **Decided:** Coco is the final reviewer for this work.
3. Does the parser run on the server only, with the review card showing the server's result, or in both places as the builder does today?
4. DC-4 in `docs/requirements.md` (Notion is the master copy) describes the model scoring every offer. It needs a line saying structured rules are scored in code.
