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

Evidence: call IDs and metrics from `report`.

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
