"""Minimal stdlib WebSocket client that records a call's listenUrl audio."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import threading
from pathlib import Path
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# (label, bytes per second of 16-bit PCM), in tie-break order (Ruling R7).
_FORMATS = (
    ("16-bit mono 8000 Hz", 16000),
    ("16-bit mono 16000 Hz", 32000),
    ("16-bit stereo 8000 Hz", 32000),
    ("16-bit stereo 16000 Hz", 64000),
    ("16-bit mono 24000 Hz", 48000),
    ("16-bit stereo 24000 Hz", 96000),
)


def ws_accept_key(client_key: str) -> str:
    """Sec-WebSocket-Accept value for a client key (RFC 6455)."""
    digest = hashlib.sha1((client_key + _GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def decode_frame(buf: bytes) -> tuple[int, bytes, int] | None:
    """Decode one frame: (opcode, payload, bytes consumed), or None if incomplete."""
    if len(buf) < 2:
        return None
    opcode = buf[0] & 0x0F
    masked = bool(buf[1] & 0x80)
    length = buf[1] & 0x7F
    pos = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length, pos = int.from_bytes(buf[2:4], "big"), 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length, pos = int.from_bytes(buf[2:10], "big"), 10
    mask = b""
    if masked:
        if len(buf) < pos + 4:
            return None
        mask, pos = buf[pos:pos + 4], pos + 4
    if len(buf) < pos + length:
        return None
    payload = buf[pos:pos + length]
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload, pos + length


def encode_client_frame(opcode: int, payload: bytes, mask: bytes) -> bytes:
    """Encode a final (FIN=1), masked client frame."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
    else:
        head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def infer_pcm_format(byte_count: int, seconds: float) -> str:
    """Closest PCM format by bytes per second; ties go to the first candidate."""
    rate = byte_count / seconds if seconds > 0 else 0.0
    best, best_diff = _FORMATS[0][0], abs(rate - _FORMATS[0][1])
    for label, bps in _FORMATS[1:]:
        diff = abs(rate - bps)
        if diff < best_diff:
            best, best_diff = label, diff
    return best


def _connect(url: str) -> tuple[socket.socket, bytes]:
    """Open the socket and complete the upgrade; returns (socket, bytes read past headers)."""
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise ConnectionError(f"not a ws:// or wss:// URL: {url[:60]}")
    secure = parts.scheme == "wss"
    port = parts.port or (443 if secure else 80)
    sock = socket.create_connection((parts.hostname, port), timeout=10)
    try:
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        host = parts.netloc.rsplit("@", 1)[-1]
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode("ascii"))
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed during WebSocket handshake")
            data += chunk
            if len(data) > 65536:
                raise ConnectionError("oversized handshake response")
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        headers = {k.strip().lower(): v.strip()
                   for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if " 101" not in lines[0] or headers.get("sec-websocket-accept") != ws_accept_key(key):
            raise ConnectionError(f"WebSocket handshake failed: {lines[0]}")
        return sock, rest
    except BaseException:
        sock.close()
        raise


def listen(url: str, out_path: Path, stop: threading.Event) -> int:
    """Record binary frames from `url` into `out_path`; returns bytes written.

    Stops when `stop` is set (checked about once a second) or on a close frame.
    """
    sock, buf = _connect(url)
    written = 0
    try:
        with open(out_path, "wb") as out:
            sock.settimeout(1.0)
            binary = False
            done = False
            while not done and not stop.is_set():
                while (frame := decode_frame(buf)) is not None:
                    opcode, payload, used = frame
                    buf = buf[used:]
                    if opcode == 0x2 or (opcode == 0x0 and binary):
                        binary = True
                        out.write(payload)
                        written += len(payload)
                    elif opcode == 0x1:
                        binary = False
                    elif opcode == 0x9:
                        sock.sendall(encode_client_frame(0xA, payload, os.urandom(4)))
                    elif opcode == 0x8:
                        try:
                            sock.sendall(encode_client_frame(0x8, payload[:2], os.urandom(4)))
                        except OSError:
                            pass
                        done = True
                        break
                if done:
                    break
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
    finally:
        sock.close()
    return written
