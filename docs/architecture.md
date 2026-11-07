# System architecture blueprint

**Status:** Accepted. Implemented as the backend foundation (Python 3.12 + FastAPI, plain SQL via psycopg 3, hosted on Railway); `backend/README.md` has the ownership map. Draft 2026-11-04, accepted 2026-11-07.
**Inputs:** [docs/requirements.md](requirements.md) (requirement IDs below refer to it) and the [Vapi API test design](superpowers/specs/2026-10-28-vapi-api-test-design.md).
**Depends on:** the Vapi go/no-go from the probe session. If Vapi fails probe 3, the same shape applies with Retell in the voice-platform box. Only the adapter changes.

This is a high-level design. It fixes the components, the edges between them, the protocol on each edge, and the delivery rules the pause-and-ping loop needs. The language, framework and host are decided: Python 3.12 + FastAPI, plain SQL via psycopg 3, hosted on Railway (see [docs/tech-doc-during-call.md](tech-doc-during-call.md) and `backend/README.md`).

## 0. Context and sizing

| | Target |
| --- | --- |
| System | Web app that places outbound calls through a hosted voice agent and pauses mid-call to ask the user (HITL) |
| Scale | Early-December demo. Up to ~10 users, **at most 3 concurrent calls**, under 100 calls a day. Peak load is about 20 webhook events/s, almost all transcript events. |
| Availability | Demo grade: one region, one backend instance, no SLA. A crash must not leave a live call stuck (§4.5). |
| Latency budgets (from the PRD and the probe pass criteria) | Transcript on screen within 2 s of the speaker stopping (DC-3). Ping on screen within 1 s of the agent's `ask_user`. The agent speaks a user's reply within 3 s (probe 3). Pause and end take effect within 2 s (DC-11). |
| Deployment | Managed services. Browser app on a static/edge host, one long-running backend container, Supabase (Postgres, Auth, Realtime), Vapi with an imported Twilio number. |

**The sizing drives the design.** At this scale, the risks are correctness (no double-spoken reply, no lost timeout, no leaked ask-first field) and third-party behaviour, not throughput. So the design uses one backend service, Postgres as the event log, and JSON over HTTPS. Section 2 says why Kafka, gRPC, a service mesh and mTLS stay out, and what would bring them in.

## 1. System overview and communication topology

### Components

