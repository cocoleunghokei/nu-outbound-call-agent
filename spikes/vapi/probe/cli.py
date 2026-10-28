"""Command-line interface: call, serve, control, listen and report."""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from probe.assistant import build_assistant
from probe.config import ConfigError, Settings, load_settings, parse_dotenv, validate_us_number
from probe.control import (ControlClient, ControlError, Transport, add_system_message,
                           mute_assistant, say, transfer_to, unmute_assistant, urllib_transport)
from probe.eventlog import EventLog, RunMeta, load_meta, running_cost, save_meta
from probe.listen import infer_pcm_format, listen
from probe.report import format_block, format_line, summarize
from probe.scenario import Scenario, load_scenario
from probe.vapi_api import VapiApi
from probe.webhook import ProbeServer

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = PACKAGE_ROOT / "runs"
SCENARIO_DIR = PACKAGE_ROOT / "scenarios"
DOTENV_PATH = PACKAGE_ROOT / ".env"


def _load_env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    """`env` as-is when given, else os.environ merged over spikes/vapi/.env."""
    if env is not None:
        return env
    merged: dict[str, str] = {}
    if DOTENV_PATH.exists():
        merged.update(parse_dotenv(DOTENV_PATH.read_text(encoding="utf-8")))
    merged.update(os.environ)
    return merged


def _runs_dir(env: Mapping[str, str]) -> Path:
    raw = (env.get("PROBE_RUNS_DIR") or "").strip()
    return Path(raw) if raw else DEFAULT_RUNS_DIR


def _find_scenario(scenario_id: str) -> Scenario:
    found: dict[str, Scenario] = {}
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        sc = load_scenario(path)
        found[sc.id] = sc
    if scenario_id not in found:
        raise ConfigError(f"unknown scenario {scenario_id!r}; valid ids: "
                          + ", ".join(sorted(found)))
    return found[scenario_id]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="probe", description="Vapi probe kit (throwaway)")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("call", help="place a scripted call")
    c.add_argument("scenario_id")
    c.add_argument("--to", required=True, help="US E.164 number, must be allowlisted")
    s = sub.add_parser("serve", help="run the webhook server and read operator replies")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--user", default="Alex Chen")
    r = sub.add_parser("report", help="print metrics for a call, or list all calls")
    r.add_argument("call_id", nargs="?")
    r.add_argument("--offline", action="store_true", help="skip the Vapi API (no cost fetch)")
    ls = sub.add_parser("listen", help="record a call's live audio as raw PCM")
    ls.add_argument("call_id")
    ls.add_argument("--out", type=Path, default=None, help="default runs/<call-id>.pcm")
    k = sub.add_parser("control", help="send a live instruction to a call")
    k.add_argument("call_id")
    ksub = k.add_subparsers(dest="action", required=True)
    ins = ksub.add_parser("instruct", help="add a system instruction")
    ins.add_argument("text")
    for name in ("pause", "resume", "end", "transfer"):
        ksub.add_parser(name)
    return p


