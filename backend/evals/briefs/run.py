"""Live brief eval against the REAL DeepSeek API: ``make eval-briefs`` (costs money).

Runs only when ``DEEPSEEK_API_KEY`` is set in the environment (not read from any file), one
Chat Completions call per case in cases.json, one at a time. Base URL and model come from
the usual settings (``DEEPSEEK_BASE_URL``, ``DEEPSEEK_MODEL``). Prints each case's summary
and failures, never the key. Exit 0 when every case passes, 1 when any fails, 2 without a key.
Not part of ``make test`` or CI.
"""

import asyncio
import os
import sys

import httpx

from app.briefs.llm import DeepSeekClient
from app.config import Settings
from evals.briefs.checks import RecordingTransport, check_case, load_cases, run_case


async def main() -> int:
    """Run every case once against the live API and print a report."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print(
            "DEEPSEEK_API_KEY is not set in the environment; not running (a live run costs money)."
        )
        return 2
    settings = Settings()
    recorder = RecordingTransport(httpx.AsyncHTTPTransport())
    client = DeepSeekClient(
        key, settings.deepseek_base_url, settings.deepseek_model, transport=recorder
    )
    profile, cases = load_cases()
    print(f"model {settings.deepseek_model}, {len(cases)} cases, one API call each")
    failed = 0
    for case in cases:
        result = await run_case(case, profile, client, recorder)
        failures = check_case(case, result, profile)
        interp = result.interpretation
        summary = interp.goal_summary if interp else "-"
        used = interp.llm_used if interp else False
        print(f"{'PASS' if not failures else 'FAIL'} {case['id']}: llm_used={used} {summary!r}")
        for failure in failures:
            print(f"    - {failure}")
        failed += bool(failures)
    print(f"{len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
