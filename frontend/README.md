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
- `src/precall/`: pre-call flow (PC-1 to PC-7). `types.ts` copies the `brief.v1` contract shapes from PR #19 (goes away once that merges) and drafts the template shape (to agree with Harper). `fakeBriefs.ts` holds a fake profile, the two P0 templates and example briefs shaped like the `briefs` table, until the backend is connected. `contractExample.ts` is a copy of `contracts/examples/brief.v1.valid.json` that the tests compare against. `phone.ts` is the PC-7 number check (US area codes only, emergency and N11 short codes blocked before normalising); the backend mirrors these rules.
- `public/prototype/`: static, clickable design prototype, served as is at `/prototype/`. It isn't part of the React app, so don't import from it.

## Checks

`make lint test build` from this folder (or `make check` from the repo root) runs the same checks as CI. Tests use Vitest (`npm test`); put them next to the code as `*.test.ts`.

## Notes

- Only `VITE_`-prefixed variables reach the browser, and anything in them is public. Never put the service role key or database password here.
- Realtime authorizes `postgres_changes` with the token the Supabase client hands it. A signed-in session does this for you. With a token from anywhere else, pass it as `createClient(url, anonKey, { accessToken: async () => token })` and `await client.realtime.setAuth()` before subscribing. Calling `client.realtime.setAuth(token)` on a client with no session doesn't stick: the client switches back to the anon key, and Realtime drops the subscription. Watch the channel's `system` messages: a `postgres_changes` message with status `error` means nothing will be delivered. `scripts/realtime-smoke.mjs` shows the pattern.
