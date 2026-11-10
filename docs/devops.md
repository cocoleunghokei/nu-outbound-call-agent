# DevOps flow

How a change gets from a branch to the shared database and, later, to production.

## The flow

1. Branch off an up-to-date `main` (see [CONTRIBUTING.md](../CONTRIBUTING.md) for names).
2. Commit with Conventional Commits messages. The `commit-msg` hook rejects anything else.
3. Run `make check` (plus `make db-check` for database changes) and push.
4. Open a PR with a Conventional Commits title. CI runs automatically.
5. One teammate approves, all required checks are green, then **squash and merge**.
6. If the PR added migrations, the **Deploy database** workflow applies them to Supabase.

## Checks

| CI job | Local command | What it catches | Runs on |
| --- | --- | --- | --- |
| Hygiene and secret scan | `make hygiene secrets` | Leaked keys in the history of HEAD (main plus the PR), private keys, broken YAML/TOML/JSON, invalid workflows, merge markers, files over 1 MB, whitespace | Every PR and push to `main` |
| Lint, test, build | `make lint test build` | Each subproject's own checks, through its Makefile | Every PR and push to `main` |
| Database migrations | `make db-check` | Migrations that don't apply from scratch, schema lint errors, public tables without row-level security | PRs that touch `supabase/` |
| PR title | none | PR titles not in Conventional Commits format | Every PR |

The git hooks installed by `make setup` run the hygiene checks on each commit, so most problems never reach CI.

## Plugging in the stack

Done for both the backend (`backend/`: Python 3.12 + FastAPI, uv) and the frontend (`frontend/`). Each has a `Makefile` with `lint`, `test` and `build`, a toolchain setup in the `build` job of `.github/workflows/ci.yml`, and an `.env.example`. If you add another subproject, repeat the same steps:

1. Add a `Makefile` in that folder with `lint`, `test` and `build` targets. The root `make lint test build` picks it up automatically, and so does CI.
2. Add the toolchain setup (for example `actions/setup-node` or `actions/setup-python` with dependency caching) to the `build` job in `.github/workflows/ci.yml`.
3. Add the language's formatter and linter to `.pre-commit-config.yaml`.
4. Add an `npm` or `pip` entry to `.github/dependabot.yml`.
5. Add `.env.example` with every variable name the app reads, and no values.
6. Update the stack table in `AGENTS.md`.

The `database` job also runs the backend's database tests and the Realtime smoke check (`make -C backend realtime-smoke`) against the local Supabase stack. If that step fails, the job prints the last 200 lines of the Realtime container log.

## Frontend deploys (Vercel)

Vercel's GitHub integration builds `frontend/`. Every merge to `main` goes to production, and every PR gets its own preview URL posted on the PR. The Vercel project is on the Hobby plan, so previews only build for commits by members of the Vercel team. For anyone else, Vercel posts a "not a member of this team" comment and skips the preview. CI still runs, so review those PRs from the diff or a local checkout. There's no workflow file for this; it's set up in the Vercel dashboard:

- Root directory: `frontend` (Vite is detected automatically)
- Environment variables: `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`, for Production and Preview

## Backend deploys (Railway)

The backend is a Railway service built from `backend/`. Coco creates it. Like Vercel, there's no workflow file; it's set up in the Railway dashboard:

- Root directory: `backend`. The start command and health check are in `backend/railway.toml`.
- Environment variables: every name in `backend/.env.example` (the Postgres connection string through the Supabase session pooler, the Supabase JWT settings, the Vapi keys and webhook secret). Set `VOICE_PLATFORM=vapi` in production.
- Deploys follow `main`. Vapi's server URL points at `https://<service>/webhooks/vapi` with the webhook secret in the `X-Vapi-Secret` header.

## One-time repository setup (admin)

These settings live in GitHub, not in the repo, so a repo admin has to apply them.

**Branch protection for `main`** (Settings, then Branches, or Rules):

- Require a pull request before merging, with 1 approval.
- Require status checks to pass, and require branches to be up to date. Mark these as required: `Hygiene and secret scan`, `Lint, test, build`, `Database migrations`, `Conventional Commits title`. A skipped `Database migrations` job counts as passing.
- Block force pushes and deletions.

**Merge settings** (Settings, then General): allow squash merging only, with the PR title as the commit message, and auto-delete head branches.

**Database deploys** (Settings, then Environments): create an environment named `production`. Optionally add required reviewers so each migration deploy waits for approval. Then add these repository secrets:

| Secret | Where to get it |
| --- | --- |
| `SUPABASE_ACCESS_TOKEN` | Supabase dashboard, Account, Access Tokens |
| `SUPABASE_DB_PASSWORD` | The project's database password (ask Coco) |

The project ref defaults to the one in CONTRIBUTING.md. Set a `SUPABASE_PROJECT_REF` variable to override it. Until the secrets exist, the deploy job warns and skips, and migrations are pushed by hand with `supabase db push`.

## Maintenance

- Dependabot opens a weekly PR that bumps the pinned GitHub Actions.
- Dependabot also opens weekly PRs for `frontend/` npm packages: one for all minor and patch bumps, one for eslint majors, one for vite majors, and one per other major bump. TypeScript majors are ignored because `typescript-eslint` doesn't support TypeScript 7 yet; remove the `ignore` entry in `.github/dependabot.yml` once it does.
- When two Dependabot PRs touch `package.json`, the second one conflicts after the first merges. Comment `@dependabot rebase` on it. If someone has already pushed to that branch, Dependabot won't rebase it: merge `main` in by hand, regenerate `package-lock.json` with `npm install`, and run `make lint test build`.
- Run `pre-commit autoupdate` now and then to bump hook versions, and commit the result as `ci: update pre-commit hooks`.
