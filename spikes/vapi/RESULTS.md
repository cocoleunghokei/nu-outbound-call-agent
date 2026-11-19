# Vapi probe results

Spec: [Vapi API test design](../../docs/superpowers/specs/2026-10-28-vapi-api-test-design.md). Verdicts are Pass, Partial or Fail. Fill in after the session.

Session date:
Operator:
Receptionists:
Observer:
Model (`VAPI_MODEL`):

## Checklist

- [ ] Smoke call passed (disclosure heard, `recording absent: yes`, cost printed)
- [ ] Probe 1 checked on at least 5 calls
- [ ] Probe 3 run 3 times with a reply (10 s, 30 s, 44 s)
- [ ] Probe 4 run 2 times (matching fallback, no match)
- [ ] Each other probe run at least 2 times
- [ ] Probe 11 tested, or marked "not tested" with the reason
- [ ] `report <call-id>` run after every call; total spend recorded below
- [ ] Go/no-go line filled in
- [ ] "What the tech doc should say" filled in

## Probe verdicts

| Probe | Requirements | Verdict | Evidence | Workaround |
| --- | --- | --- | --- | --- |
| 1. Outbound call and disclosure | DC-1 | | | |
| 2. Live transcript | DC-3 | | | |
| 3. Pause and ping | DC-7, DC-8 | | | |
| 4. No reply in time | DC-9 | | | |
| 5. Instruction anytime | DC-10 | | | |
| 6. Pause and end | DC-11 | | | |
| 7. Guardrails | DC-13 | | | |
| 8. Ask-first field | DC-6 | | | |
| 9. Status and post-call | DC-14, PO-1, PO-2, PO-4 | | | |
| 10. Live takeover (P1) | DC-12 | | | |
| 11. Phone menus (P2) | DC-2 | | | |

Evidence: call IDs and metrics from `report`. For probes 4, 7, 8 and 9 cite its `confidence`, `ask-first fields`, `GUARDRAIL BREACH` and `outcome` lines.

### Probe 9: app state map

| App state | Source (Vapi event or our server state) | Expected source | Observed (yes/no) |
| --- | --- | --- | --- |
| Dialing | | Vapi `status-update` queued / ringing | |
| In phone menu | | | |
| On hold | | Our server ping state | |
| Talking | | | |
| Waiting on you | | Our server ping state | |
| Ended | | | |

## Latency and cost

| Call ID | Scenario / variant | Disclosure first | First speech delay (s) | Transcript latency median (s) | Reply to speech (s) | Ended reason | Cost (USD) | Valid run |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | |
| | | | | | | | | |
| | | | | | | | | |

Total spend:
Average cost per call:
Twilio trial notice (probe 1 timing excludes it):

## Probe 3: the waits

| Call ID | Reply after (s) | Fillers heard | Stayed connected | Reply to speech (s) | Receptionist rating (acceptable or not) | Observer notes on the wait |
| --- | --- | --- | --- | --- | --- | --- |
| | 10 | | | | | |
| | 30 | | | | | |
| | 44 | | | | | |

A teammate may be more patient than a busy front desk. The ratings and notes are the best evidence available; say so in the tech doc.

## Probe 10: takeover

| Field | Value |
| --- | --- |
| Heard live (yes/no) | |
| Rough delay vs the phone | |
| Audio intelligible (yes/no) | |
| Format inferred (bytes per second, mono or stereo, rate) | |
| Takeover via transfer worked (yes/no) | |
| Time from `transfer` to the operator's phone ringing | |
| Hand-back to the agent | |

## Go/no-go

Rule: Go if probes 1 to 9 are all Pass, or Partial with a workaround the team accepts. No-go if probe 3 fails; then run the same probes against Retell before the tech doc. A failure on any other probe stays a Partial (a named workaround or scope cut), not a platform switch.

Verdict (Go / No-go):
Reason:

## What the tech doc should say

- Vapi features to rely on:
- Workarounds to plan for:
- Risks that stay open:

## First real call from the app (2026-11-18)

The tables above are the probe-kit session. This is the first call placed end to end through the app itself: anonymous session in the browser → profile → brief → `POST /calls` → Vapi → the phone. Deployed stack at the time: `main` @ `374564b` (#82) on Railway, Vercel site, hosted Supabase. Setup and the fixes it took are in #77 (closed 2026-11-19).

| Field | Value |
| --- | --- |
| Placed at | 2026-11-18 23:36 UTC, from the chat on the Vercel site |
| Backend call id | `0b58767c-a507-4d6c-a1b7-ad2312fea922` |
| Vapi call id | `01a1f105-3c1b-7aaf-8cf9-d37aa9852a7d` |
| Destination | a team member's phone, on `CALL_DESTINATION_ALLOWLIST` (number not recorded here) |
| Model | `deep-seek/deepseek-flash` (decision 7; `deepseek-chat` was retired by DeepSeek on 2026-07-24) |
| Rang and spoke | yes: the phone rang from +1 231-359-4180 and the agent spoke the DC-1 opening line |
| Duration | 37 s |
| Ended reason | `assistant-ended-call`: the agent reported the outcome and hung up itself |
| Tool calls seen by Vapi | `report_outcome`, then `endCall` |
| Cost (Vapi) | USD 0.0523 |
| Recording | off (`artifactPlan.recordingEnabled: false`); transcript on, 10 lines |

What worked: the whole path from the browser to the ringing phone, the disclosure line, DeepSeek answering through Vapi, and tool calling on DeepSeek for `report_outcome` and the built-in `endCall`. That settles the "tool calling on DeepSeek is unverified" part of decision 7 for the outcome tool.

What this call did not exercise: `ask_user` and `report_decision` (the goal needed no decision at a gate), the live page (it still played the demo until #83) and the history screen (#84). Still open from #77: a second Start during a running call (expected `409 call_in_progress`).

What it took to get here, all found on this call and fixed the same day: `PUBLIC_BASE_URL` needs its `https://` (503 `calls_not_configured`), Vapi rejects a JSON Schema `title` inside tool parameters and the retired `deepseek-chat` name (#82), and Vapi has no DeepSeek keys of its own (the team's key is connected under Integrations). A refusal's reason now reaches the Railway log (#86).