| Component | Owns | Does not own |
| --- | --- | --- |
| **Web app** (browser) | Onboarding, profile, call creation and pre-call review, live call view, ping card, history. A service worker shows push notifications. | Any secret other than the user's session. Any write to call status or outcome. |
| **Backend** (one long-running service, modules below) | All call logic, every write of billing and audit data, every call to Vapi, the LLM and Web Push. | Audio, STT, TTS and turn-taking: the voice platform does those. |
| **Supabase** | Postgres (system of record), Auth (sessions, JWTs), Realtime (fan-out of call events to the browser). | Business logic. |
| **Voice platform** (Vapi) | The phone call: dialing through Twilio, STT, the in-call LLM, TTS, barge-in, tool calls, the control and listen URLs. | The user, the brief, ping state. |
| **LLM API** | Pre-call brief building (PC-3, PC-4) and the post-call summary (PO-2). | Anything during the call. The in-call model is Vapi's, and it also interprets free-text replies (DC-8), so no extra LLM call sits on the reply path. |
| **Web Push service** (the browser vendor's) | Delivering push messages to the user's browser. | Message content: it is encrypted end to end. |

Backend modules, all in one process:

- **API**: the browser-facing REST endpoints.
- **Brief builder**: goal + profile + template → structured brief and the list of missing fields (PC-3, PC-4).
- **Call controller**: creates calls on Vapi and sends control-URL commands. It is the only module that talks to Vapi.
- **Webhook ingress**: authenticates Vapi server events, normalises them into call events, and acks fast.
- **Ask manager**: the pause-and-ping state machine (filler every 15 s, 45 s timeout, reply-or-timeout race). It holds the timers.
- **Notifier**: Web Push sends and push-subscription storage.
- **Post-call processor**: outcome, summary, "what was shared" (PO-1 to PO-5).
- **Outbox worker**: sends committed side effects (push, control-URL commands, post-call jobs) in order per call, and records whether each one was delivered (§4.0).
- **Sweeper**: on start-up, every 2 s while a ping is open and every 10 s otherwise. It finds overdue pings, pending outbox rows, uncertain call creations, ended calls with no report, and calls past max length.

### Flow diagram

```text
                         ┌──────────────────────── Supabase ───────────────────────┐
                         │  Auth (JWT)      Postgres (system of record)   Realtime │
                         └────▲───────────────▲────────▲────────────────────┬──────┘
                       E2 HTTPS│        E11 PG/TLS│        │E3b (WAL → channel)  │E3 WSS
                              │               │        │                    │ (RLS)
┌─────────────────────┐  E1 HTTPS REST  ┌─────┴────────┴──────┐              │
│ Web app (browser)   │────────────────▶│ Backend              │              │
│  + service worker   │◀────────────────│  API · Brief builder │              │
│                     │◀────────────────┼──────────────────────┼──────────────┘
│                     │                 │  Call controller     │ E12 HTTPS ┌──────────┐
│                     │                 │  Webhook ingress     │──────────▶│ LLM API  │
│                     │                 │  Ask manager · Sweeper│          └──────────┘
│                     │                 │  Notifier · Post-call│
└─────────▲───────────┘                 └──▲─────┬─────┬───────┘
          │ E5b push                E7 HTTPS│  E6 │  E8 │ HTTPS
          │                         webhooks│ REST│     │ control URL
   ┌──────┴────────────┐  E5 HTTPS  ┌───────┴─────▼─────▼───────┐
   │ Web Push service  │◀───────────│ (Backend, Notifier)        │
   └───────────────────┘  (RFC 8030)│                            │
                                    │   Voice platform (Vapi)    │── E10 SIP/RTP ──▶ Twilio ──▶ PSTN ──▶ Business
                                    │   STT · LLM · TTS · tools  │
                                    └────────────────────────────┘
   E9 (P1, DC-12 only): listen-URL WebSocket, raw PCM, Backend ⇄ Vapi
```

### Edges

| Edge | From → To | Carries |
| --- | --- | --- |
| E1 | Browser → Backend | Create call, pre-call questions, ping replies, instructions, pause, end, history reads that need joins |
| E2 | Browser → Supabase Auth | Sign-up, sign-in, token refresh |
| E3 | Supabase Realtime → Browser | Live transcript, call status, new pings, ping resolution |
| E3b | Postgres → Realtime | Committed rows of `call_events` (Postgres Changes), or backend broadcasts for transcript partials |
| E5 / E5b | Backend → Push service → Browser | Ping notification (DC-8), call-ended notification (PO-5) |
| E6 | Backend → Vapi REST | `POST /call`, `GET /call/{id}`, cost lookup |
| E7 | Vapi → Backend | Server events: `status-update`, `transcript`, `speech-update`, `tool-calls`, `end-of-call-report` |
| E8 | Backend → Vapi control URL | `say`, `add-message`, `control` (mute/unmute), `end-call` |
| E9 | Backend ⇄ Vapi listen URL | P1 only: live audio for takeover (DC-12) |
| E10 | Vapi ⇄ Twilio ⇄ PSTN | The phone call. Vendor-managed; outside our boundary. |
| E11 | Backend → Postgres | All writes; reads for call logic |
| E12 | Backend → LLM API | Brief, summary |

## 2. Protocol selection matrix

| Edge | Protocol | Rationale | Rejected |
| --- | --- | --- | --- |
| E1 Browser → Backend | HTTPS REST, JSON. HTTP/2 to the edge, HTTP/1.1 behind it is fine. | Request/response, a handful of calls a minute per user. Browsers speak it natively; easy to test with curl. | **gRPC-Web**: needs a proxy and codegen for no latency gain at this volume. **WebSocket for commands**: commands are rare and each needs its own result. REST gives that; idempotency comes from our `Idempotency-Key` table (§4.1), not from HTTP. |
| E2 Browser → Auth | HTTPS (Supabase client) | Managed; no choice to make. | — |
| E3 Realtime → Browser | WebSocket (Supabase Realtime), one **private** channel per call. Postgres Changes are authorised by table RLS; Broadcast (transcript partials) by Realtime Authorization policies on `realtime.messages` that admit only the call's owner. | Server push is needed for transcript and pings. Realtime holds the sockets, so the backend holds none, and RLS decides who may subscribe. Resync on reconnect is a plain read of `call_events` by `seq` (§4.4). | **SSE from the backend**: viable and simpler to reason about, but the backend would hold one connection per viewer and re-implement authorisation. Kept as the fallback if Realtime latency breaks the 2 s budget. **Polling**: too slow and too chatty for 2 s. |
| E5 Push | Web Push (RFC 8030) with VAPID (RFC 8292) and encrypted payload (RFC 8291), `Urgency: high`, `TTL` = seconds left on the ping | The only way to reach a backgrounded tab on desktop browsers (ON-6). TTL limits how long the push service holds it; it does not guarantee arrival in time, so the tab re-checks ping state on click (§4.1). | **SMS / native**: out of scope in the PRD. |
| E6 Backend → Vapi REST | HTTPS REST, JSON, bearer private key | Vapi's API. | — |
| E7 Vapi → Backend | HTTPS POST webhooks, JSON, shared-secret header | Vapi's server-URL model. The handler only authenticates, persists and acks, so Vapi never waits on our logic. | — |
| E8 Backend → control URL | HTTPS POST, JSON | Vapi's live-call-control API. One short request per command. | — |
| E9 Listen URL (P1) | WebSocket, raw PCM frames | Vapi's only live-audio interface. Out of P0. | — |
| E11 Backend → Postgres | Postgres wire protocol over TLS, through the Supabase pooler (session mode for the long-running service) | Transactions and unique constraints carry our idempotency and race rules (§4). | **ORM-only access with no SQL constraints**: the race rules must live in the database. |
| E12 Backend → LLM | HTTPS REST, JSON, streaming off | Few calls, none during a call. | — |
| Internal events | **No broker.** `call_events` in Postgres is the append-only log; in-process dispatch inside the backend. | One producer, one consumer process, about 20 events/s. A broker adds a deployable, a failure mode and a delivery model to reason about, and buys nothing at this size. | **Kafka / Pulsar / AMQP / ZeroMQ.** Revisit when there is more than one backend instance or a second consumer: first step is a Postgres `SELECT … FOR UPDATE SKIP LOCKED` job table, then a managed queue. |
| Service to service | **None.** One backend service. | Every internal hop removed is one less timeout, retry and auth decision. | Microservices, gRPC between them. |

## 3. Interface contracts and data formats

### Serialization standard

- **JSON (UTF-8) on every edge.** Vapi, the browser and Supabase all speak it, and payloads are small (under 10 KB). Protobuf, Avro or FlatBuffers would add codegen and a binary-to-JSON hop at both ends for no gain.
- **Contracts live in the repo as JSON Schema** (draft 2020-12), one file per message, under a `contracts/` directory created with the first backend PR. TypeScript and backend types are generated from them, not written by hand.
- Times are ISO 8601 in UTC with milliseconds. Phone numbers are E.164. IDs are UUIDs, except Vapi's own IDs, which are opaque strings.

### Schema evolution

1. **Additive changes only** on a released contract: new optional fields, new event types.
2. **Readers ignore unknown fields** (tolerant reader). This applies to Vapi's payloads too: we validate only the fields we use and keep the raw body.
3. **Enums are open.** Clients show an "Unknown" state for a value they don't know, never crash. Status and outcome enums (DC-14, PO-1) can grow.
4. **Every stored event carries `schema_version`.** A breaking change gets a new event `type` or a `/v2` path, and both run side by side until the old one has no readers.
5. **Database changes follow expand → migrate → contract**, each as its own migration (AGENTS.md rule: never edit a merged migration).

### Canonical call event (stored in `call_events`, pushed over E3)

Webhook ingress maps each Vapi event to this envelope. The browser only ever sees our envelope, never Vapi's shape, so a platform switch doesn't touch the frontend.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "contracts/call-event.v1.json",
  "type": "object",
  "required": ["event_id", "call_id", "seq", "type", "occurred_at", "schema_version", "payload"],
  "properties": {
    "event_id":   { "type": "string", "format": "uuid" },
    "call_id":    { "type": "string", "format": "uuid" },
    "seq":        { "type": "integer", "minimum": 1, "description": "Gap-free per call, assigned on insert" },
    "type":       { "enum": ["status.changed", "transcript.final", "ping.opened", "ping.resolved",
                             "agent.decision", "field.shared", "command.applied", "call.ended"] },
    "occurred_at":{ "type": "string", "format": "date-time" },
    "source":     { "enum": ["vapi", "backend", "user"] },
    "dedup_key":  { "type": "string", "description": "Natural key from the source event, unique per call" },
    "schema_version": { "const": 1 },
    "payload":    { "type": "object" }
  },
  "additionalProperties": true
}
```

### The `ask_user` tool (given to the in-call model)

This matches the probe kit, so probe results carry over. It is an **async** tool: Vapi resolves it at once and the agent keeps the line warm with a hold line.

```json
{
  "type": "function",
  "async": true,
  "function": {
    "name": "ask_user",
    "description": "Ask the user when an offer breaks a hard constraint with no matching fallback, when the other side asks for an ask-first field, or before agreeing to any payment, deposit or fee, or to a never-agree item, whatever your confidence.",
    "parameters": {
      "type": "object",
      "required": ["question", "reason", "suggested_replies", "confidence", "dimensions"],
      "properties": {
        "question": { "type": "string", "maxLength": 200 },
        "reason":   { "type": "string", "maxLength": 200, "description": "Plain language, e.g. 'They offered 4pm. You said before 3pm.' (DC-5)" },
        "suggested_replies": { "type": "array", "minItems": 2, "maxItems": 4, "items": { "type": "string", "maxLength": 60 } },
        "field_requested": { "type": "string", "description": "Profile field key when the trigger is an ask-first field (DC-6)" },
        "confidence": { "enum": ["high", "medium", "low"], "description": "The tier behind this call. Stored backend-only (agent_scores); never in the card or the push (DC-4, DC-5)" },
        "dimensions": { "type": "object", "description": "The six DC-4 booleans behind the tier: needs_ask_first_field, involves_money, meets_constraints, matches_fallback, answerable_from_brief, understood. All required, no extras. Backend-only." }
      }
    }
  }
}
```

### The `report_decision` tool (DC-4, DC-5)

The in-call agent scores each offer or question against the brief, as DC-4 requires. It calls `report_decision` for every action it takes on its own. It calls `ask_user` instead whenever the tier is Low, and **always, regardless of score**, for an ask-first field (DC-6) and before agreeing to any payment, deposit or fee, or to a never-agree item from the brief (DC-13, PC-5). These overrides are in the tool description and the system prompt, and the backend checks them after the call: a `report_decision` of `accept` on a situation that mentions money, with no ping before it, is flagged in the summary as a guardrail breach. The tool is async and fire-and-forget. The backend stores each report as an `agent.decision` event. The tier and its six `dimensions` are kept for debugging and are never sent to the browser; the reason is shown (DC-5). The scoring rubric and thresholds belong in the tech doc; this fixes only where the score is produced and how it is reported.

```json
{
  "type": "function",
  "async": true,
  "function": {
    "name": "report_decision",
    "parameters": {
      "type": "object",
      "required": ["situation", "action", "confidence", "dimensions", "reason"],
      "properties": {
        "situation":  { "type": "string", "maxLength": 200, "description": "What the other side offered or asked" },
        "action":     { "enum": ["accept", "counter", "decline", "share_field", "take_fallback", "end_call"] },
        "confidence": { "enum": ["high", "medium", "low"] },
        "dimensions": { "type": "object", "description": "The same six DC-4 booleans as ask_user. Backend-only." },
        "reason":     { "type": "string", "maxLength": 200 },
        "field_key":  { "type": "string", "description": "For share_field: the share-freely field the agent is about to say" }
      }
    }
  }
}
```

### The `report_outcome` tool (PO-1, PO-2)

Before it ends the call, the agent calls `report_outcome` once. The outcome needs evidence, not just a final action.

```json
{
  "type": "function",
  "async": true,
  "function": {
    "name": "report_outcome",
    "parameters": {
      "type": "object",
      "required": ["outcome", "business_confirmed"],
      "properties": {
        "outcome": { "enum": ["booked", "cancelled", "rescheduled", "needs_follow_up", "refused_ai", "wrong_number"] },
        "business_confirmed": { "type": "boolean", "description": "True only if the other side explicitly confirmed it, in words" },
        "details": { "type": "object", "properties": {
          "date": { "type": "string" }, "time": { "type": "string" },
          "confirmation_number": { "type": "string" }, "staff_name": { "type": "string" } } },
        "open_items": { "type": "array", "items": { "type": "string" } }
      }
    }
  }
}
```

The outcome job (§4.1) accepts Booked, Cancelled or Rescheduled only when `business_confirmed` is true **and** its own check finds the confirmation in the final transcript. That check is part of the outcome job, not the summary job, so a failed summary never blocks the outcome. Otherwise the outcome is `Needs follow-up`, with the reason. No answer, or a call that ends with no `report_outcome`, is decided from the end reason Vapi reports.

### Ping (event payload and push payload)

```json
{
  "$id": "contracts/ping.v1.json",
  "type": "object",
  "required": ["ping_id", "call_id", "kind", "question", "reason", "suggested_replies", "expires_at", "state"],
  "properties": {
    "ping_id": { "type": "string", "format": "uuid" },
    "call_id": { "type": "string", "format": "uuid" },
    "kind": { "enum": ["decision", "field_approval"], "description": "field_approval: server-written question and Share / Don't share replies (§3, privacy)" },
    "field_key": { "type": "string", "description": "Only for field_approval" },
    "question": { "type": "string" },
    "reason": { "type": "string" },
    "suggested_replies": { "type": "array", "items": { "type": "string" } },
    "expires_at": { "type": "string", "format": "date-time" },
    "state": { "enum": ["open", "answered", "expired", "superseded", "cancelled"] }
  }
}
```

The push payload carries the same fields minus `state`, and never a profile field's value.

### Browser → backend commands (E1)

| Method and path | Body | Notes |
| --- | --- | --- |
| `POST /calls` | `{ brief_id }` | Starts a reviewed brief (PC-6). Validates the number (PC-7) and the caps (DC-15) again on the server. |
| `POST /calls/{call_id}/pings/{ping_id}/reply` | `{ "choice": 0 }` or `{ "text": "..." }` | `409 ping_closed` if the ping already expired or was answered. |
| `POST /calls/{call_id}/commands` | `{ "type": "instruction" \| "pause" \| "resume" \| "end", "text"? }` | DC-10, DC-11. |

Every mutating request carries an `Idempotency-Key: <uuid>` header and the Supabase JWT. Errors use one shape: `{ "error": { "code": "ping_closed", "message": "...", "retryable": false } }`.

### Backend → control URL (E8)

| Intent | Vapi message |
| --- | --- |
| Filler (DC-7) | `{"type":"say","content":"Thanks for holding, still checking with Alex."}` |
| User reply or approved field value (DC-6, DC-8) | `{"type":"add-message","message":{"role":"system","content":"Alex's answer to \"<ping question>\": ..."},"triggerResponseEnabled":true}` |
| Timeout (DC-9) | Same, with "The user did not reply in time. Use a matching fallback or end politely." |
| Instruction (DC-10) | `add-message` with `triggerResponseEnabled: false`, so it's used at the next natural turn |
| Pause (DC-11) | `say` hold line, then `{"type":"control","control":"mute-assistant"}`. Vapi's events don't say which utterance a `speech-update` belongs to, so the gate can't tie one to the hold line. It uses a quiet rule instead: the control lane holds back any new filler, and mutes once the assistant has been silent for 700 ms after at least one assistant *stopped* event that came after the `say`. With no such quiet within 4 s it mutes anyway. Probe 6 tests pause during silence, during an agent sentence and during a filler (§7). |
| End (DC-11) | `{"type":"say","content":"<goodbye>","endCallAfterSpoken":true}`, so Vapi hangs up only after the goodbye has played. `end-call` is the fallback if the call is still up 6 s later. |

**Tool contracts are args-only.** The JSON above shows the shape the model fills in. The authoritative schemas are `contracts/tools/<name>.args.v1.json`; the backend builds the Vapi tool definitions from them (`app/voice/tools.py`) and validates every `tool-calls` argument object against them. Tool names, descriptions and the async flag are fixed in the backend; a config that supplies its own `model.tools` is overwritten.

**Where scores live (DC-5).** `confidence` and `dimensions` are stored in `agent_scores` (backend-only). Outside it they exist in only two backend-only places: the redacted raw delivery log (`vapi_raw_events`), and `tool_calls.arguments` while the receipt is `pending`. They are stripped from `tool_calls.arguments` when the receipt is handled or failed. Handlers and browser payloads never see them: browser-readable payloads are built from explicit allow-lists, and `agent.decision` has no score fields.

### Data model (all tables RLS-enabled; browser reads only its own rows)

`profiles`, `profile_fields` (key, value, `sharing` = `share_freely` \| `ask_first`), `briefs`, `calls` (status, outcome, `vapi_call_id`, max length), `call_events` (append-only, unique `(call_id, seq)` and `(call_id, dedup_key)`, `processed_at`), `pings` (kind, state, `expires_at`, unique `vapi_tool_call_id`, at most one open per call), `call_profile_snapshots`, `shared_fields` (PO-3, with disclosure state), `outbox` (§4.0, three lanes), `push_subscriptions`, `idempotency_keys`, `tool_calls` (one receipt per Vapi tool call: `pending`, `handled` or `failed`), `agent_scores` (`confidence` and `dimensions` per scored tool call) and `call_secrets` (the control and listen URLs, deleted when the call ends). Backend-only, with no browser access: `vapi_raw_events`, `call_events_failed`, `tool_calls`, `agent_scores`, `call_secrets`, `call_profile_snapshots`, `outbox` and `idempotency_keys`. Calls, call events, pings, shared fields and the outbox are written only by the backend.

**Privacy by construction (ON-5, DC-6, DC-13).**
- **Each call uses a snapshot.** At call start, the backend copies the user's field values and sharing settings into `call_profile_snapshots`. Everything during the call (the prompt, approvals, the injected value) uses the snapshot. Edits apply to the next call (ON-7).
- The assistant config sent to Vapi contains only share-freely values. Before approval, the agent cannot leak an ask-first value because it doesn't have one.
- **The server, not the model, decides what an approval covers.** When `ask_user` names a `field_requested`, the backend checks that key against the call's snapshot with `sharing = ask_first`. If it matches, the ping is a `field_approval` ping: the server writes the question ("Share your date of birth with Boston Dental?") and the replies (Share / Don't share). The model's suggested replies are dropped. An unknown key, or a share-freely key, gets a normal decision ping.
- Only an explicit Share on a `field_approval` ping releases that one field, for this call only. The backend injects the value with `add-message`. A free-text reply never releases a field value.
- **"What was shared" (PO-3) tracks approval and disclosure apart.** A `shared_fields` row moves `approved → sent_to_agent → spoken`. `sent_to_agent` is set when the control lane delivers the value. `spoken` is set after the call, when the value appears in an agent turn of the final transcript. Share-freely fields get rows from `report_decision` (`share_field`) and from the same transcript check, so a field the agent said without reporting it still shows up. The post-call view lists what was spoken, and marks fields that were sent to the agent but never said.
- After injection the value is in the call's context for the rest of the call. That is the scope the card states: "for this call". Later calls start without it.

**Recording and disclosure by construction (DC-1, PO-4).**
- Every assistant config sets `artifactPlan.recordingEnabled: false` and `artifactPlan.transcriptPlan.enabled: true`. Vapi records by default, so the backend refuses to create a call from a config without these two values, and a test pins them.
- The opening line is a fixed `firstMessage` template, not model output. It uses the **calling name**, a profile field that is always share-freely because DC-1 requires the opening to say who we're calling for. The user chooses it in onboarding (default: full name; "Alex" or "Alex C." is fine), and the profile UI explains that it is always said. Every template substitution, the opening included, reads only share-freely values from the snapshot. This is a design decision on top of ON-5, for the PRD owners to confirm.

## 4. Message delivery, idempotency and failure semantics

### 4.0 Processing model: state first, side effects from state

One rule carries most of this section. **Every decision is committed to Postgres before anything leaves the process, and every outbound side effect is driven from a committed row, not from memory.**

- **Webhook ingress does the durable part inline.** In one transaction it authenticates, inserts the normalised event, and applies the state change that event implies: a new ping row for `ask_user`, a status transition, a transcript segment. Then it returns `200`. These are a few small inserts, well inside the 500 ms ack budget. A crash after the commit loses nothing, because the state already exists.
- **Outbound side effects go through an `outbox` table.** Each row has `call_id`, `lane`, `kind`, `payload`, `state` (`pending`, `sent`, `uncertain`, `failed`, `cancelled`), `attempts` and `not_before`. It is written in the same transaction as the state change that caused it. There are three lanes, each with its own worker, so a slow push can never delay a reply:
  - **control**: every control-URL command for a call (filler, reply, timeout message, instruction, pause, resume, end), strictly in order per call, one in flight at a time.
  - **notify**: Web Push sends.
  - **jobs**: post-call processing.
- **Timers are rows too.** A ping's filler and timeout times are columns on the ping. The in-process timer is only a wake-up; the sweeper (every 2 s while any ping is open, otherwise every 10 s) finds overdue rows on its own, so a lost timer only costs latency.
- **On start-up** the backend replays: `pending` outbox rows are sent, overdue pings run the timeout path, and events with `processed_at IS NULL` are processed again. Each step is idempotent, so a replay is safe.
- **Tool calls leave a receipt.** Each Vapi tool call is stored in `tool_calls` as `pending` and answered `"received"` at once. A registered handler runs inside the ingress transaction, in its own savepoint, and the receipt becomes `handled` or `failed`; a redelivery never reruns it. A tool with no handler stays `pending`, and `dispatch_pending(pool, limit=50, *, tools)` runs it later: at start-up, and from Coco's sweeper, which passes `tools=reg.tools`. The call lock (`lock_call`) is always taken before any `pings`, `call_secrets`, `outbox` or `call_events` row.

### 4.1 Guarantees per flow

The guarantees are stated as they are, including where they are weaker than "exactly once".

| Flow | Guarantee | How |
| --- | --- | --- |
| Vapi webhooks → `call_events` (E7) | **Each delivered event is stored once.** Whether Vapi retries a failed delivery is unconfirmed (§7), so an event Vapi never redelivers is lost. Treat delivery as possibly duplicated and out of order. | A `dedup_key` from natural fields: `toolCallId` for tool calls, `(status)` for status updates, `(role, start time)` for final transcripts. `INSERT … ON CONFLICT DO NOTHING`. Transcript `source_start_ts` and the dedup key currently come from Vapi's `message.timestamp` (§7). The sweeper's `GET /call/{id}` repairs lost status and end events. |
| `ask_user` → ping | **One ping per tool call, and at most one open ping per call; a newer question replaces an older one** | Unique `pings.vapi_tool_call_id`, and a partial unique index on `pings(call_id) WHERE state = 'open'`. A second `ask_user` while one is open means the situation changed (for example, Thursday was withdrawn and Friday offered). "Newer" means a later **source timestamp** (the timestamp Vapi puts on the tool-call message), not a later arrival. `calls.latest_ask_at` is a per-call watermark across every ping, whatever its state. A question whose timestamp is not later than the watermark is stored as `superseded` on arrival and never shown, even when no ping is open. Ties are broken by `toolCallId`, so the order is the same on every replay. Otherwise, in one transaction, under the ping lock, the old ping becomes `superseded` and the new one opens with a fresh 45 s timer. The card swaps to the new question and says the offer changed. A reply to the superseded ping gets `409 ping_closed`. The prompt tells the agent to put everything it still needs into the newer question. Every injected answer quotes the question it answers, so the agent can't apply it to the wrong one. |
| Ping resolution: reply vs. timeout | **Exactly one winner, chosen atomically against the deadline** | In one transaction: `SELECT … FROM pings WHERE ping_id = $id FOR UPDATE`, then compare `expires_at` with `clock_timestamp()` (wall-clock time after the lock, not `now()`, which is the transaction start). The reply wins only if the ping is open and not expired; the timeout wins only if it is open and expired. The winner updates the state and writes its outbox command in the same transaction. A reply that loses gets `409 ping_closed`. Once a reply has won, its delivery is no longer bound by `expires_at`; it has its own 2 s send budget. |
| Reply, timeout and instruction → control URL (E8) | **Bounded best effort, with delivery tracked.** Not at-least-once: after one retry a send can still be lost, and that is shown, not hidden. Vapi accepts no idempotency key. | Sent from the outbox. The adapter itself never retries: it raises `VoiceNotSent` (the request provably never left) or `VoiceUncertain` (it may have been processed: timeout, dropped connection or 5xx). The outbox worker retries once, and only on `VoiceNotSent` (§5.3). A `VoiceUncertain` send may have been applied, so it marks the row `uncertain`, not retried, and the ping card says "may not have reached the call", with Resend and End call. On start-up, a reply row still `pending` (never sent) is sent. A row that may have gone out becomes `uncertain`, and only the user's Resend sends it again: a repeated sentence is the user's call. |
| Filler `say` | **At most once** | A control-lane row with no retry, and never replayed on start-up. The pre-send state check drops it if the ping closed or the call was paused or ended after it was queued. A late filler is worse than a missing one. |
| Push notification (E5) | **At most once, best effort** | `TTL` = seconds left until `expires_at`, `tag` = `ping_id`. On click, the tab reads the ping's current state before showing the reply buttons; when the ping closes, the open tab closes the notification. The in-app card over Realtime is the source of truth. A `410 Gone` deletes the subscription. |
| Browser commands (E1) | **One effect per `Idempotency-Key`** | The key is claimed with an insert into `idempotency_keys(user_id, key)` in the same transaction as the command's state change. A concurrent duplicate gets `409 in_progress`; a later one gets the stored response (kept 24 h). External effects follow the outbox states above, so "accepted" never means "delivered". |
| Call creation (E6) | **No blind retry, so at most one Vapi call per start, with an explicit uncertain state** | The `calls` row is committed as `creating`, with our `call_id`. Then `create_attempted_at` is committed, and only then is `POST /call` sent, with `call_id` in Vapi `metadata`. A success stores `vapi_call_id`. A timeout, or a `creating` row with `create_attempted_at` set and no `vapi_call_id` after 15 s (a crash mid-request), becomes `creation_uncertain`. The sweeper looks it up by metadata until it is found or the max call length plus 5 min passes. The UI shows "checking" and, after 2 min, "failed". A call found after that is adopted and ended at once with a short apology, and logged. Nothing is ever resent. |
| Post-call processing | **One outcome per call; each artifact retried on its own** | Triggered by `end-of-call-report`, or by the sweeper with `GET /call/{id}` if no report arrives within 2 min of the end. Three independent jobs, each keyed by `call_id` with its own state: **outcome** (PO-1, from `report_outcome` checked against the transcript, or from the end reason), **summary** (PO-2, LLM) and **shared fields** (PO-3, transcript check). Each retries with backoff, and a failure of one never blocks or overwrites another. If the outcome can't be determined, it is `Needs follow-up` with the reason, never a guessed `Failed`. A missing summary shows as "summary unavailable, retry" next to the transcript. |

### 4.2 Ordering and per-call control

- Order matters only **within a call**. `seq` is assigned on insert under a per-call lock, so the browser can render in order and detect gaps.
- **All control-URL commands for a call go through the control lane**, fillers included. Just before each send, the worker re-reads the call and ping state, and drops a command the state has made obsolete. Rules between commands:
  - **Pause** suppresses filler and mutes the assistant, and freezes an open ping's timer. Under the ping lock it stores the time left (`expires_at` minus `clock_timestamp()`) and clears `expires_at`, so the timeout can't fire while paused. A reassurance `say` plays every 15 s while paused.
  - **Resume** sets `expires_at` to `clock_timestamp()` plus the stored time left, so the timer picks up where it left off.
  - **A reply while paused** queues `unmute` first, then the reply, so it is audible.
  - **Pause cap (proposed 2 min, tuned from test calls):** queues `unmute`, then a call-back offer and goodbye, and the outcome is Needs follow-up (DC-11).
  - **Resume** also unmutes.
  - **End** cancels the open ping and marks every pending control row for the call `cancelled`, then queues unmute (if paused) and the goodbye `say` with `endCallAfterSpoken`. Only if the call is still live 6 s later does it send `end-call`. Nothing queued after End is sent.
- Call status is a state machine: `creating → dialing → in_phone_menu? → talking ⇄ waiting_on_you ⇄ on_hold → ended`, with `creation_uncertain` and `failed` off `creating`. Transitions out of `ended` or `failed` are rejected (`end_call_transition` treats both as terminal), so a late `status-update` can't revive a call. `waiting_on_you` comes from an open ping and `on_hold` from a user pause, not from Vapi events (DC-14).
- Transcript events are ordered by Vapi's start timestamp, not arrival time.

### 4.3 Dead-lettering and raw payloads

There is no broker, so "dead letter" means a row. An event that fails validation or processing goes to `call_events_failed` with the error. It is never retried on its own, and it never blocks later events for the same call.

**Raw Vapi bodies are redacted before they are stored**: `monitor.controlUrl`, `monitor.listenUrl` and any other URL with a token are replaced by a marker. Raw bodies live in `vapi_raw_events`, a backend-only table with no browser access and 7-day retention. The browser-readable `call_events` holds only our normalised payloads. Logs never carry raw bodies.

### 4.4 Backpressure and flow control

- **Admission control at the source:** at most 1 live call per user and 3 across the app (Vapi and Twilio concurrency, and budget). Per-user daily cap (DC-15) and Vapi `maxDurationSeconds` = 900 (15 min).
- **Webhook ingress does no slow work inline:** no LLM, push or control-URL calls (§4.0). If Postgres is down it returns `503`.
- **Transcript partials are never stored.** They go over private Realtime broadcast only (owner-only, §2); finals are stored. This cuts writes by roughly 10×. If the broadcast policy isn't ready, partials are left out and finals alone still meet DC-3.
- **Browser resync without a gap:** on (re)connect the client subscribes first and buffers what arrives, then reads `call_events WHERE seq > last_seen`, then merges both and drops duplicates by `seq`. Realtime is never the only copy.

### 4.5 Failure modes

| Failure | Effect | Handling |
| --- | --- | --- |
| Backend crashes mid-call | The call goes on at Vapi. Events Vapi sends while we're down may be lost; committed state and the outbox are not. | The host restarts the container on failure (health check every 10 s), and start-up replays pending outbox rows, overdue pings and unprocessed events (§4.0). The sweeper reconciles call state with `GET /call/{id}`. The prompt also tells the agent to wrap up politely if it has held for over 60 s with no update, but that is best effort and does nothing while muted. **The hard bound is Vapi's `maxDurationSeconds` (15 min)**, enforced by the platform without us. |
| Vapi API down | Calls can't start. | Circuit breaker on E6: after 3 consecutive failures, "Start call" is disabled with a banner for 60 s, then one trial request. |
| Control URL fails during a ping | Reply not delivered, or delivery unknown. | One retry (§4.1). Then the outbox row is `failed` or `uncertain`, and the ping card shows "may not have reached the call" with Resend and End call. |
| User's browser closed or push denied | The ping may go unseen. The in-app card still works if the tab is open. | Nothing special: if no reply arrives, the normal 45 s timeout path runs (DC-9). A matching fallback can still end in a booking; the outcome comes from the rest of the call, as for any call. |
| Realtime disconnects | Live view stalls. | The banner is driven by the Realtime channel's own status and heartbeat, not by event gaps: silence and waiting on a ping are normal. On reconnect the client resyncs by `seq` (§4.4). |
| LLM API slow or down | Pre-call brief or summary is delayed. | Brief: 20 s timeout, user can retry; templates still work without the LLM for required-field detection. Summary: retried by the post-call job; the transcript and outcome show without it. |
| Webhook secret wrong or missing | Possible spoofing. | `401`, logged, never processed. |

## 5. Network and traffic engineering

### 5.1 Encryption in transit

- Every hop that leaves a process is TLS: TLS 1.3 where the endpoint offers it, TLS 1.2 as the floor. Browser-facing hosts send HSTS. Postgres connections use `sslmode=verify-full`.
- **No mTLS and no service mesh.** There are no internal service-to-service hops to protect: one backend, and every other party is a managed vendor endpoint we reach over public TLS. A mesh would add sidecars, certificate rotation and a control plane for zero edges. Revisit only if the backend splits into services on a private network.

### 5.2 Authentication and token propagation

| Hop | Credential | Rule |
| --- | --- | --- |
| Browser → Backend | Supabase JWT (bearer) | Verified against Supabase's JWKS on every request. `user_id` comes from the token, never from the body. |
| Browser → Realtime / Postgres reads | Same JWT, anon key | RLS on every table; channel joins checked by RLS. |
| Backend → Postgres | Postgres connection string (`DATABASE_URL`, Supabase session pooler). Never the service-role API key. | Connects as the database owner, so RLS does not apply: every backend query filters by the authenticated `user_id`. Used only by the backend. |
| Vapi → Backend | Shared secret in a header, compared in constant time. Use Vapi's HMAC signature instead if the account supports it. | The event must match a call we created, by `vapi_call_id` or by our `call_id` in `metadata`. The second covers events that arrive before `POST /call` returns, since the `calls` row is committed first (§4.1). Unknown calls are rejected. |
| Backend → Vapi | Private API key | Server-side only. The public key is unused in P0. |
| Control and listen URLs | Capability URLs (the URL is the credential) | Never sent to the browser, never logged in full, dropped once the call ends. |
| Backend → Push service | VAPID key pair | The private key lives with the other secrets. |
| Backend → LLM | API key | Server-side only. |

All secrets live in the host's secret store or `.env` (git-ignored); names go in `.env.example`.

### 5.3 Timeouts, retries and circuit breaking

| Call | Timeout | Retries | Breaker |
| --- | --- | --- | --- |
| Webhook handler total | Ack within 500 ms, after the commit (§4.0) | — (Vapi's own retry behaviour, if any, to be confirmed) | — |
| Control URL (E8) | 2 s | 1, from the outbox and only on `VoiceNotSent` (the request provably never reached Vapi: connection refused, DNS or TLS failure), 200–500 ms jitter. The adapter never retries. A 5xx or a timeout (`VoiceUncertain`) may have been applied, so it is `uncertain`, not retried. | — (per-call, short-lived) |
| Vapi REST (E6) | 5 s connect, 10 s total | Reads: 2 (made by the caller, not the adapter), exponential backoff with full jitter, base 200 ms, cap 2 s. `POST /call`: look up first, never blind (§4.1). | 3 failures → open 60 s |
| LLM (E12) | 20 s | 1 | 3 failures → open 60 s; fall back to templates |
| Push (E5) | 3 s | 0 | — |
| Postgres statement | 2 s | 0 inside a transaction | — |

**Deadline propagation.** The ping's `expires_at` is the deadline for *choosing* a winner, and the check runs under the ping's row lock against `clock_timestamp()` (§4.1), so no reply can win after expiry and no accepted reply is dropped. Once a reply has won, delivery gets its own 2 s budget, independent of `expires_at`. Browser requests send their own client timeout (`X-Request-Timeout-Ms`); the backend does not start external work it can't finish inside it, and reports anything already started through the outbox state.

### 5.4 Rate limiting and abuse controls

- Per user: 1 live call, the DC-15 daily call cap, 30 commands and replies per minute, 10 brief builds per hour.
- App-wide: 3 concurrent calls, and a daily spend cap checked against Vapi's per-call cost, after which new calls are refused.
- Number validation on the server (PC-7): US E.164 only; block 911, other N11 codes and premium-rate prefixes; reject numbers marked personal (out of scope in the PRD).
- The webhook endpoint is open to the internet, so the secret check and the known-call check run before any parsing beyond the header. Vapi documents a static webhook range, `167.150.224.0/23`. Setting `VAPI_IP_ALLOWLIST=true` rejects other source addresses, as an optional extra layer. It is off by default, and the secret stays the real check (behind a proxy the source address may not be Vapi's).

### 5.5 Discovery and load balancing

One backend instance behind the host's TLS-terminating load balancer, addressed by a stable DNS name that Vapi uses as its server URL. No service discovery is needed. Moving to two or more instances needs three changes, in this order: ping timers move from process memory to a Postgres job table claimed with `SKIP LOCKED`; per-call work is pinned to one owner with an advisory lock on `call_id`; webhook ingress stays stateless and can run anywhere.

## 6. Pause-and-ping sequence (the critical path)

```text
Business      Vapi               Backend                       Postgres/Realtime       Browser
   │ "Thu 4pm?" │                    │                                │                    │
   │───────────▶│ ask_user (async)   │                                │                    │
   │            │──── E7 tool-calls ▶│ one txn: event + ping (unique  │                    │
   │            │                    │   toolCallId) + outbox rows ──▶│ commit             │
   │            │◀─── 200 (<500 ms) ─│ (ack after commit)             │── E3 ping.opened ─▶│ card
   │◀ hold line │                    │── E5 push (TTL = time left) ───────────────────────▶│ notification
   │            │◀── E8 say (15 s) ──│ filler (control lane)          │                    │
   │            │                    │◀───────────── E1 reply (Idempotency-Key) ───────────│
   │            │                    │ txn: lock ping FOR UPDATE;     │                    │
   │            │                    │   open AND expires_at >        │                    │
   │            │                    │   clock_timestamp()? → answered│                    │
   │            │                    │   + control-lane row ─────────▶│ commit (winner)    │
   │            │◀── E8 add-message ─│                                │── ping.resolved ──▶│
   │◀ "Alex says Fri 10am works"     │                                │                    │
                    budget: reply → agent speaks ≤ 3 s; on 45 s with no reply, the timeout path takes the same lock and wins instead
