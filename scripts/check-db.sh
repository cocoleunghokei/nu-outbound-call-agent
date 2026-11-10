#!/usr/bin/env bash
# Verify that the Supabase migrations build a valid, safe database from scratch.
#
# Steps:
#   1. Start a throwaway local Postgres (Docker) if it is not already running.
#   2. Rebuild it from supabase/migrations/ (and seed.sql, if present).
#   3. Run the Supabase schema linter and fail on errors.
#   4. Fail if any table in the public schema has row-level security disabled.
#   5. Run the pgTAP tests in supabase/tests/ (row-level security and grants).
#
# Used by `make db-check` locally and by the `database` job in CI.
# Requires: Docker and the Supabase CLI (set SUPABASE_BIN to override the binary).
set -euo pipefail

SUPABASE_BIN="${SUPABASE_BIN:-supabase}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v "$SUPABASE_BIN" >/dev/null 2>&1; then
  echo "error: Supabase CLI not found. Install it (see CONTRIBUTING.md) or set SUPABASE_BIN." >&2
  exit 1
fi

echo "==> Starting local database"
"$SUPABASE_BIN" db start

echo "==> Rebuilding database from supabase/migrations"
"$SUPABASE_BIN" db reset --local

echo "==> Linting schema"
"$SUPABASE_BIN" db lint --local --level warning --fail-on error

echo "==> Checking row-level security on public tables"
"$SUPABASE_BIN" db query --local --file scripts/sql/assert_rls_enabled.sql >/dev/null

echo "==> Running pgTAP tests"
"$SUPABASE_BIN" test db

echo "==> Database checks passed"
