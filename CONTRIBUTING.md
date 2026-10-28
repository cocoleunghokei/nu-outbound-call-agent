# Contributing

## Getting started

```bash
git clone https://github.com/cocoleunghokei/nu-outbound-call-agent.git
cd nu-outbound-call-agent
```

Install [pre-commit](https://pre-commit.com/) once (`brew install pre-commit` or `pipx install pre-commit`), then install the git hooks:

```bash
make setup
```

The hooks block leaked keys and badly formatted commit messages before they leave your machine. AI agents: read [AGENTS.md](AGENTS.md) first. The full pipeline is described in [docs/devops.md](docs/devops.md).

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
3. Before pushing, run the same checks CI runs:
   ```bash
   make check
   ```
4. Commit, push, and open a pull request into `main`:
   ```bash
   git push -u origin backend/short-description
   ```
5. Get at least one teammate to review it, and wait for CI to go green.
6. Merge with **Squash and merge**. GitHub deletes the branch afterwards.

### Branch names

Start with the area you're working in:

- `backend/...`: server, telephony, call loop, API
- `frontend/...`: web app UI
- `docs/...`: research, specs, tech doc
- `fix/...`: bug fixes
- `chore/...`: tooling, CI, repo setup

Example: `backend/twilio-outbound-call`

## Commits

Keep commits small. Messages follow [Conventional Commits](https://www.conventionalcommits.org/), `type(scope): summary`:

```
feat(backend): add endpoint to start a call
fix(frontend): keep the live transcript scrolled to the bottom
docs: add competitor research notes
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, `revert`. Scopes: `backend`, `frontend`, `db`, `ci`, `docs`, or none. The commit hook rejects other formats. PR titles use the same format, because the title becomes the commit on `main` when it's squash-merged.

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
2. Write the SQL in the new file.
3. If you have Docker, check that the migrations build a clean database and pass the row-level security rule. CI runs the same check on your PR:
   ```bash
   make db-check
   ```
4. Open a PR like any other change.
5. After the PR is merged, the **Deploy database** workflow applies it to the shared database. If that workflow isn't set up yet (see [docs/devops.md](docs/devops.md)), apply it by hand from an up-to-date `main`:
   ```bash
   supabase db push
   ```

Rules for every table:

- Turn on row-level security in the same migration that creates the table (`alter table ... enable row level security;`). Without it, anyone with the public key can read the whole table. CI fails the PR if a table in `public` is missing it.
- Tables only the backend uses: row-level security on, with no policies.
- Anything that costs money or records what happened (call minutes, call status, outcomes) is written only by the backend, never directly by the browser.