```

## 7. Open questions this design depends on

| Question | Answered by | If the answer is bad |
| --- | --- | --- |
| Does the agent hold the line for 45 s with filler, and speak an injected reply within 3 s? | Probe 3 | No-go for Vapi; rerun the probes on Retell. The design is unchanged. |
| Does Vapi retry webhooks, and do events carry stable IDs? | Probe logs | Dedup keys stay on natural fields (current design). |
| Does every `tool-calls` message carry a source timestamp, so two `ask_user` calls can be ordered? | Probe logs (scenario B) | Don't supersede automatically: keep the open ping, record the second, and send "one question at a time" to the agent, which re-asks after the answer. |
| Does Realtime's Postgres Changes path fit the 2 s transcript budget? | First backend spike | Switch E3 to backend broadcast, or SSE. |
| Is the transcript `message.timestamp` stable across redeliveries and unique per utterance? The transcript `source_start_ts` and dedup key use it. | Probe logs (replay the same call twice, compare) | Dedup on `(role, text, timestamp)`, or add a content hash; two distinct utterances sharing a timestamp would otherwise be merged. |
| Vapi assumptions the adapter makes: (1) `GET /call?createdAtGt=…&limit=100` is the list call `find_call_by_app_id` uses; (2) list items carry `monitor` URLs; (3) the control URL needs no auth header; (4) transcript roles are `assistant`/`bot` (our `agent`) and `user`/`customer` (our `business`). | Probe session | (1) and (2): adjust the lookup, and a call that cannot be found stays `creation_uncertain`. (3): add the header in `send_control`. (4): extend `ROLE_MAP`; an unknown role goes to `call_events_failed`. |
| Does the quiet rule let the hold line finish when Pause lands during an agent sentence or a filler? Does `endCallAfterSpoken` let the goodbye finish before hang-up? | Probe 6 | The 4 s and 6 s fallbacks are recovery paths, not a pass: if the normal path misses DC-11's 2 s, probe 6 is a Partial and the tech doc names the workaround. Measure the normal path separately. |
| Can the user listen live and take over (DC-12, P1)? | Probe 10 | Takeover stays out of the demo. |
| Is per-call cost reported late? | Report script | The spend cap uses an estimate from duration until the cost arrives. |
