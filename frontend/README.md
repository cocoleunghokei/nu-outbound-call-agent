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

- `src/App.tsx`: root component, start here
- `src/lib/supabase.ts`: shared Supabase client (anon key only; every table it reads needs row-level security)
- `src/index.css`: global styles
- `src/live-call/`: live-call page (DC-3 transcript, DC-14 status, DC-8 ping card). `state.ts` folds the call's `call_events` rows into what the page shows; `ping.ts` holds the ping rules (reply clock, free-text cleanup, no free text on Ask-me-first pings). `mockCall.ts` plays a scripted fake call and answers ping replies like the backend will, until the backend is connected. `types.ts` copies the contract shapes from PR #19 and goes away once that merges.
- `public/prototype/`: static, clickable design prototype, served as is at `/prototype/`. It isn't part of the React app, so don't import from it.

## Checks

`make lint test build` from this folder (or `make check` from the repo root) runs the same checks as CI. Tests use Vitest (`npm test`); put them next to the code as `*.test.ts`.

## Patterns

- Live call: never keep call state in components. Hold the call's events (merged and de-duplicated by `seq` with `mergeEvents`) and derive the view with `deriveLiveCall`. Unknown event types and statuses are expected (open enums): skip or show "Unknown", never throw.

## Notes

- Only `VITE_`-prefixed variables reach the browser, and anything in them is public. Never put the service role key or database password here.
