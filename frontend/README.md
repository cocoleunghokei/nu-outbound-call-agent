# Frontend

The web app: onboarding, profile, create call, live call (transcript, pings, HITL controls), end-of-call summary and call history.

React + TypeScript, built with Vite. Talks to Supabase through the client in `src/lib/supabase.ts`.

## Run it locally

Needs Node 20.19 or newer (Vite 8 and eslint 10 require it; 22 or newer avoids a supabase-js engine warning).

```bash
cd frontend
npm install
cp .env.example .env.local   # then paste the anon key from the Supabase dashboard
npm run dev
```

Open http://localhost:5173.

## Layout

- `src/App.tsx`: root component, start here. It picks the screen for the current address with `useRoute()`.
- `src/router.ts`: the app's small router on the browser History API (no routing library). Routes: `/` chat home, `/live?call=<call_id>` live call (no `call`: the scripted demo call), `/history` past calls. Screens never touch `window.history`: they call `navigate(path)`, and Back / Forward just work. Add a route in `routeOf`, a `Route` case, and a `case` in `App.tsx`.
- `src/lib/supabase.ts`: shared Supabase client (anon key only; every table it reads needs row-level security)
- `src/index.css`: global styles
- `src/precall/`: pre-call flow (PC-1 to PC-7). `types.ts` copies the `brief.v1` contract shapes from PR #19 (goes away once that merges) and drafts the template shape (to agree with Harper). `fakeBriefs.ts` holds a fake profile, the two P0 templates and example briefs shaped like the `briefs` table, until the backend is connected. `contractExample.ts` is a copy of `contracts/examples/brief.v1.valid.json` that the tests compare against. `phone.ts` is the PC-7 number check (US area codes only, emergency and N11 short codes blocked before normalising); the backend mirrors these rules. `missing.ts` works out which template fields are still missing (PC-4): it never asks for a field the profile has, and a profile field typed in the chat is saved to the profile with ON-5 default sharing, never put in the brief. `brief.ts` turns a finished chat draft into a `brief.v1` brief plus `missing_fields` (field keys only; Ask-me-first fields are listed, never referenced; an empty fallbacks list never blocks Start). `chat.ts` is the chat home logic (PC-1, PC-3, PC-4, PC-5: limits, preferences, ranked backups with a one-time nudge if skipped, never-agree items) as a pure reducer, and `ChatHome.tsx` renders it with styles copied from the prototype. `ReviewCard.tsx` is the PC-6 "Check before I dial" card: the opening line comes from `openingLine()` in `brief.ts`, which mirrors the backend's `build_first_message`; each row's Edit re-asks just that part in the chat, and Start stays disabled while `missing_fields` isn't empty. `api.ts` is the typed client for the FastAPI routes (`POST/GET/PATCH /briefs`, and Coco's `POST /calls {brief_id}`): `Authorization: Bearer <Supabase access token>` on every request, an `Idempotency-Key` on every POST/PATCH, and the shared `{"error": {code, message, retryable}}` shape thrown as `ApiError`. `start.ts` is what Start does: save the brief (POST the first time, PATCH after an edit, nothing if unchanged), let the server's `missing_fields` and opening line win, then `POST /calls`; if that route isn't deployed yet (404/405) it says "Saved your brief. Calling isn't connected yet." and logs the `brief_id`. If `POST /calls` answers 201 with `status: "failed"` (the phone service refused the call, so nothing rang), it says "The call didn't go through, so nobody was called." and drops that request's key, so the next Start places a new call instead of replaying the failed one; `creation_uncertain` counts as started. If `POST /calls` gets no answer at all, the call may already be ringing: it says so and keeps the key, so Start again replays that call instead of dialing twice. Idempotency keys are kept per request in the draft's `SaveSession`, so retrying the same action reuses its key. `mode.ts` picks demo or API mode (below).
- `src/live-call/`: live-call page (DC-3 transcript, DC-14 status, DC-8 ping card, DC-11 Pause/Resume/End, DC-4/DC-5 decision feed). `state.ts` folds the call's `call_events` rows into what the page shows; `ping.ts` holds the ping rules (reply clock, free-text cleanup, no free text on Ask-me-first pings). `controls.ts` holds the Pause/Resume/End rules and the feed labels. `mockCall.ts` plays a scripted fake call and answers ping replies and commands like the backend will, until the backend is connected. `backend.ts`, `follow.ts` and `useRealCall.ts` follow a real call instead: `follow.ts` is the start-up sequence without React (sign the browser in, subscribe, load, reload on every reconnect), `useRealCall.ts` wires it to state; events load from Supabase and stream in through Realtime, replies and commands go to the backend API. `types.ts` copies the contract shapes from PR #19 and goes away once that merges.
- `public/prototype/`: static, clickable design prototype, served as is at `/prototype/`. It isn't part of the React app, so don't import from it.

