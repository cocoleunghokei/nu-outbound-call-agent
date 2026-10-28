import contextlib
import io
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from probe.cli import main
from probe.eventlog import RunMeta, save_meta
from probe.listen import (decode_frame, encode_client_frame, infer_pcm_format, listen,
                          ws_accept_key)
from tests.helpers import env_with_runs


class PureFunctionsTest(unittest.TestCase):
    def test_accept_key_rfc6455_example(self):
        self.assertEqual(ws_accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_decode_small_binary_frame(self):
        self.assertEqual(decode_frame(b"\x82\x03abc"), (0x2, b"abc", 5))

    def test_decode_16bit_length_and_incomplete(self):
        frame = b"\x82\x7e\x01\x00" + b"x" * 256
        self.assertEqual(decode_frame(frame)[2], 260)
        self.assertIsNone(decode_frame(frame[:100]))

    def test_decode_64bit_length(self):
        frame = b"\x82\x7f" + (70000).to_bytes(8, "big") + b"y" * 70000
        op, payload, used = decode_frame(frame)
        self.assertEqual((op, len(payload), used), (0x2, 70000, 10 + 70000))

    def test_decode_masked_frame(self):
        mask = b"\x01\x02\x03\x04"
        self.assertEqual(decode_frame(encode_client_frame(0x1, b"hello", mask))[:2],
                         (0x1, b"hello"))

    def test_decode_incomplete_header(self):
        self.assertIsNone(decode_frame(b""))
        self.assertIsNone(decode_frame(b"\x82"))
        self.assertIsNone(decode_frame(b"\x82\x7e\x01"))

    def test_encode_masks_payload_and_sets_fin(self):
        frame = encode_client_frame(0xA, b"ab", b"\xff\xff\xff\xff")
        self.assertEqual(frame, b"\x8a\x82\xff\xff\xff\xff" + bytes([0x61 ^ 0xFF, 0x62 ^ 0xFF]))

    def test_infer_pcm_format(self):
        self.assertEqual(infer_pcm_format(32000 * 10, 10.0), "16-bit mono 16000 Hz")
        self.assertEqual(infer_pcm_format(32000 * 10, 10.0 * 2), "16-bit mono 8000 Hz")

    def test_infer_pcm_format_other_rates(self):
        self.assertEqual(infer_pcm_format(96000 * 5, 5.0), "16-bit stereo 24000 Hz")
        self.assertEqual(infer_pcm_format(48000 * 5, 5.0), "16-bit mono 24000 Hz")
        self.assertEqual(infer_pcm_format(64000 * 5, 5.0), "16-bit stereo 16000 Hz")

    def test_infer_pcm_format_zero_seconds(self):
        self.assertEqual(infer_pcm_format(0, 0.0), "16-bit mono 8000 Hz")


def _serve_once(listener: socket.socket, frames: list[bytes], seen: dict) -> None:
    conn, _ = listener.accept()
    with conn:
        conn.settimeout(5)
        data = b""
        while b"\r\n\r\n" not in data:
            data += conn.recv(4096)
        seen["request"] = data.decode("latin-1")
        key = next(line.split(":", 1)[1].strip() for line in seen["request"].split("\r\n")
                   if line.lower().startswith("sec-websocket-key"))
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Accept: "
                      f"{ws_accept_key(key)}\r\n\r\n").encode())
        for f in frames:
            conn.sendall(f)
        try:  # wait for the client's pong/close reply, then hang up
            seen["reply"] = conn.recv(4096)
        except OSError:
            pass


class LoopbackTest(unittest.TestCase):
    def run_server(self, frames, bad_accept=False):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        seen: dict = {}
        t = threading.Thread(target=_serve_once, args=(listener, frames, seen))
        t.start()
        self.addCleanup(t.join, 5)
        return f"ws://127.0.0.1:{listener.getsockname()[1]}/listen/abc?x=1", seen, t

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / "a.pcm"

    def test_writes_binary_frames_and_stops_on_close(self):
        frames = [b"\x82\x03abc", b"\x02\x02de", b"\x80\x01f",  # binary + continuation
                  b"\x81\x02hi",                                 # text, ignored
                  b"\x89\x02pp",                                 # ping
                  b"\x88\x00"]
        url, seen, t = self.run_server(frames)
        n = listen(url, self.out, threading.Event())
        t.join(5)
        self.assertEqual(n, 6)
        self.assertEqual(self.out.read_bytes(), b"abcdef")
        self.assertIn("GET /listen/abc?x=1 HTTP/1.1", seen["request"])
        self.assertIn("Sec-WebSocket-Version: 13", seen["request"])
        op, payload, _ = decode_frame(seen["reply"])
        self.assertEqual((op, payload), (0xA, b"pp"))

    def test_rejects_non_101(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        def refuse():
            conn, _ = listener.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")

        t = threading.Thread(target=refuse)
        t.start()
        self.addCleanup(t.join, 5)
        with self.assertRaises(ConnectionError) as cm:
            listen(f"ws://127.0.0.1:{listener.getsockname()[1]}/x", self.out, threading.Event())
        self.assertIn("403", str(cm.exception))


class ListenCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runs = Path(tmp.name)
        self.env = env_with_runs(self.runs)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, env=self.env)
        return code, out.getvalue(), err.getvalue()

    def test_unknown_call_exits_2(self):
        code, _, err = self.run_main(["listen", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("unknown call", err)

    def test_empty_listen_url_exits_2(self):
        save_meta(self.runs, RunMeta("c1", "a", "+16175550101", "https://ctl", "", 0.0))
        code, _, err = self.run_main(["listen", "c1"])
        self.assertEqual(code, 2)
        self.assertIn("listen URL", err)

    def test_listens_and_reports_format(self):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        seen: dict = {}
        t = threading.Thread(target=_serve_once,
                             args=(listener, [b"\x82\x04abcd", b"\x88\x00"], seen))
        t.start()
        self.addCleanup(t.join, 5)
        url = f"ws://127.0.0.1:{listener.getsockname()[1]}/l"
        save_meta(self.runs, RunMeta("c2", "a", "+16175550101", "https://ctl", url, 0.0))
        code, out, _ = self.run_main(["listen", "c2"])
        self.assertEqual(code, 0)
        self.assertEqual((self.runs / "c2.pcm").read_bytes(), b"abcd")
        self.assertIn("4 bytes", out)
        self.assertIn("16-bit", out)

    def test_connection_error_exits_1(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listens here now
        save_meta(self.runs, RunMeta("c3", "a", "+16175550101", "https://ctl",
                                     f"ws://127.0.0.1:{port}/l", 0.0))
        code, _, err = self.run_main(["listen", "c3"])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error:"))


if __name__ == "__main__":
    unittest.main()
