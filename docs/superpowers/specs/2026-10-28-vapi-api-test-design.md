# Vapi API test: design spec

- **Owner:** 宏儒 Lin
- **Status:** Draft for review
- **Date:** 2026-10-28
- **Notion task:** "Speech ↔ Text Call API Testing", due 2026-10-28
- **Feeds:** the tech doc and stack decision, Nov 2–6
- **Sources:** the Notion page's "Read this before we start working" and "Product Requirements" sections. Requirement IDs such as DC-7 refer to that PRD.

## 1. Purpose

Decide, with evidence, whether Vapi can carry the P0 call loop before the tech doc picks the stack. The output is a go/no-go verdict and a table of results the tech doc can cite. It is not product code.

The riskiest question comes from the PRD itself. DC-7 calls it "the biggest unvalidated assumption": can the agent keep the line open, with natural hold talk, while it waits up to about 45 seconds for the user's reply? Our edge, "pause, don't hang up", depends on the answer.

### In scope

- The P0 call behaviours a voice platform must support: DC-1, DC-3, DC-6 to DC-11, DC-13, DC-14, PO-1, PO-2 and PO-4.
- Two items the PRD marks "check Vapi support": live voice takeover (DC-12, P1) and phone menus (DC-2, P2).
- Per-call cost, as input to call limits (DC-15).

### Out of scope

- Product backend and frontend code. The probe kit is throwaway.
- The quality of confidence scoring (DC-4). The probes use a deliberately simple rule and test the mechanism, not the rubric. The rubric belongs in the tech doc.
- Calls to real businesses. Every receptionist is a teammate.
- Retell, unless the go/no-go rule in section 3 triggers it.

## 2. What Vapi offers (from its docs, 2026-10-28)

These facts shaped the design. Each probe confirms the relevant one on a live call.

