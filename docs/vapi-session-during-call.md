# Vapi session: During-call add-on

**Owner:** Coco · **Updated:** 2026-11-10 · **For:** whoever runs the Vapi probe session

The probe session already has a full plan: the [probe kit runbook](../spikes/vapi/README.md#session-runbook), the receptionist cards in `spikes/vapi/cards/` and the results sheet in [spikes/vapi/RESULTS.md](../spikes/vapi/RESULTS.md). This page adds only what the During-call build decided after that plan was written. Each item names the open decision it settles, what to do, what counts as a pass, and what we change if it fails. The decisions are listed under "Build decisions" in [tech-doc-during-call.md](tech-doc-during-call.md), added by PRs #24 and #26. The `backend/app/calls/` files named below come from the same PRs.

It adds about 4 calls (12 minutes) to the 90-minute session. Run it after the main probes, while the receptionists are still available.

## Before the session

| # | What | Who | Why |
|---|---|---|---|
| P1 | Add the DeepSeek API key in the Vapi dashboard (Integrations) | Coco (key from Lotto) | The agent runs on DeepSeek-V3 (decision 7). Without the key, DeepSeek calls fail. |
| P2 | Let the probe kit choose the model provider. Today `spikes/vapi/probe/assistant.py` always sends `"provider": "openai"`; `VAPI_MODEL` only changes the model name. Add a `VAPI_MODEL_PROVIDER` setting (default `openai`) so the kit can send `deep-seek` with `deepseek-chat`. | Probe kit owner (Steve) | Needed for D1. |
| P3 | Add a `say` action to `python3 -m probe control` that sends only `{"type": "say", "content": "<text>"}`. Today the kit's `pause` always sends the hold line and the mute together, and there is no way to speak one line while muted. | Probe kit owner (Steve) | Needed for D2 and D4. |

If P2 or P3 isn't ready, skip the item that needs it and write "not tested" in the results.

## D1. Does DeepSeek make the tool calls? (decision 7)

The agent pings the user by calling `ask_user`, and logs its own decisions with `report_decision`. Vapi's docs don't say whether DeepSeek handles tool calls reliably, and the main probes run on OpenAI.

**Do:** set `VAPI_MODEL_PROVIDER=deep-seek` and `VAPI_MODEL=deepseek-chat`. Rerun scenario b (variant 1, reply after 10 s) twice and scenario d once.

**Pass:**
- Every call where the receptionist breaks a constraint or asks for the date of birth or the deposit produces an `ask_user` in `report`.
- No tool call has malformed arguments. Check the `serve` terminal's webhook log.
- The agent stays silent after `ask_user`, then uses the reply.
- Reply to speech is within about 1 s of the OpenAI runs.

**If it fails:** switch the agent back to an OpenAI model. It's one line, `MODEL` in `backend/app/calls/assistant.py`. Record which tool calls were missed.

## D2. Can the agent speak while muted? (decision 2)

During a pause, the backend reassures the business every 15 s. It sends unmute, then the line, then mute. If Vapi plays a `say` even while the assistant is muted, the unmute and mute aren't needed, and the model can't slip in its own words in between.

**Do:** in scenario c, after `pause`, wait 5 s, then `python3 -m probe control <call-id> say "Thanks for holding, just a moment longer."`

**Record:** did the receptionist hear the line? (yes / no)

**If yes:** simplify the reassurance to a plain `say` (backend change in `app/calls/commands.py`).
**If no:** keep unmute, say, mute. Check in D4 that the model doesn't add words of its own during the unmuted moment.

## D3. Does the hold line finish before the mute? (decision 3)

The kit's `pause` sends the hold line and the mute back to back. The backend instead mutes 0.7 s after the agent stops talking, or after 4 s at the latest (the quiet rule), so that the mute never cuts the hold line off.

**Do:** in scenario c, run `pause` twice: once while the agent is silent, and once in the middle of an agent sentence.

**Record:** was "Could you hold for just a moment, please?" heard in full, both times? Note any cut-off words.

**If it's cut off:** this confirms the quiet rule is needed; nothing to change.
**If it's never cut off:** the quiet rule is a safety margin, not a necessity; keep it anyway.

## D4. Will a receptionist hold through a long pause? (DC-11 pause cap)

The pause cap is 2 minutes (to be tuned from test calls). Probe 3 covers the 45 s ping wait, but no probe covers a long user pause.

**Do:** one call on card c:
1. Pause once the receptionist has answered.
2. Send the reassurance line with `say` (or with `resume`, `say`, `pause` if D2 failed) at 15 s, 30 s, 45 s, 60 s, 75 s, 90 s and 105 s.
3. At 120 s, send `end`.

The receptionist plays a normal, busy front desk.

**Record:** how long until the receptionist says they need to go, or hangs up. Their rating: acceptable, borderline or not acceptable, plus a comment.

**Then:**
- If they hang up before 120 s, lower `PAUSE_CAP_S` in `app/calls/commands.py` to just below where they gave up.
- If 120 s felt fine, keep it.

## Results

Add these rows to `spikes/vapi/RESULTS.md` under the probe verdicts:

| Check | Decision | Verdict | Evidence (call IDs, notes) | Change needed |
|---|---|---|---|---|
| D1. DeepSeek tool calls | 7 | | | |
| D2. `say` while muted | 2 | | | |
| D3. Hold line not cut off | 3 | | | |
| D4. Long pause, 2 min cap | DC-11 | | | |

Then update the "Build decisions" table in `tech-doc-during-call.md`: mark decisions 2 and 7 as decided, with what the session showed.
