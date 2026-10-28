import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from probe.cli import main
from probe.control import add_system_message, say
from probe.eventlog import Event, RunMeta, load_meta, read_events, save_meta
from probe.ping import HOLD_LINE_PREFIX, TIMEOUT_MARKER
from probe.report import (disclosure_ok, first_speech_delay, median, ping_metrics,
                          recording_absent, statuses_seen, summarize, transcript_latencies)
from probe.vapi_api import VapiApi
from tests.helpers import FakeTransport, env_with_runs

FIX = Path(__file__).resolve().parent / "fixtures"


def replace_transcript(e, text):
    if e.kind.startswith("vapi:transcript"):
        return Event(e.t, e.kind, {**e.payload, "transcript": text})
    return e


def ctl(t, msg):
    return Event(t, "control-sent", {"url": "u", "message": msg})


def ev(t, kind, **p):
    return Event(t, kind, p)


class ReportTest(unittest.TestCase):
    def test_happy_run(self):
        e = read_events(FIX / "run_happy.jsonl")
        self.assertTrue(disclosure_ok(e, "Alex Chen"))
        self.assertAlmostEqual(first_speech_delay(e), 1.2)
        self.assertEqual([round(x, 1) for x in transcript_latencies(e)], [0.5, 1.1])
        self.assertEqual(statuses_seen(e), ["in-progress", "ended"])

    def test_disclosure_fails_without_transcribed(self):
        e = [replace_transcript(x, "Hi, I'm an AI assistant for Alex Chen.")
             for x in read_events(FIX / "run_happy.jsonl")]
        self.assertFalse(disclosure_ok(e, "Alex Chen"))

    def test_disclosure_ai_is_whole_case_sensitive_word(self):
        for text in ("Hi, I'm an assistant for Alex Chen. This call is transcribed.",
                     "Hi, I'm an ai for Alex Chen. This call is transcribed.",
                     "Hi, I'm MAIN for Alex Chen. This call is transcribed."):
            e = [replace_transcript(x, text) for x in read_events(FIX / "run_happy.jsonl")]
            self.assertFalse(disclosure_ok(e, "Alex Chen"), text)

    def test_disclosure_uses_first_final_assistant_transcript_only(self):
        e = [ev(1, "vapi:transcript", role="assistant", transcriptType="final",
                transcript="Hello there."),
             ev(2, "vapi:transcript", role="assistant", transcriptType="final",
                transcript="I'm an AI for Alex Chen. This call is transcribed.")]
        self.assertFalse(disclosure_ok(e, "Alex Chen"))

    def test_final_transcript_with_bracketed_kind_counts(self):
        e = [ev(1, 'vapi:transcript[transcriptType="final"]', role="assistant",
                transcriptType="final",
                transcript="Hi, I'm an AI for Alex Chen. This call is transcribed.")]
        self.assertTrue(disclosure_ok(e, "Alex Chen"))

    def test_empty_events(self):
        self.assertIsNone(first_speech_delay([]))
        self.assertEqual(transcript_latencies([]), [])
        self.assertIsNone(median([]))
        self.assertEqual(ping_metrics([]), [])
        self.assertFalse(disclosure_ok([], "Alex Chen"))

    def test_ping_run(self):
        (p,) = ping_metrics(read_events(FIX / "run_ping.jsonl"))
        self.assertEqual((p.fillers, p.outcome, p.stayed_connected), (2, "replied", True))
        self.assertAlmostEqual(p.reply_to_speech_s, 2.1)
        self.assertEqual(p.opened_at, 20.0)

    def test_ping_timeout_and_call_ended_and_failed_sends_ignored(self):
        hold = say(f"{HOLD_LINE_PREFIX} Alex Chen, one moment.")
        e = [ctl(10, hold),
             Event(12, "control-failed", {"url": "u", "message": add_system_message(
                 "Alex Chen replied: x", True)}),
             ctl(55, add_system_message(f"Alex Chen {TIMEOUT_MARKER} 45 seconds.", True)),
             ctl(70, hold),
             ev(80, "vapi:status-update", status="ended")]
        a, b = ping_metrics(e)
        self.assertEqual((a.outcome, a.stayed_connected, a.reply_to_speech_s),
                         ("timeout", True, None))
        self.assertEqual((b.outcome, b.stayed_connected), ("call-ended", False))

    def test_median(self):
        self.assertEqual(median([3.0, 1.0, 2.0]), 2.0)

    def test_recording_absent(self):
        self.assertTrue(recording_absent({"artifact": {"transcript": "x"}}))
        self.assertTrue(recording_absent({}))
        self.assertFalse(recording_absent({"artifact": {"recordingUrl": "https://r"}}))
        self.assertFalse(recording_absent({"artifact": {"stereoRecordingUrl": "https://r"}}))


class SummarizeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runs = Path(tmp.name)
        (self.runs / "c1.jsonl").write_bytes((FIX / "run_happy.jsonl").read_bytes())
        save_meta(self.runs, RunMeta("c1", "a", "+16175550101", "", "", 0.0))

    def api(self, responses):
        return VapiApi("k", "https://api", FakeTransport(responses))

    def test_offline(self):
        s = summarize("c1", self.runs, None)
        self.assertEqual(s["scenario_id"], "a")
        self.assertTrue(s["disclosure_ok"])
        self.assertEqual(s["pings"], [])
        self.assertNotIn("cost", s)
        self.assertNotIn("api_error", s)

    def test_with_api_stores_cost(self):
        body = b'{"cost": 0.42, "endedReason": "customer-ended-call", "artifact": {}}'
        s = summarize("c1", self.runs, self.api([(200, body)]))
        self.assertEqual((s["cost"], s["ended_reason"], s["recording_absent"]),
                         (0.42, "customer-ended-call", True))
        self.assertEqual(load_meta(self.runs, "c1").cost, 0.42)

    def test_with_api_missing_cost_is_none(self):
        s = summarize("c1", self.runs, self.api([(200, b'{"artifact": {"recordingUrl": "r"}}')]))
        self.assertIsNone(s["cost"])
        self.assertFalse(s["recording_absent"])

    def test_api_error_does_not_raise(self):
        for resp in ((500, b"boom"), OSError("down")):
            s = summarize("c1", self.runs, self.api([resp]))
            self.assertIn("api_error", s)
            self.assertNotIn("cost", s)
        self.assertIsNone(load_meta(self.runs, "c1").cost)

    def test_pings_are_plain_dicts(self):
        (self.runs / "c1.jsonl").write_bytes((FIX / "run_ping.jsonl").read_bytes())
        s = summarize("c1", self.runs, None)
        self.assertEqual(s["pings"][0]["outcome"], "replied")
        self.assertIsInstance(s["pings"][0], dict)


class ReportCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runs = Path(tmp.name)
        self.env = env_with_runs(self.runs)
        (self.runs / "c1.jsonl").write_bytes((FIX / "run_happy.jsonl").read_bytes())
        save_meta(self.runs, RunMeta("c1", "a", "+16175550101", "", "", 0.0, cost=1.5))
        save_meta(self.runs, RunMeta("c2", "b", "+16175550101", "", "", 0.0))

    def run_main(self, argv, transport=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, env=self.env, transport=transport or FakeTransport([]))
        return code, out.getvalue(), err.getvalue()

    def test_report_one_offline(self):
        t = FakeTransport([])
        code, out, _ = self.run_main(["report", "c1", "--offline"], t)
        self.assertEqual(code, 0)
        self.assertIn("c1", out)
        self.assertIn("disclosure first: yes", out)
        self.assertIn("statuses: in-progress, ended", out)
        self.assertEqual(t.calls, [])

    def test_report_one_with_api(self):
        body = b'{"cost": 0.25, "endedReason": "hangup", "artifact": {}}'
        t = FakeTransport([(200, body)])
        code, out, _ = self.run_main(["report", "c1"], t)
        self.assertEqual(code, 0)
        self.assertIn("cost: $0.2500", out)
        self.assertIn("hangup", out)
        self.assertIn("recording absent: yes", out)
        self.assertEqual(load_meta(self.runs, "c1").cost, 0.25)

    def test_report_api_error_still_prints(self):
        code, out, _ = self.run_main(["report", "c1"], FakeTransport([(500, b"x")]))
        self.assertEqual(code, 0)
        self.assertIn("api error", out)

    def test_report_unknown_call_exits_2(self):
        code, _, err = self.run_main(["report", "nope", "--offline"])
        self.assertEqual(code, 2)
        self.assertIn("nope", err)

    def test_report_all_lists_calls_and_total(self):
        code, out, _ = self.run_main(["report", "--offline"])
        self.assertEqual(code, 0)
        self.assertIn("c1", out)
        self.assertIn("c2", out)
        self.assertIn("spent $1.50 of $20.00 cap", out)


if __name__ == "__main__":
    unittest.main()
