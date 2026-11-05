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
| Hygiene and secret scan | `make hygiene secrets` | Leaked keys in the full history, private keys, broken YAML/TOML/JSON, invalid workflows, merge markers, files over 1 MB, whitespace | Every PR and push to `main` |
| Lint, test, build | `make lint test build` | Each subproject's own checks, through its Makefile | Every PR and push to `main` |
| Database migrations | `make db-check` | Migrations that don't apply from scratch, schema lint errors, public tables without row-level security | PRs that touch `supabase/` |
| PR title | none | PR titles not in Conventional Commits format | Every PR |

The git hooks installed by `make setup` run the hygiene checks on each commit, so most problems never reach CI.

## Plugging in the stack

When `backend/` or `frontend/` gets code:

1. Add a `Makefile` in that folder with `lint`, `test` and `build` targets. The root `make lint test build` picks it up automatically, and so does CI.
2. Add the toolchain setup (for example `actions/setup-node` or `actions/setup-python` with dependency caching) to the `build` job in `.github/workflows/ci.yml`.
3. Add the language's formatter and linter to `.pre-commit-config.yaml`.
4. Add an `npm` or `pip` entry to `.github/dependabot.yml`.
5. Add `.env.example` with every variable name the app reads, and no values.
6. Update the stack table in `AGENTS.md`.

## Frontend deploys (Vercel)

Vercel's GitHub integration builds `frontend/`. Every merge to `main` goes to production, and every PR gets its own preview URL posted on the PR. There's no workflow file for this; it's set up in the Vercel dashboard:

- Root directory: `frontend` (Vite is detected automatically)
- Environment variables: `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`, for Production and Preview

Backend hosting gets its own workflow once it's picked in the tech doc.

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
- Run `pre-commit autoupdate` now and then to bump hook versions, and commit the result as `ci: update pre-commit hooks`.
