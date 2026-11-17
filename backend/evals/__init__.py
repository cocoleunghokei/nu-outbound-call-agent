"""Evals. Those that call paid APIs (briefs, in-call) never run in make test or CI; run them
by hand. Free ones with no network (postcall) also run in CI through tests/."""
