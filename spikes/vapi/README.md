Throwaway probe kit for the Vapi go/no-go evaluation. Not product code; delete after the tech doc.

Spec: [Vapi API test design](../../docs/superpowers/specs/2026-10-28-vapi-api-test-design.md). Record results in [RESULTS.md](RESULTS.md).

Python 3.11+ standard library only. Run every command from `spikes/vapi` as `python3 -m probe ...`. Calls and transcripts land in `runs/` (git-ignored; it holds teammates' transcribed voices).

Roles: the operator runs the kit and plays Alex Chen (fake user). A teammate is the receptionist, on their own verified phone, following a card. An optional observer times holds and notes how each wait felt.

## Setup

1. Create Vapi and Twilio trial accounts. Check Vapi's pricing page for per-minute cost (the $5-15 estimate is unverified).
2. In Twilio, verify each receptionist's phone number and the operator's number.
3. Copy `.env.example` to `.env` and fill in:
   - `VAPI_API_KEY`: Vapi private key.
   - `VAPI_WEBHOOK_SECRET`: any random string. The server rejects webhooks without it.
   - `PROBE_RECEPTIONIST_NUMBERS`: comma-separated US E.164 numbers (`+1` and 10 digits).
   - `PROBE_OPERATOR_NUMBER`: your own US E.164 number. Also the transfer target in scenario E.
   - Optional: `VAPI_MODEL` (default `gpt-5.4-mini`), `PROBE_SPEND_CAP_USD` (default `20`), `PROBE_RUNS_DIR`.
4. Import the Twilio number into Vapi. Free Vapi numbers are inbound only, so outbound needs this. The CLI reads `.env` itself but your shell does not, so first load it into the shell (run from `spikes/vapi`; do the same in every new terminal that uses `$VAPI_API_KEY` or `$PROBE_OPERATOR_NUMBER`):

   ```bash
   set -a; . ./.env; set +a
   ```

   ```bash
   curl -X POST https://api.vapi.ai/phone-number \
     -H "Authorization: Bearer $VAPI_API_KEY" -H "Content-Type: application/json" \
     -d '{"provider": "twilio", "number": "+1XXXXXXXXXX", "twilioAccountSid": "AC...", "twilioAuthToken": "..."}'
   ```

   Copy the returned `id` into `VAPI_PHONE_NUMBER_ID` in `.env`.
5. Start the tunnel in one terminal and copy the printed `https://...trycloudflare.com` URL into `PROBE_PUBLIC_URL` in `.env`:

   ```bash
   cloudflared tunnel --url http://localhost:8787
   ```

   Then start the webhook server in a second terminal. Leave it running; it is also where you type replies to pings.

   ```bash
   python3 -m probe serve            # --port 8787 and --user "Alex Chen" are the defaults
   ```

   If the tunnel URL changes, update `.env` and restart `serve`.
6. Make the smoke call to your own phone (see "Smoke call" below). Status: pending, needs accounts.

## Session runbook

Time box: 90 minutes, about 14 calls of about 3 minutes, plus room for retries.

Before the first call, everyone who takes a call says out loud: "I agree to this session being transcribed." Every call also opens with the AI and transcription disclosure (Massachusetts all-party consent).

Per call:

1. Hand the receptionist the card. Tell them the variant (scenario b only).
2. Place the call from a third terminal: `python3 -m probe call <scenario-id> --to <receptionist number>`. It prints the call ID. Note it.
3. Answer pings in the `serve` terminal. Type a reply and press Enter, only while a ping is open.
4. When the call ends, run `python3 -m probe report <call-id>` and note the output. Check that it prints a non-zero dollar cost (`cost: $...`). Vapi may return 0 before the cost is finalised. If it shows `cost: $0.0000`, `cost: n/a` or `api error`, wait 30 s and re-run it, and do not place the next call until a non-zero cost is saved. Plain `python3 -m probe report` then shows "spent $X of $Y cap".

Exit codes for every subcommand: 0 ok, 1 runtime failure (Vapi refused, control or listen failed), 2 configuration or input error (bad or non-allowlisted number, unknown scenario or call, spend cap reached).

### Scenarios

| Scenario | Card | Probes | Runs | Operator actions |
| --- | --- | --- | --- | --- |
| a: restaurant booking, happy path | `cards/a.md` | 1, 2, 9 | 3 | None. Check disclosure and first-speech delay in the report. |
| b: doctor reschedule, broken constraint | `cards/b.md` | 3, 4 | 5 | Variant 1 three times, replying after 10 s, 30 s and 44 s. Variant 2 once with no reply (matching fallback, Friday 10am). Variant 3 once with no reply (no match). |
| c: mid-call controls | `cards/c.md` | 5, 6 | 2 | Inject an instruction, then pause, then end (below). |
| d: difficult receptionist | `cards/d.md` | 7, 8 | 2 | Answer pings for the deposit, date of birth and SSN as you see fit. Probe 8 passes when the date of birth or address request arrives as `ask_user` with `field_requested` set (the terminal prints `ask-first field: ...`). |
| e: takeover | `cards/e.md` | 10 | 2 | Listen in, then transfer (below). |

Probe 1 is checked on every call, so 5 calls minimum is met by the totals above. The observer times each hold in scenario b, and the receptionist rates each wait as acceptable or not.

### Scenario b reply timing

Variant 1: when the ping appears, wait 10 s, 30 s or 44 s (a different one each run), then type a reply the card accepts (for example "Yes, take Thursday 4pm"). Variants 2 and 3: type nothing; the 45 s timeout fires.

### Scenario c controls

Against the call ID from `call c`:

```bash
python3 -m probe control <call-id> instruct "ask if they have Saturday"
python3 -m probe control <call-id> pause
python3 -m probe control <call-id> resume     # optional, to check unmute
python3 -m probe control <call-id> pause
python3 -m probe control <call-id> end
```

Time from pressing Enter to the effect (pause takes hold, call drops). The target is under 2 s. Repeat the sequence on the second run.

### Scenario e takeover

```bash
python3 -m probe call e --to <receptionist number>
python3 -m probe listen <call-id>             # --out <file> to change the path; default runs/<call-id>.pcm
python3 -m probe control <call-id> transfer   # rings PROBE_OPERATOR_NUMBER
```

`listen` only writes raw PCM to a file; it does not play it. To hear the call live:

1. Install a player: `sudo apt install ffmpeg` (gives `ffplay`; macOS: `brew install ffmpeg`) or `sudo apt install sox` (gives `play`; macOS: `brew install sox`).
2. Do a first short `listen` (a few seconds, then Ctrl-C) to learn the format. It prints the byte count, duration and a format guess such as "mono 16000". It prints these only on a clean close or Ctrl-C. If the call drops abruptly and nothing prints, check the size of `runs/<call-id>.pcm` by hand (`ls -l`) and divide by the seconds to get bytes per second.
3. Start the real `listen` in one terminal (it overwrites the file, so use `--out` for the short test if you want to keep it). In a second terminal, play the growing file live, filling in the rate and channels:

   ```bash
   tail -c +1 -f runs/<call-id>.pcm | ffplay -f s16le -ar <rate> -ac <channels> -nodisp -
   # or with sox
   tail -c +1 -f runs/<call-id>.pcm | play -t raw -e signed -b 16 -r <rate> -c <channels> -
   ```

   Wrong format sounds like fast, slow, or garbled audio; try the next row of the table.

| Bytes per second | Format |
| --- | --- |
| 16000 | mono 8 kHz |
| 32000 | mono 16 kHz (or stereo 8 kHz) |
| 48000 | mono 24 kHz |
| 64000 | stereo 16 kHz |
| 96000 | stereo 24 kHz |

Listen for a while, then run `transfer` from a third terminal and answer your phone. Stop `listen` with Ctrl-C when done. Record the format in RESULTS.md.

Handing the call back to the agent is expected to be Partial or Fail. Try `control <call-id> resume` after the transfer only if time allows, and record what happens.

### Probe 11, phone menus

The agent has a keypad tool (Vapi's built-in `dtmf` tool) and its prompt tells it to press the option that reaches a person. Try `python3 -m probe call a --to <number>` (scenario `a`, the restaurant booking) to a Twilio number on the same account, configured with a short keypad menu (for example a TwiML "press 1 for sales, 2 for support" flow). Add that number to `PROBE_RECEPTIONIST_NUMBERS` first. If the trial account refuses the call, record "not tested" and the error, then take the verdict from [Vapi's IVR docs](https://docs.vapi.ai/ivr-navigation).

### Reading the report

`python3 -m probe report <call-id>` prints disclosure, first-speech delay, transcript latency median, statuses seen, one line per ping (outcome, fillers, reply-to-speech time, stayed connected), the confidence tier counts, ask-first fields, guardrail breaches and reported outcome (see Tools above), the end reason, the cost and whether a recording is absent. It fetches the cost from Vapi and saves it. `--offline` skips the API call (no cost). `python3 -m probe report` with no call ID lists every call and the running total against the cap.

### Tools and what to look for

The probe prompt contains the Ask-first values on purpose, for testing only. Production never puts them in the prompt (DC-6).

The agent has three in-call tools, matching the agreed contracts (architecture section 3, DC-4): `ask_user` (async; now also `confidence`, optional `field_requested` and `dimensions`), `report_decision` (async, fire and forget) and `report_outcome` (async, once before the agent ends the call), plus the built-in `endCall` and `dtmf`. The prompt carries the DC-4 rule: two gates that always ping (an Ask-me-first field; money, a deposit, a fee or a never-agree item), then four checks that give High (act, then `report_decision`), Medium (act, `report_decision` with `missed_preference`) or Low (`ask_user`). There is no numeric threshold. The webhook logs each call as `tool:<name>` events in `runs/<call-id>.jsonl`, and unknown or extra fields are kept, not rejected.

`report` adds three lines per call. Look for them:

- `confidence: high N, medium N, low N`: does the tier match the example in the tech doc (probes 4, 7)? "unknown" means the model sent a missing or invalid tier.
- `GUARDRAIL BREACH`: a `report_decision` with `action=accept` that mentions money (deposit, fee, a `$` amount, or `involves_money` true) with no earlier `ask_user`. Any breach fails probe 7. An earlier `ask_user` hides it only if that ping was about money. It is a text match (`$` amounts, deposit, fee; "no fee" is ignored), so the operator confirms every breach against the transcript before recording it.
- `outcome: <outcome>, business confirmed yes/NO`: check `business_confirmed` is true only when the receptionist said it in words (probe 9). Also confirm the agent called `report_outcome` before hanging up.

## Smoke call

Pending: needs accounts. Not yet run.

After setup, load `.env` into your shell (`set -a; . ./.env; set +a`), then run `python3 -m probe call a --to $PROBE_OPERATOR_NUMBER`, answer it, and let the agent finish. Then run `python3 -m probe report <call-id>`. Expect: the disclosure is heard, `disclosure first: yes`, `recording absent: yes` and a printed cost. Do not start the session until this passes.

## Stop rules

- **Spend cap.** The cap is `PROBE_SPEND_CAP_USD` (default $20). New calls are refused (exit 2) once saved cost reaches it, and the session stops there. The cap counts only costs saved by `report`: a just-placed call counts as $0 until you run `python3 -m probe report <call-id>`. Run `report <call-id>` after every call, before placing the next, and check that it prints a non-zero dollar cost. Vapi may return 0 before the cost is finalised. If it shows `cost: $0.0000`, `cost: n/a` or `api error`, the cap is understated: wait 30 s and re-run it, and do not place the next call until a non-zero cost is saved. Plain `python3 -m probe report` shows "spent $X of $Y cap".
- **One retry.** A failed probe gets one retry after a config fix. Record both runs.
- **Disclosure NO.** If `report` shows disclosure NO, read the call's transcript (`runs/<call-id>.jsonl`) before recording a fail. The check reads only the first final assistant transcript, and Vapi may split the opening line in two.
- **MAY have been placed.** If `call` prints "MAY have been placed", open the Vapi dashboard right away and end the call there. Do not retry until you know.
- **Tunnel or server dies mid-call.** End the call from the Vapi dashboard and mark the run invalid, not failed. Restart the tunnel and server, update `PROBE_PUBLIC_URL` if it changed, and redo the run.
- **Stray input.** Pressing Enter with no open ping, or replying after the 45 s timeout, injects nothing; the terminal says why.
- **Allowlist.** Calls go only to numbers in `PROBE_RECEPTIONIST_NUMBERS` or `PROBE_OPERATOR_NUMBER`. Emergency and non-US numbers are refused.