def _cmd_call(args, settings: Settings, runs_dir: Path, transport: Transport) -> int:
    scenario = _find_scenario(args.scenario_id)
    to = validate_us_number(args.to)
    if to not in settings.allowed_numbers:
        raise ConfigError(f"{to} is not on the allowlist "
                          "(PROBE_RECEPTIONIST_NUMBERS or PROBE_OPERATOR_NUMBER)")
    spent = running_cost(runs_dir)
    if spent >= settings.spend_cap_usd:
        raise ConfigError(f"spend cap reached: ${spent:.2f} of ${settings.spend_cap_usd:.2f} used")
    api = VapiApi(settings.api_key, settings.api_base, transport)
    try:
        resp = api.create_call(build_assistant(scenario, settings),
                               settings.phone_number_id, to)
    except ControlError as exc:
        print(f"error: Vapi refused the call: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # timeout or bad body: Vapi may already have accepted the POST
        print(f"error: the call MAY have been placed ({type(exc).__name__}: {exc}); "
              "check the Vapi dashboard and end it there", file=sys.stderr)
        return 1
    call_id = resp.get("id") if isinstance(resp, dict) else None
    if not isinstance(call_id, str) or not call_id:
        print("error: the call MAY have been placed but the response had no call id; "
              "check the Vapi dashboard and end it there. Response: "
              f"{str(resp)[:300]}", file=sys.stderr)
        return 1
    monitor = resp.get("monitor")
    if not isinstance(monitor, dict):
        monitor = {}

    def _url(key: str) -> str:
        v = monitor.get(key)
        return v if isinstance(v, str) else ""

    meta = RunMeta(call_id=call_id, scenario_id=scenario.id, to=to,
                   control_url=_url("controlUrl"), listen_url=_url("listenUrl"),
                   started_at=time.time())
    save_meta(runs_dir, meta)
    print(call_id)
    if not meta.control_url:
        print("warning: no control URL in the response; live control is unavailable "
              "for this call", file=sys.stderr)
    return 0


def _control_messages(args, settings: Settings) -> list[dict]:
    if args.action == "instruct":
        return [add_system_message(
            f"Alex's instruction: {args.text}. Work it in at the next natural turn.",
            trigger_response=False)]
    if args.action == "pause":
        return [say("Could you hold for just a moment, please?"), mute_assistant()]
    if args.action == "resume":
        return [unmute_assistant(),
                add_system_message("Alex is back. Continue the call.", trigger_response=True)]
    if args.action == "end":
        return [say("Thanks so much for your help. Goodbye!", end_call_after=True)]
    return [transfer_to(settings.operator_number)]


def _cmd_control(args, settings: Settings, runs_dir: Path, transport: Transport) -> int:
    meta = load_meta(runs_dir, args.call_id)
    if meta is None:
        raise ConfigError(f"unknown call {args.call_id}")
    if not meta.control_url:
        raise ConfigError(f"call {args.call_id} has no control URL")
    client = ControlClient(transport)
    log = EventLog(runs_dir / f"{args.call_id}.jsonl").append
    ok = True
    for msg in _control_messages(args, settings):
        if not client.send(meta.control_url, msg, log):
            print(f"error: control message failed: {msg}", file=sys.stderr)
            ok = False
    return 0 if ok else 1


def _cmd_listen(args, runs_dir: Path) -> int:
    meta = load_meta(runs_dir, args.call_id)
    if meta is None:
        raise ConfigError(f"unknown call {args.call_id}")
    if not meta.listen_url:
        raise ConfigError(f"call {args.call_id} has no listen URL")
    out_path = args.out or runs_dir / f"{args.call_id}.pcm"
    stop = threading.Event()
    print(f"Recording to {out_path}; Ctrl-C to stop.", flush=True)
    started = time.monotonic()
    try:
        try:
            count = listen(meta.listen_url, out_path, stop)
        except KeyboardInterrupt:
            stop.set()  # Ctrl-C before listen returned: nothing to report
            count = out_path.stat().st_size if out_path.exists() else 0
    except (ConnectionError, OSError) as exc:
        print(f"error: listen failed: {exc}", file=sys.stderr)
        return 1
    seconds = time.monotonic() - started
    print(f"{count} bytes in {seconds:.1f} s; likely {infer_pcm_format(count, seconds)}")
    return 0


def _cmd_report(args, settings: Settings, runs_dir: Path, transport: Transport,
                user_name: str = "Alex Chen") -> int:
    if args.call_id is None:
        for path in sorted(runs_dir.glob("*.meta.json")):
            call_id = path.name[:-len(".meta.json")]
            summary = summarize(call_id, runs_dir, None, user_name)
            print(format_line(summary | {"cost": load_meta(runs_dir, call_id).cost}))
        print(f"spent ${running_cost(runs_dir):.2f} of ${settings.spend_cap_usd:.2f} cap")
        return 0
    if load_meta(runs_dir, args.call_id) is None:
        raise ConfigError(f"unknown call {args.call_id}")
    api = None
    if not args.offline and settings.api_key:
        api = VapiApi(settings.api_key, settings.api_base, transport)
    print(format_block(summarize(args.call_id, runs_dir, api, user_name)))
    return 0


def _cmd_serve(args, settings: Settings, runs_dir: Path, transport: Transport,
               stdin: TextIO) -> int:
    server = ProbeServer(secret=settings.webhook_secret, runs_dir=runs_dir,
                         control=ControlClient(transport), user_name=args.user)
    port = server.start(port=args.port)
    stop = threading.Event()

    def ticker() -> None:
        while not stop.wait(1.0):
            server.tick()

    thread = threading.Thread(target=ticker, daemon=True)
    thread.start()
    print(f"Listening on port {port}, webhook path /webhook. "
          f"Type a reply and press Enter when a ping is open; Ctrl-D to quit.", flush=True)
    try:
        for line in stdin:
            server.reply(line.strip())
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        thread.join(timeout=5)
        server.stop()
    return 0


def main(argv: list[str], env: Mapping[str, str] | None = None,
         transport: Transport = urllib_transport, stdin: TextIO | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        values = _load_env(env)
        settings = load_settings(values)
        runs_dir = _runs_dir(values)
        if args.command == "call":
            return _cmd_call(args, settings, runs_dir, transport)
        if args.command == "listen":
            return _cmd_listen(args, runs_dir)
        if args.command == "report":
            return _cmd_report(args, settings, runs_dir, transport)
        if args.command == "control":
            return _cmd_control(args, settings, runs_dir, transport)
        return _cmd_serve(args, settings, runs_dir, transport, stdin or sys.stdin)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
