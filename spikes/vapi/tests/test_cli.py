import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from probe.cli import main
from probe.control import (add_system_message, mute_assistant, say, transfer_to,
                           unmute_assistant)
from probe.eventlog import RunMeta, load_meta, read_events, save_meta
from tests.helpers import FakeTransport, env_with_runs


class CliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runs = Path(tmp.name)
        self.env = env_with_runs(self.runs)

    def run_main(self, argv, transport, **kw):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, env=self.env, transport=transport, **kw)
        return code, out.getvalue(), err.getvalue()

    def save(self, control_url="https://ctl/9"):
        save_meta(self.runs, RunMeta("call_9", "a", "+16175550101", control_url, "wss://l", 0.0))

    def bodies(self, t):
        return [json.loads(c[3]) for c in t.calls]

    # Review Focus 1
    def test_call_refuses_number_not_on_allowlist(self):
        t = FakeTransport([])
        code, _, err = self.run_main(["call", "a", "--to", "+16175550123"], t)
        self.assertEqual(code, 2)
        self.assertIn("allow", err)
        self.assertEqual(t.calls, [])

    def test_call_refuses_emergency_number(self):
        t = FakeTransport([])
        code, _, err = self.run_main(["call", "a", "--to", "911"], t)
        self.assertEqual(code, 2)
        self.assertIn("emergency", err)
        self.assertEqual(t.calls, [])

    def test_call_refuses_malformed_and_non_us_numbers(self):
        for number in ("6175550101", "+161755501", "+442071234567"):
            with self.subTest(number=number):
                t = FakeTransport([])
                code, _, err = self.run_main(["call", "a", "--to", number], t)
                self.assertEqual(code, 2)
                self.assertIn("not a US E.164 number", err)
                self.assertEqual(t.calls, [])

    def test_call_response_without_id_exits_1_and_says_may_have_been_placed(self):
        t = FakeTransport([(201, b'{"status": "queued"}')])
        code, _, err = self.run_main(["call", "a", "--to", "+16175550101"], t)
        self.assertEqual(code, 1)
        self.assertIn("may have been placed", err.lower())
        self.assertIn("queued", err)

    def test_call_monitor_null_saves_meta_and_warns(self):
        t = FakeTransport([(201, b'{"id": "call_8", "monitor": null}')])
        code, out, err = self.run_main(["call", "a", "--to", "+16175550101"], t)
        self.assertEqual(code, 0)
        self.assertIn("call_8", out)
        self.assertIn("live control is unavailable", err)
        meta = load_meta(self.runs, "call_8")
        self.assertEqual((meta.control_url, meta.listen_url), ("", ""))

    def test_call_refuses_when_spend_cap_reached(self):
        save_meta(self.runs, RunMeta("old", "a", "+16175550101", "u", "l", 0.0, cost=20.0))
        t = FakeTransport([])
        code, _, err = self.run_main(["call", "a", "--to", "+16175550101"], t)
        self.assertEqual(code, 2)
        self.assertIn("spend cap", err)
        self.assertEqual(t.calls, [])

    def test_call_unknown_scenario_lists_ids(self):
        t = FakeTransport([])
        code, _, err = self.run_main(["call", "zzz", "--to", "+16175550101"], t)
        self.assertEqual(code, 2)
        for sid in ("a", "b", "c", "d", "e"):
            self.assertIn(sid, err.split("valid ids:")[-1].replace(",", " ").split())
        self.assertEqual(t.calls, [])

    def test_call_missing_settings_exits_2(self):
        self.env = {"PROBE_RUNS_DIR": str(self.runs)}
        code, _, err = self.run_main(["call", "a", "--to", "+16175550101"], FakeTransport([]))
        self.assertEqual(code, 2)
        self.assertIn("VAPI_API_KEY", err)

    def test_call_saves_meta_with_monitor_urls(self):
        t = FakeTransport([(201, json.dumps({"id": "call_9", "monitor": {
            "controlUrl": "https://ctl/9", "listenUrl": "wss://lst/9"}}).encode())])
        code, out, _ = self.run_main(["call", "b", "--to", "+16175550101"], t)
        self.assertEqual(code, 0)
        self.assertIn("call_9", out)
        meta = load_meta(self.runs, "call_9")
        self.assertEqual(meta.control_url, "https://ctl/9")
        self.assertEqual(meta.listen_url, "wss://lst/9")
        self.assertEqual(meta.scenario_id, "b")

    def test_call_transport_timeout_warns_call_may_be_live(self):
        t = FakeTransport([OSError("timed out")])
        code, _, err = self.run_main(["call", "a", "--to", "+16175550101"], t)
        self.assertEqual(code, 1)
        self.assertIn("may have been placed", err.lower())
        self.assertIn("OSError", err)

    def test_call_non_json_2xx_warns_call_may_be_live(self):
        t = FakeTransport([(201, b"not json")])
        code, _, err = self.run_main(["call", "a", "--to", "+16175550101"], t)
        self.assertEqual(code, 1)
        self.assertIn("may have been placed", err.lower())

    def test_call_vapi_400_says_refused(self):
        t = FakeTransport([(400, b'{"message":"bad"}')])
        code, _, err = self.run_main(["call", "a", "--to", "+16175550101"], t)
        self.assertEqual(code, 1)
        self.assertIn("refused", err)
        self.assertNotIn("may have been placed", err.lower())

    def test_call_api_error_exits_1(self):
        t = FakeTransport([(500, b"boom")])
        code, _, err = self.run_main(["call", "a", "--to", "+16175550101"], t)
        self.assertEqual(code, 1)
        self.assertIn("500", err)

    def test_control_unknown_call_exits_2(self):
        code, _, err = self.run_main(["control", "nope", "pause"], FakeTransport([]))
        self.assertEqual(code, 2)
        self.assertIn("unknown call nope", err)

    def test_control_empty_control_url_exits_2(self):
        self.save(control_url="")
        t = FakeTransport([])
        self.assertEqual(self.run_main(["control", "call_9", "pause"], t)[0], 2)
        self.assertEqual(t.calls, [])

    def test_control_pause_sends_hold_then_mute(self):
        self.save()
        t = FakeTransport([(200, b"{}"), (200, b"{}")])
        self.assertEqual(self.run_main(["control", "call_9", "pause"], t)[0], 0)
        self.assertEqual(self.bodies(t), [
            say("Could you hold for just a moment, please?"), mute_assistant()])
        self.assertTrue(read_events(self.runs / "call_9.jsonl"))

    def test_control_resume(self):
        self.save()
        t = FakeTransport([(200, b"{}"), (200, b"{}")])
        self.assertEqual(self.run_main(["control", "call_9", "resume"], t)[0], 0)
        self.assertEqual(self.bodies(t), [
            unmute_assistant(),
            add_system_message("Alex is back. Continue the call.", trigger_response=True)])

    def test_control_instruct(self):
        self.save()
        t = FakeTransport([(200, b"{}")])
        self.assertEqual(
            self.run_main(["control", "call_9", "instruct", "ask for Friday"], t)[0], 0)
        self.assertEqual(self.bodies(t), [add_system_message(
            "Alex's instruction: ask for Friday. Work it in at the next natural turn.",
            trigger_response=False)])

    def test_control_end_says_goodbye_then_hangs_up(self):
        self.save()
        t = FakeTransport([(200, b"{}")])
        self.assertEqual(self.run_main(["control", "call_9", "end"], t)[0], 0)
        self.assertEqual(self.bodies(t), [
            say("Thanks so much for your help. Goodbye!", end_call_after=True)])

    def test_control_transfer_uses_operator_number(self):
        self.save()
        t = FakeTransport([(200, b"{}")])
        self.assertEqual(self.run_main(["control", "call_9", "transfer"], t)[0], 0)
        self.assertEqual(self.bodies(t), [transfer_to("+16175550199")])

    def test_control_send_failure_exits_1(self):
        self.save()
        t = FakeTransport([(500, b"x"), (500, b"x")])
        self.assertEqual(self.run_main(["control", "call_9", "end"], t)[0], 1)

    def test_serve_starts_and_exits_cleanly_on_eof(self):
        code, out, _ = self.run_main(["serve", "--port", "0"], FakeTransport([]),
                                     stdin=io.StringIO())
        self.assertEqual(code, 0)
        self.assertIn("/webhook", out)


if __name__ == "__main__":
    unittest.main()