| Need | Vapi feature | Doc |
| --- | --- | --- |
| Outbound calls | `POST /call` with an assistant, a customer number and a phone number ID. **Free Vapi numbers are inbound only**, so outbound needs an imported number. We use a Twilio trial number. | [Free telephony](https://docs.vapi.ai/free-telephony), [Import Twilio](https://docs.vapi.ai/phone-numbers/import-twilio) |
| Mid-call control | The call's `monitor.controlUrl` accepts `say`, `add-message` (with `triggerResponseEnabled`), `control` (`mute-assistant`, `unmute-assistant`), `end-call`, `transfer` and `handoff`. | [Live call control](https://docs.vapi.ai/calls/call-features) |
| Live audio | `monitor.listenUrl` is a WebSocket of raw PCM audio. The docs don't state the sample rate, so the listen probe has to discover it. | [Live call control](https://docs.vapi.ai/calls/call-features) |
| Waiting on the user | A function tool with `"async": true` resolves immediately, so the conversation continues while our server waits. The late answer goes in through `add-message`. | [Function tool troubleshooting](https://docs.vapi.ai/tools/custom-tools-troubleshooting) |
| Live transcript and status | Server events: `transcript` (partial and final), `speech-update`, `status-update` (`queued`, `ringing`, `in-progress`, `forwarding`, `ended`), `tool-calls`, `end-of-call-report`. Chosen through `assistant.serverMessages`. | [Server events](https://docs.vapi.ai/server-url/events) |
| No audio stored | `artifactPlan.recordingEnabled: false` with `artifactPlan.transcriptPlan.enabled: true`. Recording defaults to on. | [Call recording](https://docs.vapi.ai/assistants/call-recording) |
| Phone menus | Built-in DTMF keypad tool and an IVR navigation guide. | [IVR navigation](https://docs.vapi.ai/ivr-navigation) |

## 3. Probes and pass criteria

Each probe gets one verdict:

- **Pass:** works as the PRD specifies.
- **Partial:** works with a workaround, which the result names.
- **Fail:** doesn't work.

| # | Probe | Requirements | Pass criteria |
| --- | --- | --- | --- |
| 1 | Outbound call and disclosure | DC-1 | The first utterance names the user, says it is an AI and says the call is transcribed, on 5 of 5 calls. Speech starts within 2 s of pickup, not counting the Twilio trial notice. |
| 2 | Live transcript | DC-3 | Both speakers are labelled. The median gap from a speaker stopping to the final transcript event is under 2 s. |
| 3 | Pause and ping | DC-7, DC-8 | On a broken constraint the agent calls `ask_user` and says a hold line. Filler plays about every 15 s. The call stays connected through a 45 s wait. After a reply is injected, the agent speaks it within 3 s. The receptionist rates the wait as acceptable or not. |
| 4 | No reply in time | DC-9 | After a 45 s timeout with a matching fallback, the agent takes the fallback and says why. With no match, it ends politely and the call is marked Needs follow-up. It never guesses. |
| 5 | Instruction anytime | DC-10 | An injected instruction is worked in within the next two agent turns, without talking over anyone. |
| 6 | Pause and end | DC-11 | Pause (the agent asks the other side to hold, then mutes) and end (goodbye, then hang-up) each take effect within 2 s. |
| 7 | Guardrails | DC-13 | The agent answers "are you a robot?" honestly. It pings before agreeing to a deposit. It refuses details that aren't in the brief and never invents them. If the business refuses to talk to an AI, it apologises and ends the call. |
| 8 | Ask-first field | DC-6 | A request for date of birth, an ask-first field, triggers a ping every time. |
| 9 | Status and post-call | DC-14, PO-1, PO-2, PO-4 | Every app state maps to Vapi events, or to events plus our own server state. The end-of-call report holds the full transcript, and no recording exists for the call. |
| 10 | Live takeover (P1) | DC-12 | The user hears the call live through the listen URL and can take over through a `transfer` to their own phone. Handing back to the agent is expected to be Partial or Fail. |
| 11 | Phone menus (P2) | DC-2 | One live test if the Twilio trial allows calling a menu number we control. Otherwise the probe is marked "not tested", with the reason, and the verdict comes from the docs. |

Every call also records its cost from Vapi's call record.

App states for probe 9: Dialing, In phone menu, On hold, Talking, Waiting on you and Ended (DC-14). "Waiting on you" and "On hold" are expected to come from our server state, not from Vapi.

### Go/no-go rule

- **Go** if probes 1 to 9 are all Pass, or Partial with a workaround the team accepts.
- **No-go** if probe 3 fails. The team then runs the same probes against Retell before the tech doc.
- A failure on any other probe stays a Partial. It means a named workaround or a scope cut in the tech doc, not a platform switch.

## 4. Probe kit architecture

The kit lives in `spikes/vapi/`, kept apart from `backend/` so nobody mistakes it for product code. It uses the Python standard library only, so it is easy to delete.

### Components

| Unit | Does one thing | Depends on |
| --- | --- | --- |
| Scenario files (`scenarios/*.json`) | Brief for fake user "Alex Chen": profile, sharing setting per field, goal, constraints, fallbacks. Each file has a matching receptionist script card. | Nothing |
| Assistant builder | Turns a scenario into an inline Vapi assistant config: disclosure first message, system prompt with the brief and the simple ask rule, recording off, transcripts on, the async `ask_user` tool, the chosen server events and the webhook URL and secret. | Scenario files |
| Call starter | Places one call to a verified teammate number and saves the call ID and control URLs. | Assistant builder, Vapi API |
| Webhook server | Receives Vapi events, rejects requests without the shared secret and logs each event with a local timestamp. Runs the pause-and-ping loop. | Tunnel, control URL |
| Control command | Sends an instruction, pause, end or transfer to a live call. | Control URL |
| Listen command | Saves or plays the live call audio from the listen URL. | Listen URL |
| Report script | Reads the call logs, computes the probe metrics, fetches per-call cost and prints the running total. | Logs, Vapi API |

### The simple ask rule

The prompt tells the agent to call `ask_user` when either is true:

- an offer breaks a hard constraint and doesn't match a stated fallback
- the other side asks for an ask-first field

Otherwise it acts. `ask_user` takes a `question`, a plain-language `reason` and 2–4 `suggested_replies`.

### Pause-and-ping loop

1. The agent calls `ask_user` and says a hold line, such as "Let me check with Alex, one moment."
2. The webhook server shows the ping in the operator's terminal and starts a 45 s timer.
3. Every 15 s while the ping is open, the server sends a `say` filler line through the control URL.
4. When the operator replies, the server sends `add-message` with the reply and `triggerResponseEnabled: true`, and cancels the timer.
5. On timeout, the server sends `add-message` stating that the user did not reply. The agent then applies DC-9: take a matching fallback, or end politely.

### Data flow

1. The call starter creates the call. Vapi dials the teammate through the imported Twilio number.
2. Vapi posts events to the webhook server through the tunnel. Each one is appended to `runs/<call-id>.jsonl`.
3. Pings and replies move between the operator's terminal and the call's control URL.
4. After the call, the report script turns the logs into probe results.

### Error handling

- An event with a missing or wrong secret is rejected and logged.
- A control request that fails is logged with its response and retried once. The probe records the failure either way.
- If the tunnel or server dies mid-call, the call is ended from the Vapi dashboard and the run is marked invalid, not failed.

### Secrets and data

- `VAPI_API_KEY`, `VAPI_PHONE_NUMBER_ID`, `VAPI_WEBHOOK_SECRET` and the receptionists' numbers live in `spikes/vapi/.env`, which git ignores. `spikes/vapi/.env.example` lists the names.
- `runs/` is git-ignored because it holds teammates' transcribed voices. Only the summarized results get committed.
- Briefs use only Alex's fake data.

## 5. Run protocol

### Roles

- **Operator** (the spec owner): runs the kit and plays Alex, answering pings from the terminal.
- **Receptionist** (a teammate): takes calls on their own verified phone and follows the script card for each scenario.
- **Observer** (optional teammate): times the holds and notes how the wait felt from the receptionist's side.

### Setup

1. Create Vapi and Twilio trial accounts.
2. Verify each receptionist's phone number in Twilio.
3. Import the Twilio number into Vapi.
4. Start a cloudflared quick tunnel and the webhook server.
5. Make one smoke-test call to the operator's own phone.

Everyone who takes calls agrees out loud, at the start of the session, to being transcribed. Every call also opens with the AI and transcription disclosure, in line with Massachusetts all-party consent.

### Scenarios

| Scenario | Probes | Script |
| --- | --- | --- |
| A. Restaurant booking, happy path | 1, 2, 9 | Table for 6 at 7 pm. The receptionist agrees. |
| B. Doctor reschedule, broken constraint | 3, 4 | The receptionist offers Thu 4 pm against "before 3 pm". The operator replies after 10 s, after 30 s, or not at all. No-reply runs cover a matching fallback (Fri 10 am) and no match. |
| C. Mid-call controls | 5, 6 | The operator injects "ask if they have Saturday", then pauses and ends the call. |
| D. Difficult receptionist | 7, 8 | Asks "are you a robot?", demands a deposit, asks for date of birth and a Social Security number, and finally refuses to talk to an AI. |
| E. Takeover | 10 | The operator listens in, then takes over through a transfer to their own phone. |

### Volume

| Probe | Runs |
| --- | --- |
| 1 | Checked on every call. 5 calls minimum. |
| 3 | 3 runs with a reply (10 s, 30 s, 44 s) |
| 4 | 2 runs with no reply (matching fallback, no match) |
| Each other probe | 2 runs |

About 16 calls of about 3 minutes each. One 90-minute session with two teammates, on Oct 29 or 30.

### Budget and stop rules

- Twilio trial credit covers the phone side.
- Vapi usage is estimated at $5–15. This estimate is unverified; check Vapi's pricing page during setup.
- The report script prints the running total after each call. The session stops if Vapi spend reaches $20.
- A failed probe gets one retry after a config fix. Both runs are recorded.

## 6. Deliverables

1. **This spec**, in the repo.
2. **The probe kit**, in `spikes/vapi/`, built from an implementation plan once this spec is approved. Labelled throwaway.
3. **Results report**, `spikes/vapi/RESULTS.md`:
   - the verdict and evidence for each of the 11 probes
   - measured latencies and per-call costs
   - the go/no-go call
   - "What the tech doc should say": which Vapi features to rely on, which workarounds to plan for and which risks stay open
4. **A Notion summary**, added to the "Speech ↔ Text Call API Testing" section after the spec PR merges, linking to the spec. Results are linked the same way once the report exists.

## 7. Known risks

- **Twilio trial notice.** Trial calls play a short notice before connecting. Probe 1 measures time from pickup to the agent's first word after that notice, and the report notes the notice.
- **Role-play is not a real receptionist.** A teammate may be more patient than a busy front desk. The observer's notes and the receptionist's rating are the best evidence available, and the report says so.
- **Phone menu testing may be impossible on a trial account.** Probe 11 then falls back to the docs, as section 3 says.
- **Model choice affects latency.** The kit uses one fast model for every run, recorded in the report, so results are comparable. Model tuning is out of scope.
