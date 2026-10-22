# Contributing

## Getting started

```bash
git clone https://github.com/cocoleunghokei/nu-outbound-call-agent.git
cd nu-outbound-call-agent
```

## Branches

`main` is the shared branch everyone works from. Don't commit to it directly.

1. Update your local `main`:
   ```bash
   git checkout main
   git pull
   ```
2. Create a branch for your work:
   ```bash
   git checkout -b backend/short-description
   ```
3. Commit, push, and open a pull request into `main`:
   ```bash
   git push -u origin backend/short-description
   ```
4. Get at least one teammate to review it before merging.
5. After merging, delete the branch.

### Branch names

Start with the area you're working in:

- `backend/...`: server, telephony, call loop, API
- `frontend/...`: web app UI
- `docs/...`: research, specs, tech doc
- `fix/...`: bug fixes

Example: `backend/twilio-outbound-call`

## Commits

Keep commits small, with a message that says what changed, for example `Add endpoint to start a call`.

## Secrets

Never commit API keys (Twilio, voice AI platform, LLM provider). Put them in a local `.env` file, which git ignores. Once we pick the stack, we'll add a `.env.example` that lists the variable names.

## Personal data

This app will handle names, birthdays, addresses and insurance info. Use fake data for testing, and never commit real personal info or call recordings.

## Database (Supabase)

Database changes live in `supabase/migrations/` as SQL files, so everyone's database ends up the same. Don't change tables by hand in the Supabase dashboard.

One-time setup:

```bash
brew install supabase/tap/supabase
supabase login
supabase link --project-ref tenmxbpwuxkicmgvmfym
```

Ask Coco for the database password. `supabase link` and `supabase db push` ask for it, or you can put it in a `.env` file at the repo root as `SUPABASE_DB_PASSWORD=...` (git ignores `.env`).

To make a change:

1. Create a migration file (the CLI names it for you):
   ```bash
   supabase migration new add_call_history
   ```
2. Write the SQL in the new file, and open a PR like any other change.
3. After the PR is merged, apply it to the shared database from an up-to-date `main`:
   ```bash
   supabase db push
   ```

Rules for every table:

- Turn on row-level security in the same migration that creates the table (`alter table ... enable row level security;`). Without it, anyone with the public key can read the whole table.
- Tables only the backend uses: row-level security on, with no policies.
- Anything that costs money or records what happened (call minutes, call status, outcomes) is written only by the backend, never directly by the browser.
