# Product requirements (snapshot)

> **Read-only snapshot. Notion is the master copy.**
> Source: the "Product Requirements" section of the [NU Launch Labs Outbound Call Agent](https://app.notion.com/p/NU-Launch-Labs-Outbound-Call-Agent-402b6bc9644c80d7ad0ae5636298240f) Notion page.
> Snapshot taken 2026-11-04 from the Notion version last updated Oct 26, 2026 (owners: Coco, Lotto · status: draft for team review). Refreshed 2026-11-05 with per-requirement owners and the System Architecture section. Refreshed 2026-11-06 with the tech doc decisions (ON-3, ON-5, DC-4, DC-11). These are not in Notion yet, because the workspace is at its free block limit.
>
> To change a requirement, edit it in Notion first, then refresh this file in a `docs` PR. If the two disagree, Notion wins.
> Requirement IDs (`ON-`, `PC-`, `DC-`, `PO-`) are stable: specs, tests and code cite them, so never renumber or reuse one.

**Priorities:** P0 = must work in the demo, P1 = build if time allows, P2 = later / flag in the presentation. This PRD covers the early-December demo, not a public release.

## Overview

**Why this section:** this section is the one-page picture: the problem, who it's for, what makes us different, and what's in or out of the demo. The sections below take each part of the flow in turn, with a scenario, the decisions made, and the requirements.

| Section | Summary |
|---|---|
| Problem | Some things still need a phone call (booking, cancelling, rescheduling), but people are in class or at work and can't sit on hold or talk it through. Today's AI callers either hang up when they get stuck (Wajo) or make you take over the call live (Gemini). |
| Target user | Students and young professionals who are busy at a laptop (in class, at work) and can glance at a browser notification but can't talk. |
| Product | A web app that places calls for you. It starts each call with your context, pauses instead of hanging up when it's missing info, and pings you for a one-tap reply. Human-in-the-loop (HITL) controls run throughout. |
| Our edge | **Pause, don't hang up.** The call stays live while you answer a ping. Every decision is driven by a confidence score, and the agent explains why it acted or asked. No competitor publicly offers mid-call ask-and-wait (Gemini: live takeover only; Muse: nothing published; Instinct: pushes through without asking). Other gaps: no device lock-in (any browser) and per-field privacy control. |
| In scope | Custom calls plus templates (restaurant book/cancel, doctor book/cancel/reschedule). Desktop browser on a laptop. Browser push notifications. English, US numbers. |
| Out of scope | Bank and financial calls (too much identity verification, banks block AI callers). Payments. Emergency numbers. Native phone app and SMS. Calls to personal (non-business) numbers. Audio recording storage. |
| Stretch (P1) | Calendar integration (the AI checks your schedule and books into open slots), live voice takeover, the user's own number as caller ID. |
| Flag in presentation, not built | Data retention (proposal: transcripts auto-delete after 30 days, one-click delete-all). Encryption at rest beyond Supabase defaults. Multi-state AI-disclosure compliance review. Mobile / SMS pings. |

## 1. Onboarding

**Scenario:** Alex, a junior at Northeastern, signs up between classes. In about two minutes they enter the basics once (name, phone, date of birth, address), decide which fields the agent can share freely and which need their OK, and allow browser notifications so the agent can reach them mid-call. They never type those details again.

**Decisions made**

- **Collect the profile once and reuse it on every call.** "Never repeat yourself" is the promise. Gemini makes you give the details on every request.
- **Each field is set to "Share freely" or "Ask me first", and DOB, address and insurance default to Ask me first.** This is finer control than competitors' all-or-nothing access, and Meta Muse and Instinct both drew privacy backlash.
- **Explain up front that the agent says it's an AI and that calls are transcribed.** Users aren't surprised later, and it sets up the pings.
- **Ask for notification permission during onboarding, with a test ping.** A missed ping means a dropped call. The MVP is laptop and browser only, with no SMS or phone app.
- **The extended profile (insurance, providers, availability) is optional and P1.** It shouldn't block the first call.

**Requirements**

| ID | Requirement | Details / acceptance criteria | Priority | Notes for design & eng | Owner |
|---|---|---|---|---|---|
| ON-1 | Sign up and sign in | Email sign-in via Supabase Auth. A user only ever sees their own profile and calls (RLS). | P1 | Eng: RLS on every table (see CONTRIBUTING). |  |
| ON-2 | Explain how the agent works | Before the profile step, show the user: the agent says it's an AI, says the call is transcribed, calls from our number, and pauses to ask you when unsure. The user acknowledges once. | P1 | Design: short, 1 screen. Sets expectations for pings. |  |
| ON-3 | Basic profile | Name, calling name, phone, email, date of birth, address. Required: name, calling name and phone. The calling name is how the agent introduces who it is calling for (DC-1). It defaults to the full name and can be edited (e.g. "Alex" or "Alex C."). | P0 | Collect once, reuse everywhere. The profile UI explains the calling name is always said on calls. | Steve |
| ON-4 | Extended profile | Typical availability windows, default party size, dietary needs, doctors/providers (name, office, phone), insurance (carrier, member ID). All optional, and can be added later or mid-flow. | P1 | Design: progressive. Don't block first call on this. |  |
| ON-5 | Per-field sharing setting | Each field is set to **Share freely** or **Ask me first**. Defaults: DOB, address, insurance = Ask me first. Others = Share freely. Exception: the calling name is always shared and can't be set to Ask me first, because DC-1 says it in the first sentence. | P0 | Core trust feature. During a call, ask-first fields always trigger a ping (DC-6). | Steve |
| ON-6 | Browser notification permission | Ask for notification permission with a clear reason ("so we can ask you mid-call"), then send a test notification. If the user denies it, warn that pings only show in the open tab. | P0 | Eng: Web Push / Notification API. Design: handle the denied state. | Steve |
| ON-7 | Edit profile anytime | Profile page to edit fields and sharing settings. Changes apply to future calls. | P0 |  | Steve |

## 2. Pre-call

**Scenario:** Alex needs to move a doctor's appointment. They pick the Doctor → Reschedule template. (Or they type a custom goal, like "Call Tatte and ask if they can hold a table for 6 at 7pm".) The app fills in what it knows from the profile and calendar, asks only for what's missing ("Which office?", "Any times that won't work?"), and asks for a fallback. Then it shows a review card. Alex checks it in about 10 seconds and taps Start.

**Decisions made**

- **Custom calls are the core, and templates are shortcuts set up as config.** The demo shows the overall flow, not one use case. Templates cover the common restaurant and doctor calls.
- **Gather context before dialing, and ask only for what's missing.** Every gap left now becomes a mid-call ping, or a dropped call.
- **Constraints and fallbacks are first-class inputs.** They're what the confidence score checks against, and a good fallback lets the agent act without bothering the user.
- **The pre-call review is the last checkpoint.** It shows exactly what the agent may share and say, before anything happens.
- **The calendar (read-only, P1) fills in availability automatically.** The user doesn't have to type their schedule.
- **The UI (chat, form or a mix) is left to the designer.** These requirements define what must be gathered, not how.

**Requirements**

| ID | Requirement | Details / acceptance criteria | Priority | Notes for design & eng | Owner |
|---|---|---|---|---|---|
| PC-1 | Start a call: template or custom | The entry point offers templates and a "Custom call" option. | P0 | Designer decides the UI (chat, form, or hybrid). | Lotto |
| PC-2 | Templates | P0: Restaurant (book), Doctor (book). P1: Restaurant (cancel/modify), Doctor (cancel/reschedule). Each template defines its required fields, the likely questions the other side will ask, and the default constraints. | P0 | Templates are config, not code. Easy to add more later. | Harper |
| PC-3 | Custom call | User enters the business name, phone number and goal in plain words. The system turns this into a structured call brief. | P0 | This is the general case. Templates are shortcuts. | Lotto |
| PC-4 | Gather missing context | The system builds a call brief from goal + profile + template. It detects missing required info and asks the user for it before the call (e.g. "Which doctor?", "New or existing patient?"). | P0 | Design: how we ask is up to the designer. Requirement: ask only for what's missing, and never re-ask for anything already in the profile. | Lotto |
| PC-5 | Constraints and fallbacks | User can set hard constraints (e.g. "before 3pm"), preferences, ranked acceptable alternatives ("else Tue afternoon"), and never-agree items (e.g. "no deposit"). | P0 | The confidence score (DC-4) checks every decision against these. Prompt for at least one fallback. | Lotto |
| PC-6 | Pre-call review | Show the goal, the number to dial, the opening line, what the agent may share (freely vs. ask first), constraints and fallbacks. The user can edit, then Start. | P0 | Design: the final checkpoint. Must be scannable in ~10 seconds. | Lotto |
| PC-7 | Number validation | US numbers only. Block 911 and other emergency numbers. | P0 |  | Lotto |
| PC-8 | Schedule for later / business hours | Warn if the business is likely closed. Option to schedule the call. | P2 |  |  |
| PC-9 | Calendar availability | Connect Google Calendar (read-only). Before the call, the user's free slots fill in the time constraints automatically. During the call, the agent checks any offered time against the calendar before accepting it (e.g. "They offered Tue 2pm, and you have class then"). | P1 | Connect in onboarding or the first time it's needed. A calendar conflict counts as a broken constraint in confidence scoring (DC-4). |  |

## 3. During call

**Scenario:** The agent dials. It says it's an AI assistant calling for Alex and that the call is transcribed, then asks for Thursday morning. The receptionist offers Thursday at 4pm, but Alex said before 3pm. The agent says "Let me check with Alex, one moment", and Alex's laptop shows: "They offered Thu 4pm. You said before 3pm." with buttons [Accept 4pm] [Ask for Fri] [Decline]. Alex taps Ask for Fri from a lecture, and the call carries on without hanging up. If Alex doesn't reply within ~45s and Fri 10am matches the fallback, the agent takes it. If nothing matches, it ends the call politely and marks it Needs follow-up.

**Decisions made**

- **Pause and ping instead of hanging up or making the user take over live.** This is our edge. Gemini makes you take over the call yourself, and the agent Coco tested hung up.
- **A three-tier confidence score drives every decision, and the agent re-scores whenever the other side counters.** That gives a predictable rule for when to act and when to ask. An offer that matches a stated fallback scores High.
- **Hide the score and show the reason instead.** A number is hard to act on. A reason like "They offered 4pm, you said before 3pm" can be read at a glance.
- **No reply and low confidence means end the call, never guess.** A wrong booking is worse than a callback. If the other side rejects the fallback, the agent re-scores, and if nothing fits, it ends the call.
- **Fields marked Ask me first always trigger a ping,** however confident the agent is.
- **Always disclose the AI and the transcription in the first sentence, and never use hidden humans.** Massachusetts requires every party's consent to recording, and Meta Muse drew backlash for both.
- **Four interventions:** one-tap reply, send an instruction anytime, and pause/end are P0. Live voice takeover is P1 because it's the hardest to build.

**Requirements**

| ID | Requirement | Details / acceptance criteria | Priority | Notes for design & eng | Owner |
|---|---|---|---|---|---|
| DC-1 | Opening disclosure | The first sentence names who we're calling for, says it's an AI assistant, and says the call is transcribed. E.g. "Hi, I'm an AI assistant calling on behalf of Alex Chen. This call is transcribed. I'd like to book a table for..." | P0 | Massachusetts requires every party's consent to recording/transcription (open notice at the start). Meta Muse lost trust by not disclosing. | Coco |
| DC-2 | Phone menus and hold | Navigate IVR menus (DTMF), wait on hold, detect when a human picks up. | P2 | Eng: check Vapi support. Many doctor offices use IVR. |  |
| DC-3 | Live transcript | The web app streams the transcript with speaker labels (Agent / Business / You), with under ~2s delay. | P0 |  | Coco |
| DC-4 | Confidence-driven decisions | For every question or offer from the other side, the agent scores its candidate response against the brief (goal, profile, constraints, fallbacks).<br>**High:** act and log it.<br>**Medium:** every constraint is met but a preference is missed. Act, and flag the miss in the live feed.<br>**Low:** stall and ping, and if there's no reply, end the call (DC-9). When the other side counters or rejects a fallback, re-score the new offer. An offer that matches a stated fallback scores High. Ask-me-first fields and money (a deposit, a fee or a never-agree item) always ping, whatever the score. | P0 | Core differentiator. The rubric (2 gates, 4 checks, no numeric threshold) is in the [tech doc](tech-doc-during-call.md). Log every score for debugging. | Coco |
| DC-5 | Explain decisions, hide the score | The score is never shown. Every action and ping shows a plain-language reason, e.g. "They offered 4pm. You said before 3pm." or "Matched your fallback: Tue 2pm." | P0 | Design: the reason is the main content of the ping card. | Coco |
| DC-6 | Ask-first fields always ping | If the agent needs an Ask-me-first field (e.g. insurance ID), it pings the user regardless of confidence. | P0 | Shows up in the post-call "what was shared" log. | Coco |
| DC-7 | Pause and ping, keep the line open | While waiting, the agent uses a natural hold script ("Let me check with Alex, one moment") and reassures the other side every ~15s. Default timeout is 45s, to be tuned from test calls. | P0 | The biggest unvalidated assumption: will a receptionist wait? Test this early with role-play calls. | Coco |
| DC-8 | Ping delivery and reply | Browser push notification plus an in-app card: the question, the reason, 2–4 suggested one-tap replies, and a free-text box. Clicking the notification focuses the live-call tab. | P0 | Laptop/browser only for the MVP. Design: must work from a glance.<br>Free text box → need to clean up user’s input and convert to a message | Coco |
| DC-9 | No reply in time | On timeout: if a High-confidence fallback fits, use it and log the reason. Otherwise end politely ("I'll confirm with Alex and call back"), and mark the call Needs follow-up. Never guess on Low confidence. | P0 |  | Coco |
| DC-10 | Send an instruction anytime | The user can type an instruction (e.g. "ask if they have Saturday") and the agent works it in at the next natural turn. | P0 |  | Lotto |
| DC-11 | Pause agent / end call | Pause: the agent asks the other side to hold. While paused, an open ping's timer stops and picks up where it left off on Resume. The agent keeps the business informed with a short reassurance every ~15s, and after 2 min paused (to be tuned from test calls) it offers to call back and ends politely as Needs follow-up. End: the agent says a polite goodbye and hangs up. | P0 |  | Coco |
| DC-12 | Live voice takeover | The user joins from their laptop mic (WebRTC). The agent goes silent and can be handed back control. | P1 | Hardest piece. Eng: check whether Vapi supports a live listen/join. |  |
| DC-13 | Guardrails | Never claim to be human. Answer "are you a robot?" honestly. Never share info that isn't in the brief, and never invent details. Never agree to payments, deposits or fees without a ping. If the business refuses to talk to an AI: apologise, end, and mark the call "Refused AI". | P0 |  | Lotto |
| DC-14 | Call status | Visible states: Dialing, In phone menu, On hold, Talking, Waiting on you, Ended. | P0 |  | Coco |
| DC-15 | Call limits | Max call length (e.g. 15 min) and a cap on calls per day for each user, to control cost. | P1 |  |  |

## 4. Post-call

**Scenario:** When the call ends, Alex gets a notification: "Booked: Fri 10am with Dr. Lee, confirmation #4821." The summary shows what was agreed, which decisions the agent made and why, and every field it shared (e.g. "DOB, approved mid-call"). The appointment is added to Alex's Google Calendar. If the call ended without a booking, Alex can retry with a new fallback in one tap.

**Decisions made**

- **Every call ends with a clear status** (Booked / Cancelled / Rescheduled / Needs follow-up / Failed). The user should see at a glance whether they need to do anything.
- **Show what was shared.** This is the trust lesson from Meta Muse: users see exactly which of their info was given out.
- **Keep the transcript and summary, not the audio.** That's less sensitive data to secure, and still enough to review the call.
- **Retry and follow-up calls reuse the context (P1).** "Never repeat yourself" applies after a failed call too.
- **Confirmed bookings go to the calendar (P1),** so the loop closes without any manual step.

**Requirements**

| ID | Requirement | Details / acceptance criteria | Priority | Notes for design & eng | Owner |
|---|---|---|---|---|---|
| PO-1 | Outcome status | Booked / Cancelled / Rescheduled / Needs follow-up / Failed (no answer, refused AI, wrong number). | P0 | Written only by the backend. | Harper |
| PO-2 | Summary | What was agreed (date, time, confirmation number, staff name), decisions the agent made with their reasons, and open items. | P0 |  | Harper |
| PO-3 | What was shared | A list of every profile field disclosed on the call | P0 | Trust lesson from Meta Muse. | Harper |
| PO-4 | Transcript | The full transcript is saved and viewable. No audio is stored. | P0 |  | Harper |
| PO-5 | Post-call notification | A browser notification when the call ends, with the outcome. | P0 |  | Harper |
| PO-6 | Call history | List of past calls with. Open any call to see its summary and transcript. | P0 |  | Harper |
| PO-7 | Retry / follow up | "Call again", either reusing the brief or with an adjusted brief (e.g. new fallback). Follow-up calls carry over context from the earlier call. | P1 |  |  |
| PO-8 | Save new info to profile | Offer to save info learned on the call (e.g. patient ID, the office's preferred number). | P1 |  |  |
| PO-9 | Add to calendar | Once a booking is confirmed, add it to the connected Google Calendar. If no calendar is connected, offer an .ics download. | P1 | Needs write access to the calendar, on top of PC-10's read access. |  |

## 5. System Architecture

The full blueprint is [docs/architecture.md](architecture.md) (status: Proposed, for the tech doc). In short: a browser web app talks to one backend service, which drives the call through Vapi on our Twilio number. Supabase provides Postgres, Auth and Realtime, an LLM API builds the pre-call brief and the post-call summary, and Web Push delivers pings.

**Decided (Coco):** the profile has a required, always-shared calling name (ON-3, ON-5), because DC-1 says it in the opening line. The technical design for the During-call requirements is in [docs/tech-doc-during-call.md](tech-doc-during-call.md).

## AI Disclosure examples

1. "Hi there, I’m a digital assistant calling to book a table for **[Your Name]**. Just so you know, my system is transcribing this to log the confirmation for them.

   [Task] Do you have anything open for a party of [Number] this Friday?"

1. "Good afternoon, I am a virtual concierge booking on behalf of our client, **[Your Name]**. I am transcribing this line to ensure accuracy for their schedule.

   [Task] Are you currently accepting reservations for next week?"

## Known gaps in the source

Copied as written above; to be fixed in Notion by the owners, then refreshed here.

- **PO-6** is cut off: "List of past calls with."
- **PO-9** cites "PC-10's read access". Calendar read access is **PC-9**; there is no PC-10.
