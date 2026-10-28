import tempfile
import unittest
from pathlib import Path

from probe.control import add_system_message, say
from probe.eventlog import EventLog, RunMeta, load_meta, read_events, running_cost, save_meta
from probe.ping import (FILLER_LINES, HOLD_LINE_PREFIX, REPLY_MARKER, TIMEOUT_MARKER,
                        PingSession)

ARGS = {"question": "Accept Thu 4pm?", "reason": "They offered 4pm. You said before 3pm.",
        "suggested_replies": ["Accept 4pm", "Ask for Fri", "Decline"]}


class PingSessionTest(unittest.TestCase):
    def setUp(self):
        self.p = PingSession("Alex")

    def test_constants(self):
        self.assertEqual(len(FILLER_LINES), 3)
        self.assertTrue(all("{user}" in line for line in FILLER_LINES))
        self.assertEqual(HOLD_LINE_PREFIX, "Let me check with")
        self.assertEqual(REPLY_MARKER, " replied: ")
        self.assertEqual(TIMEOUT_MARKER, "did not reply within")

    def test_ask_opens_with_hold_line(self):
        self.assertEqual(self.p.on_ask(ARGS, now=100.0),
                         [say("Let me check with Alex, one moment.")])
        self.assertTrue(self.p.is_open)
        self.assertEqual(self.p.view.reasons, [ARGS["reason"]])
        self.assertEqual(self.p.view.opened_at, 100.0)

    def test_filler_every_15_seconds(self):
        self.p.on_ask(ARGS, now=100.0)
        self.assertEqual(self.p.tick(114.9), [])
        (m,) = self.p.tick(115.0)
        self.assertEqual(m["type"], "say")
        self.assertIn("Alex", m["content"])
        self.assertEqual(self.p.tick(120.0), [])
        (m,) = self.p.tick(130.0)
        self.assertEqual(m["type"], "say")

    def test_late_tick_sends_at_most_one_filler(self):
        self.p.on_ask(ARGS, now=100.0)
        self.assertEqual(len(self.p.tick(135.0)), 1)
        self.assertEqual(len(self.p.tick(136.0)), 1)
        self.assertEqual(self.p.tick(137.0), [])

    def test_reply_injects_and_closes(self):
        self.p.on_ask(ARGS, now=100.0)
        self.assertEqual(self.p.on_reply("Ask for Fri", now=110.0),
                         [add_system_message("Alex replied: Ask for Fri", trigger_response=True)])
        self.assertFalse(self.p.is_open)
        self.assertIsNone(self.p.view)
        self.assertEqual(self.p.tick(200.0), [])

    def test_digit_selects_suggested_reply(self):
        self.p.on_ask(ARGS, now=100.0)
        (m,) = self.p.on_reply(" 2 ", now=101.0)
        self.assertEqual(m["message"]["content"], "Alex replied: Ask for Fri")

    def test_digit_out_of_range_is_typed_as_is(self):
        self.p.on_ask(ARGS, now=100.0)
        (m,) = self.p.on_reply("4", now=101.0)
        self.assertEqual(m["message"]["content"], "Alex replied: 4")

    def test_timeout_at_45_seconds_wins_over_filler(self):
        self.p.on_ask(ARGS, now=100.0)
        self.p.tick(115.0)
        self.p.tick(130.0)
        self.assertEqual(self.p.tick(144.9), [])
        msgs = self.p.tick(145.0)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["type"], "add-message")
        self.assertIn("did not reply within 45 seconds", msgs[0]["message"]["content"])
        self.assertIn("fallback", msgs[0]["message"]["content"])
        self.assertIs(msgs[0]["triggerResponseEnabled"], True)
        self.assertFalse(self.p.is_open)

    def test_very_late_tick_returns_only_timeout(self):
        self.p.on_ask(ARGS, now=100.0)
        msgs = self.p.tick(500.0)
        self.assertEqual(len(msgs), 1)
        self.assertIn(TIMEOUT_MARKER, msgs[0]["message"]["content"])

    def test_late_empty_and_unprompted_replies_are_ignored_with_notice(self):
        self.assertEqual(self.p.on_reply("hello", now=1.0), [])
        self.p.on_ask(ARGS, now=100.0)
        self.assertEqual(self.p.on_reply("   ", now=101.0), [])
        self.assertTrue(self.p.is_open)
        self.p.tick(145.0)
        self.assertEqual(self.p.on_reply("Accept 4pm", now=146.0), [])
        self.assertEqual(len(self.p.notices), 3)
        self.assertIn("timed out", self.p.notices[2])
        self.assertNotIn("timed out", self.p.notices[0])

    def test_second_ask_merges_and_keeps_original_timer(self):
        self.p.on_ask(ARGS, now=100.0)
        self.assertEqual(self.p.on_ask({**ARGS, "question": "Share DOB?"}, now=120.0), [])
        self.assertEqual(self.p.view.questions, ["Accept Thu 4pm?", "Share DOB?"])
        self.assertEqual(self.p.view.reasons, [ARGS["reason"]])
        self.assertEqual(self.p.view.suggested_replies, ARGS["suggested_replies"])
        self.assertEqual(self.p.tick(145.0)[-1]["type"], "add-message")

    def test_malformed_args_do_not_raise(self):
        self.assertEqual(len(self.p.on_ask({"question": 5, "reason": None,
                                            "suggested_replies": "nope"}, now=1.0)), 1)
        self.assertEqual(self.p.view.suggested_replies, [])
        p2 = PingSession("Alex")
        p2.on_ask({"suggested_replies": ["ok", 3]}, now=1.0)
        self.assertEqual(p2.view.questions, [""])
        self.assertEqual(p2.view.suggested_replies, ["ok"])


class EventLogTest(unittest.TestCase):
    def test_append_and_read_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "c1.jsonl"
            log = EventLog(path, clock=lambda: 42.0)
            log.append("a", {"x": 1})
            log.append("b", {})
            with path.open("a") as f:
                f.write("\n")
            ev = read_events(path)
            self.assertEqual([(e.t, e.kind, e.payload) for e in ev],
                             [(42.0, "a", {"x": 1}), (42.0, "b", {})])
            meta = RunMeta("c1", "a_restaurant", "+16175550101", "http://c", "ws://l", 5.0)
            save_meta(Path(d), meta)
            self.assertEqual(load_meta(Path(d), "c1"), meta)
            self.assertIsNone(load_meta(Path(d), "nope"))

    def test_running_cost_sums_meta(self):
        with tempfile.TemporaryDirectory() as d:
            for i, c in enumerate([1.25, 0.5, None]):
                save_meta(Path(d), RunMeta(f"c{i}", "s", "+1", "u", "l", 0.0, c))
            self.assertEqual(running_cost(Path(d)), 1.75)
            self.assertEqual(running_cost(Path(d) / "missing"), 0.0)


if __name__ == "__main__":
    unittest.main()
