# Call template format (PC-2): proposal for Harper

Lotto · 2026-11-10 · Plan Step 7 · Status: proposal for Harper to agree. No template code changes until she does.

**Goal:** lock one template shape that the pre-call chat (PC-1, PC-4) and the server brief builder (Step 9) both read, so Harper can add or edit templates as config, without touching pre-call code. PRD PC-2: "Templates are config, not code."

Sources checked: `frontend/precall-review` (`precall/types.ts`, `fakeBriefs.ts`, `missing.ts`, `chat.ts`, `brief.ts`), PR #19 (`contracts/brief.v1.json`, `backend/README.md`, `supabase/migrations/20261107171217_briefs.sql`, `backend/app/voice/prompt_safety.py`), `docs/requirements.md`, `docs/tech-doc-during-call.md`, Harper's prototype (`TPLS`, `makeBrief`) and PR #21 (`frontend/call-history`).

---

## 1. The format

Everything the current code already reads stays exactly as it is. One optional field is new (marked).

```ts
/** One call template (PC-2). */
export interface CallTemplate {
  /** Stable id. Saved in briefs.template_key, so never renamed or reused. */
  key: string
  /** Chip text on the chat home. Also becomes the brief's `goal` for template calls. */
  label: string
  /** Becomes the brief's task_line. {placeholders} are required field keys. Max 120 chars. */
  task_line: string
  /** What the call needs. The chat asks only for those still missing (PC-4), in this order. */
  required_fields: TemplateField[]
  /** What the business will likely ask. Data only today (see open question 3). */
  likely_questions: string[]
  /** Pre-filled into the user's hard limits (PC-5). Same {text} shape as brief.v1. */
  default_constraints: { text: string }[]
}

export interface TemplateField {
  key: string
  /** 'profile': saved on the user's profile, asked once ever. 'call': asked for this call only. */
  source: 'profile' | 'call'
  /** What the chat asks when the value is missing. */
  question: string
  /** PROPOSED ADDITION. One-tap answers shown as chips. Default none. */
  suggestions?: string[]
}
```

### Proposed addition, and why

| Field | Why | Cost |
|---|---|---|
| `TemplateField.suggestions?` | Harper's prototype already gives every question chips (`['2','4','6']`, `['Existing','New']`). Today the chat has chips for PC-5 lists only (`LIST_PROMPTS.*.suggestions` in `chat.ts`), not for template questions. Fewer typed answers means faster setup. | UI only. The backend ignores it. Chat change: about 5 lines in `waitForTemplateField`. |

**Not proposed:**
- **An `enabled` flag.** A template that isn't ready simply isn't added to the template list yet. Retiring one means removing it from the list, never reusing its key (R1), because old briefs and Harper's history keep `template_key`.
- **A version field.** A brief copies `task_line` and `task_vars` in full (brief.v1), so an old brief never depends on a later template edit. If a template's meaning changes, give it a new key instead.
- **A P0/P1 field.** That's a planning label, not something the app does anything with.

### JSON example (with the addition)

```json
{
  "key": "restaurant_booking",
  "label": "Restaurant: book a table",
  "task_line": "I'd like to book a table for {party_size} on {day} at {time}.",
  "required_fields": [
    { "key": "calling_name", "source": "profile", "question": "What name should the booking be under?" },
    { "key": "party_size", "source": "call", "question": "How many people?", "suggestions": ["2", "4", "6"] }
  ],
  "likely_questions": ["Name for the reservation?"],
  "default_constraints": []
}
```

---

## 2. Rules every template must follow

These come from the code and contracts. A validation test will check every one (see section 7).

