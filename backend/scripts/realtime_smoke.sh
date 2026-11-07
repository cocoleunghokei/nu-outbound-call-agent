#!/usr/bin/env bash
# End-to-end Realtime check; see frontend/scripts/realtime-smoke.mjs. Run from backend/ via
# `make realtime-smoke`. Needs the local Supabase stack with Realtime (CI: `supabase start`).
# Reads SUPABASE_API_URL and SUPABASE_ANON_KEY, falling back to `supabase status -o env`; the
# seed reads SUPABASE_DB_URL / SUPABASE_AUTH_URL / SUPABASE_JWT_* the same way.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -z "${SUPABASE_API_URL:-}" ] || [ -z "${SUPABASE_ANON_KEY:-}" ]; then
  status="$(supabase status -o env)"
  SUPABASE_API_URL="${SUPABASE_API_URL:-$(sed -n 's/^API_URL="\(.*\)"$/\1/p' <<<"$status")}"
  SUPABASE_ANON_KEY="${SUPABASE_ANON_KEY:-$(sed -n 's/^ANON_KEY="\(.*\)"$/\1/p' <<<"$status")}"
fi
: "${SUPABASE_API_URL:?need SUPABASE_API_URL}" "${SUPABASE_ANON_KEY:?need SUPABASE_ANON_KEY}"

# The backend needs the same stack details the seed discovers.
eval "$(uv run python -c '
from app.dev.local_stack import discover_supabase_env
e = discover_supabase_env()
print(f"export DATABASE_URL={e.db_url!r} SUPABASE_JWT_SECRET={e.jwt_secret!r}")
print(f"export SUPABASE_JWT_ISSUER={e.jwt_issuer!r}")
')"
export VAPI_WEBHOOK_SECRET="smoke-local-secret" VOICE_PLATFORM=fake

read_var() { sed -n "s/^$1=//p" <<<"$2"; }
alex="$(uv run python -m app.dev.seed --email alex@example.test --vapi-call-id fake-1)"
bob="$(uv run python -m app.dev.seed --email bob@example.test --vapi-call-id fake-bob-1)"
call_id="$(read_var CALL_ID "$alex")"
alex_token="$(read_var ACCESS_TOKEN "$alex")"
bob_token="$(read_var ACCESS_TOKEN "$bob")"

log="$(mktemp)"
uv run uvicorn app.main:app --port 8000 >"$log" 2>&1 &
server=$!
trap 'kill "$server" 2>/dev/null || true; wait "$server" 2>/dev/null || true; rm -f "$log"' EXIT

for _ in $(seq 1 50); do
  curl -fs http://localhost:8000/health >/dev/null && break
  sleep 0.2
done
curl -fs http://localhost:8000/health >/dev/null || { echo "backend did not start:"; cat "$log"; exit 1; }

node ../frontend/scripts/realtime-smoke.mjs \
  --url "$SUPABASE_API_URL" --anon-key "$SUPABASE_ANON_KEY" --call-id "$call_id" \
  --owner-token "$alex_token" --other-token "$bob_token" \
  --trigger "uv run python -m app.dev.replay tests/fixtures/vapi/happy_booking.jsonl $call_id --type transcript --limit 1"