## Demo mode and API mode

- **Demo mode** (default): `VITE_API_URL` is empty, Supabase isn't configured, or the anonymous sign-in fails. The pre-call flow runs on fake data, Start only logs the brief, and the top bar shows a small "Demo mode" note that says why.
- **API mode**: `VITE_API_URL` is set (e.g. `http://localhost:8000`) and the browser has a Supabase session. There is no sign-in screen: every real screen signs the browser in anonymously on first use through `ensureSignedIn()` in `src/lib/accessToken.ts` (over `src/lib/session.ts`; the hosted project and `supabase/config.toml` need anonymous sign-ins enabled), so the chat, the profile, saved briefs and calls belong to this browser, whichever page it opens first. The first visit shows `src/precall/OnboardingStep.tsx` over the chat: full name, calling name, phone and email (unverified), written to `profiles` and `profile_fields` through the Supabase client under RLS (`src/lib/profile.ts`), with `onboarded_at` stamped (ON-3, ON-8). A returning browser skips it; clearing site data starts over with a new user and an empty profile. The chat then runs on those rows (`src/precall/useProfile.ts` loads them), the top bar shows the email, and Start before the form is saved opens it again. Start saves the brief to FastAPI and hands it to `POST /calls`, then opens `/live?call=<call_id>` from its answer (demo mode opens `/live`, the demo call). The server checks the brief against the same profile rows, and its answer wins. Templates are still the fake ones until that API exists.
- Any `accessToken` setup for Supabase is for Realtime subscriptions only; FastAPI calls get the token from `src/lib/accessToken.ts` and send it as a Bearer header themselves: `signedInAccessToken()` for a screen's first load (it signs the browser in first; concurrent callers share one sign-in), `accessToken()` for calls made once a screen is up. The Supabase client is loaded lazily, so demo mode works without the Supabase variables.

## Checks

`make lint test build` from this folder (or `make check` from the repo root) runs the same checks as CI. Tests use Vitest (`npm test`); put them next to the code as `*.test.ts`.

## Following a real call

The live-call page follows a real call when `VITE_API_URL` (the backend, e.g. `http://localhost:8000`) is set and `?call=<call id>` is in the address; otherwise it plays the fake call. `VITE_LIVE_CALL_SOURCE` is an opt-out: set it to `fake` to keep the scripted call with an API URL configured (`real` or unset means real). The page signs the browser in anonymously itself; a call placed from another browser belongs to another user, so its link shows "Couldn't load the call". To try it locally: `supabase start`, run the backend with `make -C backend seed-dev` and `uv run uvicorn app.main:app`, open `http://localhost:5173/live?call=<CALL_ID>`, then `make -C backend replay CALL_ID=<CALL_ID>`.

The history screen (`/history`) lists real calls whenever `VITE_API_URL` is set (`VITE_HISTORY_SOURCE=fake` opts out; `real` or unset means real) and shows fake calls otherwise; with the backend it signs the browser in anonymously if needed and lists that user's calls from `GET /calls` and opens each with `GET /calls/{call_id}/result` (code in `src/postcall/`). `?open=<call id>` opens one call's result directly. Opening a call adds a browser history entry, so Back returns to the list (`src/postcall/openCallUrl.ts`). The call-ended notification opens `/history?open=<call id>`.

## Patterns

- Live call: never keep call state in components. Hold the call's events (merged and de-duplicated by `seq` with `mergeEvents`) and derive the view with `deriveLiveCall`. Unknown event types and statuses are expected (open enums): skip or show "Unknown", never throw.
- Live call: never show the agent's confidence tier or `dimensions` (DC-5). Decisions show the plain-language reason, plus the `missed_preference` flag for Medium.
- Live call: the instruction box (DC-10) belongs to the pre-call owner; `CallControls.tsx` marks where it goes.

## Notes

- Only `VITE_`-prefixed variables reach the browser, and anything in them is public. Never put the service role key or database password here.
- Realtime authorizes `postgres_changes` with the token the Supabase client hands it. A signed-in session does this for you. With a token from anywhere else, pass it as `createClient(url, anonKey, { accessToken: async () => token })` and `await client.realtime.setAuth()` before subscribing. Calling `client.realtime.setAuth(token)` on a client with no session doesn't stick: the client switches back to the anon key, and Realtime drops the subscription. Watch the channel's `system` messages: a `postgres_changes` message with status `error` means nothing will be delivered. `scripts/realtime-smoke.mjs` shows the pattern.