| # | Rule | Source |
|---|---|---|
| R1 | `key` is snake_case (`^[a-z][a-z0-9_]*$`), unique, and **never renamed or reused**. A changed meaning gets a new key. | `briefs.template_key` (migration), `Brief.template_key`, PR #21 history |
| R2 | `label` is not empty and is short (aim for 40 chars or less). It's the chip, the user's chat line and the brief `goal`. | `chat.ts` `pick_template` sets `goal: template.label`; brief.v1 `goal` minLength 1 |
| R3 | `task_line` is **120 chars or less**, counted with the `{placeholders}` still in it. Aim for 15 words or less, like the DC-1 lines. | brief.v1 `task_line.maxLength: 120`; `fakeBriefs.test.ts` `TASK_LINE_MAX`; tech doc DC-1 |
| R4 | Every `{placeholder}` (`\{(\w+)\}`) is the `key` of one of the template's `required_fields`. | `brief.ts` `buildBrief`: otherwise it lands in `missing_fields` and blocks Start |
| R5 | A placeholder is **never an Ask-me-first field** (`date_of_birth`, `address`, insurance). The only profile key allowed in a placeholder is `calling_name`, which the DB forces to Share freely. Any other profile field could be switched to Ask me first by the user, and the call would then be blocked. | `brief.ts` (an ask-first field is never given a `field_ref`); `prompt_safety.py` `resolve_task_vars` raises `AskFirstInPrompt`; `profile_fields_calling_name_shared` check; `brief.test.ts` "Patient born {date_of_birth}" case |
| R6 | Ask-first fields can still be **required** (for example `date_of_birth` for a doctor). The chat makes sure the profile has the field, and the agent pings before sharing it (DC-6). | `missing.ts` `missingTemplateFields` |
| R7 | `source: "profile"` keys come from the official profile key list. That list is **still to confirm with Steve**. Today: `full_name`, `calling_name`, `phone`, `email`, `date_of_birth`, `address`. | `fakeBriefs.ts` header; PR #19 seed has `calling_name`, `full_name`, `date_of_birth`; PRD ON-3 |
| R8 | `source: "call"` keys must **not** match a profile key, and keys are unique within a template. | `brief.ts` checks call answers first, then the profile, so a clash would mix the two |
| R9 | Every `question` is one plain question. Answers are capped at 200 chars. | `chat.ts` `ANSWER_MAX` |
| R10 | `default_constraints` items use the brief.v1 `{ "text": "..." }` shape, with non-empty text of 200 chars or less, and **3 items or fewer**. The chat keeps at most 5 per list and the user needs room for their own. | brief.v1 `BriefConstraint`; `chat.ts` `LIST_ITEM_MAX`, `LIST_MAX_ITEMS` (slices to 5) |
| R11 | No personal data in templates. They ship in the frontend bundle and are the same for every user. | Privacy rule in PR #19 (brief holds keys, never values) |

---

## 3. Where templates live

Step 9's server brief builder (Python) must read the **same** definitions as the chat (TypeScript). There is no `templates` table today.

| Option | Good | Bad |
|---|---|---|
| A. Typed TS config in `frontend/src/precall/` | Simplest. Typed. Harper edits one file. | The backend can't read it, so Step 9 needs a Python copy that will drift. |
| B. Schema `contracts/call-template.v1.json` plus template JSON files served by the backend | One source of truth. `make -C contracts gen` gives typed `CallTemplate` / `TemplateField` in both TS and pydantic. Harper adds a template by adding one JSON file. | A `contracts/` change needs Steve's review, additive only. Frontend gets templates from the API (`GET /templates`) from Step 11. |
| C. DB table `call_templates` | Can be edited without a deploy. | Needs a migration, RLS and seeding, plus a fetch, for 4 rows that change rarely. Overkill now. |

