"""Webhook server: logs Vapi events per call and runs the pause-and-ping loop."""
from __future__ import annotations

import hmac
import json
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from probe.assistant import (ASK_USER_TOOL_NAME, REPORT_DECISION_TOOL_NAME,
                             REPORT_OUTCOME_TOOL_NAME, SECRET_HEADER)
from probe.control import ControlClient
from probe.eventlog import EventLog, load_meta
from probe.ping import TIMEOUT_S, PingSession

UNKNOWN = "unknown"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_STOP = object()
MAX_BODY_BYTES = 1_048_576
LOG_ONLY_TOOLS = (REPORT_DECISION_TOOL_NAME, REPORT_OUTCOME_TOOL_NAME)


class ProbeServer:
    """Receives Vapi webhooks; queues control messages to one ordered sender thread."""

    def __init__(self, secret: str, runs_dir: Path, control: ControlClient, user_name: str,
                 clock: Callable[[], float] = time.time,
                 out: Callable[[str], None] = print):
        self.secret, self.runs_dir, self.control = secret, Path(runs_dir), control
        self.user_name, self.clock, self.out = user_name, clock, out
        self.server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()          # guards every PingSession call and read (R12)
        self._sessions: dict[str, PingSession] = {}
        self._notices_printed: dict[str, int] = {}
        self._last_left: dict[str, int] = {}
        self._opened_seq: dict[str, int] = {}
        self._seq = 0
        self._logs_lock = threading.Lock()
        self._logs: dict[str, EventLog] = {}
        self._urls: dict[str, str] = {}
        self._q: queue.Queue = queue.Queue()
        self._http_thread: threading.Thread | None = None
        self._sender: threading.Thread | None = None

    # ---- lifecycle -------------------------------------------------------

    def start(self, host: str = "127.0.0.1", port: int = 8787) -> int:
        """Bind and serve in daemon threads; return the bound port."""
        self.server = ThreadingHTTPServer((host, port), _Handler)
        self.server.daemon_threads = True
        self.server.probe = self  # type: ignore[attr-defined]
        self._sender = threading.Thread(target=self._send_loop, daemon=True)
        self._sender.start()
        self._http_thread = threading.Thread(target=self.server.serve_forever,
                                             kwargs={"poll_interval": 0.1}, daemon=True)
        self._http_thread.start()
        return self.server.server_address[1]

    def stop(self) -> None:
        """Shut down the HTTP server, close its socket and stop the sender."""
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self._http_thread.join(timeout=5)
            self.server = None
        if self._sender is not None:
            self._q.put(_STOP)
            self._sender.join(timeout=25)
            self._sender = None

    def drain(self, timeout: float = 2.0) -> bool:
        """Block until every queued control message has been sent; False on timeout."""
        deadline = time.monotonic() + timeout
        with self._q.all_tasks_done:
            while self._q.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._q.all_tasks_done.wait(remaining)
        return True

    # ---- operator side ---------------------------------------------------

    def ping_for(self, call_id: str) -> PingSession:
        """The call's PingSession, created on first use."""
        with self._lock:
            return self._session(call_id)

    def tick(self) -> None:
        """Advance every call's ping (fillers, timeout) and queue the resulting messages."""
        with self._lock:
            now = self.clock()
            for cid, s in self._sessions.items():
                was_open = s.is_open
                self._dispatch(cid, s.tick(now))
                if s.is_open:
                    left = max(0, int(TIMEOUT_S - (now - s.view.opened_at)))
                    if abs(self._last_left.get(cid, TIMEOUT_S) - left) >= 5:
                        self._last_left[cid] = left
                        self.out(f"{left}s left")
                elif was_open:
                    self.out(f"No reply within {TIMEOUT_S}s: the agent was told to apply "
                             "the no-reply rule.")
                self._print_notices(cid, s)

    def reply(self, text: str) -> None:
        """Send the operator's reply to the most recently opened, still-open ping."""
        with self._lock:
            open_ids = [c for c, s in self._sessions.items() if s.is_open]
            pool = open_ids or list(self._sessions)
            if not pool:
                self.out("Reply ignored: no ping is open.")
                return
            cid = max(pool, key=lambda c: self._opened_seq.get(c, -1))
            s = self._sessions[cid]
            msgs = s.on_reply(text, self.clock())
            self._dispatch(cid, msgs)
            if msgs:
                self.out("Reply sent.")
            self._print_notices(cid, s)

    # ---- webhook side ----------------------------------------------------

    def handle(self, path: str, headers,
               read_body: Callable[[int], bytes]) -> tuple[int, dict]:
        """Process one request; return (status, JSON response). Never waits on a send.

        Path, secret and Content-Length are checked before `read_body(length)` runs, so an
        unauthenticated or oversized request never makes the server buffer its body.
        """
        if path != "/webhook":
            return 404, {}
        got = (headers.get(SECRET_HEADER) or "").encode()
        if not hmac.compare_digest(got, self.secret.encode()):
            self._log(UNKNOWN).append("rejected", {"reason": "bad or missing secret"})
            return 401, {}
        raw_len = headers.get("Content-Length")
        try:
            length = int(raw_len)
            if length < 0:
                raise ValueError("negative")
        except (TypeError, ValueError):
            self._log(UNKNOWN).append("bad-request", {"error": "bad Content-Length",
                                                      "content_length": raw_len})
            return 400, {}
        if length > MAX_BODY_BYTES:
            self._log(UNKNOWN).append("bad-request", {"error": "body too large",
                                                      "content_length": length})
            return 413, {}
        try:
            body = read_body(length) if length else b""
        except OSError as e:  # includes the handler's socket timeout
            self._log(UNKNOWN).append("bad-request", {"error": f"body read failed: {e}"})
            return 400, {}
        try:
            data = json.loads(body)
            message = data["message"]
            if not isinstance(message, dict):
                raise TypeError("message is not an object")
        except Exception as e:  # any undecodable or misshapen body is a 400
            self._log(UNKNOWN).append("bad-request", {
                "error": f"{type(e).__name__}: {e}",
                "body": body[:500].decode("utf-8", "replace")})
            return 400, {}
        call = message.get("call")
        call_id = call.get("id") if isinstance(call, dict) else None
        call_id = call_id if isinstance(call_id, str) and _SAFE_ID.match(call_id) else None
        # Log per call even before its meta exists (R13); only sending needs the meta.
        self._log(call_id or UNKNOWN).append(f"vapi:{message.get('type')}", message)
        if message.get("type") == "status-update" and message.get("status") == "ended":
            self._on_call_ended(call_id or UNKNOWN)
        if message.get("type") != "tool-calls":
            return 200, {}
        return 200, {"results": self._on_tool_calls(call_id or UNKNOWN, message)}

    def _on_call_ended(self, cid: str) -> None:
        """Close the call's open ping, if any, so nothing more is sent to it."""
        with self._lock:
            s = self._sessions.get(cid)
            if s is None:
                return
            self._dispatch(cid, s.on_call_ended(self.clock()))
            self._print_notices(cid, s)

    def _on_tool_calls(self, cid: str, message: dict) -> list[dict]:
        """Handle ask_user (ping) and report_* (log only); ignore any other tool."""
        results = []
        calls = message.get("toolCallList")
        for tc in calls if isinstance(calls, list) else []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            name = fn.get("name") if isinstance(fn, dict) else None
            if name != ASK_USER_TOOL_NAME and name not in LOG_ONLY_TOOLS:
                continue
            args = _parse_args(fn.get("arguments"))
            call_id = tc.get("id")
            self._log(cid).append(f"tool:{name}", {"tool_call_id": call_id, "args": args})
            if name in LOG_ONLY_TOOLS:
                results.append({"name": name, "toolCallId": call_id, "result": "Logged."})
                continue
            field = args.get("field_requested")
            if isinstance(field, str) and field:
                self._log(cid).append("ask-first-trigger", {"field_requested": field,
                                                            "tool_call_id": call_id})
            with self._lock:
                s = self._session(cid)
                if not s.is_open:
                    self._seq += 1
                    self._opened_seq[cid] = self._seq
                    self._last_left[cid] = TIMEOUT_S
                msgs = s.on_ask(args, self.clock())
                self._print_view(cid, s)
                if isinstance(field, str) and field:
                    self.out(f"  ask-first field: {field}")
                self._dispatch(cid, msgs)
            results.append({"name": ASK_USER_TOOL_NAME, "toolCallId": call_id,
                            "result": f"Waiting for {self.user_name} to reply."})
        return results

    # ---- internals (callers hold self._lock where noted) -----------------

    def _session(self, cid: str) -> PingSession:
        """Caller holds self._lock."""
        if cid not in self._sessions:
            self._sessions[cid] = PingSession(self.user_name)
            self._notices_printed[cid] = 0
        return self._sessions[cid]

    def _print_view(self, cid: str, s: PingSession) -> None:
        """Caller holds self._lock."""
        v = s.view
        lines = [f"[{cid}] {self.user_name}, the agent is asking:"]
        lines += [f"  Q: {q}" for q in v.questions]
        lines += [f"  why: {r}" for r in v.reasons]
        lines += [f"  {i}. {r}" for i, r in enumerate(v.suggested_replies, 1)]
        n = len(v.suggested_replies)
        lines.append(f"  type 1-{n} or a reply" if n else "  type a reply")
        self.out("\n".join(lines))

    def _print_notices(self, cid: str, s: PingSession) -> None:
        """Caller holds self._lock."""
        done = self._notices_printed.get(cid, 0)
        for notice in s.notices[done:]:
            self.out(notice)
        self._notices_printed[cid] = len(s.notices)

    def _dispatch(self, cid: str, msgs: list[dict]) -> None:
        url = self._control_url(cid)
        for m in msgs:
            if url is None:
                self.out(f"no control URL for call {cid}")
            else:
                self._q.put((cid, url, m))

    def _control_url(self, cid: str) -> str | None:
        if cid in self._urls:
            return self._urls[cid]
        if cid == UNKNOWN or not _SAFE_ID.match(cid):
            return None
        meta = load_meta(self.runs_dir, cid)
        if meta is None or not meta.control_url:
            return None
        self._urls[cid] = meta.control_url
        return meta.control_url

    def _log(self, cid: str) -> EventLog:
        with self._logs_lock:
            if cid not in self._logs:
                self._logs[cid] = EventLog(self.runs_dir / f"{cid}.jsonl", self.clock)
            return self._logs[cid]

    def _send_loop(self) -> None:
        while True:
            item = self._q.get()
            try:
                if item is _STOP:
                    return
                cid, url, message = item
                try:
                    self.control.send(url, message, self._log(cid).append)
                except Exception as e:  # keep the sender alive whatever happens
                    self.out(f"control send crashed for call {cid}: {e}")
            finally:
                self._q.task_done()


def _parse_args(raw) -> dict:
    """Tool-call arguments as a dict; {} when missing, malformed or not an object."""
    if isinstance(raw, dict):
        return raw
    try:
        args = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return args if isinstance(args, dict) else {}


class _Handler(BaseHTTPRequestHandler):
    """Thin HTTP adapter over ProbeServer.handle."""

    timeout = 10  # a client that stalls mid-request frees its thread after 10 s

    def do_POST(self) -> None:  # noqa: N802
        status, payload = self.server.probe.handle(self.path, self.headers, self.rfile.read)
        self._respond(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        self._respond(404, {})

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_GET

    def _respond(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except OSError:  # client already gone (e.g. reset after an early 401/413)
            self.close_connection = True

    def log_message(self, format: str, *args) -> None:  # silence default stderr logging
        pass