**Recommendation: B, in two stages.**
1. **Now (before PR #19 merges):** agree the format here. Lotto moves the templates out of `fakeBriefs.ts` into one `templates.json` with exactly this shape. The frontend imports it, so nothing on the backend is needed yet.
2. **Step 8/9 (after PR #19):**
   - Add the schema `contracts/call-template.v1.json`. It's additive, gets Steve's review, and needs `make -C contracts gen` with both generated files committed.
   - Move the template data into Lotto's package as JSON files (`backend/app/briefs/templates/<key>.json`), served by `GET /templates`. The brief builder reads them directly.
   - In Step 11 the frontend switches from the local copy to the API.

Note: `gen` copies only `*.v1.json` schemas into the backend build. Template **data** files in `contracts/` would not reach the backend unless the Makefile changes, so it's simpler to keep the data in `app/briefs/` and keep only the schema in `contracts/`.

---

## 4. The four templates (P0 + P1)

The two P0 templates go in the template list now. The two P1 drafts stay in this doc until Harper says they're ready for the demo, then get added. All four pass R1 to R11. The lengths are `task_line` characters including placeholders: 61, 53, 69 and 84.

```json
[
  {
    "key": "restaurant_booking",
    "label": "Restaurant: book a table",
    "task_line": "I'd like to book a table for {party_size} on {day} at {time}.",
    "required_fields": [
      { "key": "calling_name", "source": "profile", "question": "What name should the booking be under?" },
      { "key": "phone", "source": "profile", "question": "What's the best number for the reservation?" },
      { "key": "party_size", "source": "call", "question": "How many people?", "suggestions": ["2", "4", "6"] },
      { "key": "day", "source": "call", "question": "Which day?" },
      { "key": "time", "source": "call", "question": "What time?" }
    ],
    "likely_questions": [
      "Name for the reservation?",
      "Phone number?",
      "Any dietary needs or a special occasion?",
      "Indoor or outdoor seating?"
    ],
    "default_constraints": []
  },
  {
    "key": "doctor_booking",
    "label": "Doctor: book an appointment",
    "task_line": "I'd like to book an appointment with {provider_name}.",
    "required_fields": [
      { "key": "calling_name", "source": "profile", "question": "Who is the appointment for?" },
      { "key": "date_of_birth", "source": "profile", "question": "What's your date of birth?" },
      { "key": "provider_name", "source": "call", "question": "Which doctor?" },
      { "key": "patient_status", "source": "call", "question": "Are you a new or existing patient?", "suggestions": ["Existing", "New"] },
      { "key": "reason", "source": "call", "question": "What's the visit for?", "suggestions": ["Annual physical", "Sick visit"] }
    ],
    "likely_questions": [
      "Date of birth?",
      "New or existing patient?",
      "Insurance carrier and member ID?",
      "Reason for the visit?"
    ],
    "default_constraints": []
  },
  {
    "key": "restaurant_cancel",
    "label": "Restaurant: cancel a reservation",
    "task_line": "I'd like to cancel a reservation for {party_size} on {day} at {time}.",
    "required_fields": [
      { "key": "calling_name", "source": "profile", "question": "What name is the reservation under?" },
      { "key": "phone", "source": "profile", "question": "What number is on the reservation?" },
      { "key": "day", "source": "call", "question": "Which day is the reservation?" },
      { "key": "time", "source": "call", "question": "What time is it?" },
      { "key": "party_size", "source": "call", "question": "For how many people?", "suggestions": ["2", "4", "6"] }
    ],
    "likely_questions": [
      "Name on the reservation?",
      "Phone number on the reservation?",
      "Would you like to rebook for another day?"
    ],
    "default_constraints": []
  },
  {
    "key": "doctor_reschedule",
    "label": "Doctor: reschedule an appointment",
    "task_line": "I'd like to reschedule an appointment with {provider_name} on {current_appointment}.",
    "required_fields": [
      { "key": "calling_name", "source": "profile", "question": "Who is the appointment for?" },
      { "key": "date_of_birth", "source": "profile", "question": "What's your date of birth?" },
      { "key": "provider_name", "source": "call", "question": "Which doctor is it with?" },
      { "key": "current_appointment", "source": "call", "question": "When is the appointment you're moving?" }
    ],
    "likely_questions": [
      "Date of birth?",
      "Name on the appointment?",
      "When is your current appointment?",
      "What days and times work better?"
    ],
    "default_constraints": [{ "text": "Keep my current appointment unless a new time is confirmed" }]
  }
]
```

Notes on the P1 drafts (based on Harper's prototype `doc-resched` and `rest-cancel`):
- **Questions the shared flow already covers.** The prototype's "Which office should I call?" is the business name and phone, which the chat already asks for every call. Its "Any times that won't work?" and the fallback question are the PC-5 lists, which the chat asks after the template questions.
- **Shared keys.** `restaurant_cancel` uses the same `day`, `time` and `party_size` keys as booking, so a later "modify" template can reuse them.
- **Why the reschedule default.** The one default constraint stops the agent from dropping the existing slot before a new one is confirmed.
- **Not in the list yet.** Both P1 drafts are added to the template list only once Harper says they're ready for the demo.

---

## 5. Open questions for Harper

1. **Key names.** Lotto's PRs (#25 to #34) and Steve's contract example (`contracts/examples/brief.v1.valid.json`) use `restaurant_booking` and `doctor_booking`. Harper's fake history in PR #21 and PR #33 (`fakeHistory.ts`) uses `restaurant_book` and `doctor_book`. **Proposal: keep `_booking`**, and change the fake history keys to match, while no real brief is saved yet. OK?
2. **The P1 list.** The PRD lists Restaurant cancel/modify and Doctor cancel/reschedule. Harper's prototype has `rest-cancel` and `doc-resched` only. Are `restaurant_modify` and `doctor_cancel` wanted for the demo?
3. **`likely_questions` aren't used by any code yet.** Should they (a) stay notes for template authors, (b) become extra chat questions, or (c) go into the brief for the agent? Option (c) needs a new brief.v1 field (Steve). For now, a business question that isn't in the brief makes the agent ping (DC-4).
4. **PC-5 lists in cancel and reschedule.** The chat asks for preferences and backups on every call. Should cancel skip them? The prototype skips the backup tip for `rest-cancel`. If yes, a small optional field may be needed later. It isn't proposed now.
5. **A default never-agree item.** The prototype pre-fills "Pay a deposit, fee, or anything else". The chat offers "A deposit or fee" as a one-tap suggestion, and DC-4 always pings on money anyway. Is that enough, or does Harper want per-template `default_never_agree`?
6. **Labels.** Are these Harper's final chip labels (her design call, PC-1), or shorter ones like the prototype's "Doctor: book"?

**For Steve, not Harper:**
- Confirm the official profile key list (R7).
- Confirm the insurance key name. `missing.ts` assumes `insurance_carrier` and `insurance_member_id`. PRD ON-5 says insurance defaults to Ask me first, but ON-3 has no insurance field.

---

## 6. Message to Harper (paste-ready)

> Hi Harper! Before I build the server side of the brief builder, I'd like us to lock the template format, so you can add templates as config without touching my code. Here's a short proposal: [link]
>
> It keeps what my chat already reads (key, label, task line, required fields, likely questions, default constraints) and adds one optional field: one-tap answer chips per question, like in your prototype. I also drafted restaurant cancel and doctor reschedule from your prototype's questions.
>
> A few things I need from you:
> 1. Key names: my PRs and Steve's contract use `restaurant_booking` / `doctor_booking`, and your fake history uses `restaurant_book` / `doctor_book`. Could we keep `_booking` and update your fake history?
> 2. Which P1 templates do you want for the demo?
> 3. What should `likely_questions` be used for?
>
> Happy to change anything. Thanks!

---

## 7. Code changes if Harper agrees (not made yet)

In a small frontend PR on Lotto's side:
- **Move the templates.** Move them out of `fakeBriefs.ts` into `frontend/src/precall/templates.json`, with the two P0 templates (P1 added when Harper says they're ready), and import it in `App.tsx` and the tests.
- **Update the types.** Add `suggestions?` to `TemplateField` in `types.ts`, and drop the "draft until agreed" comment.
- **Use the new field in the chat.** In `chat.ts`, show `suggestions` as chips in `waitForTemplateField`.
- **Add a validation test.** Add `templates.test.ts` with a `validateTemplate()` that checks R1 to R10 for every template, so Harper's template PRs fail CI rather than at call time. This replaces and extends the two template checks already in `fakeBriefs.test.ts` (profile keys, 120 chars). Then each template should run end to end through `buildBrief` with an empty `missing_fields`.
- **Backend (Step 8/9).** Add the `contracts/call-template.v1.json` schema (Steve's review), move the JSON into `backend/app/briefs/templates/`, add `GET /templates`, and run the same rule checks in a pytest.
