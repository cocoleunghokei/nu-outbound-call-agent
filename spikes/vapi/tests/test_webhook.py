import http.client
import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from probe.control import say
from probe.eventlog import RunMeta, read_events, save_meta
from probe.webhook import ProbeServer
from tests.helpers import FakeClock, FakeControl

SECRET = "s3cret"
ARGS = {"question": "Accept Thu 4pm?", "reason": "They offered 4pm. You said before 3pm.",
        "suggested_replies": ["Accept 4pm", "Ask for Fri", "Decline"]}
TOOL_CALL_BODY = {"message": {
    "type": "tool-calls", "call": {"id": "call_1"},
    "toolCallList": [{"id": "tc_1", "type": "function",
                      "function": {"name": "ask_user", "arguments": json.dumps(ARGS)}}],
    "toolWithToolCallList": []}}
TRANSCRIPT_BODY = {"message": {"type": "transcript", "call": {"id": "call_1"}, "role": "user",
                               "transcriptType": "final", "transcript": "We have Thursday at 4."}}


class Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body


class WebhookTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runs = Path(tmp.name)
        save_meta(self.runs, RunMeta(call_id="call_1", scenario_id="s1", to="+16175550101",
                                     control_url="https://ctl/1", listen_url="wss://l/1",
                                     started_at=0.0))
        self.control, self.clock, self.printed = FakeControl(), FakeClock(), []
        self.srv = ProbeServer(SECRET, self.runs, self.control, "Alex Chen",
                               clock=self.clock, out=self.printed.append)
        self.port = self.srv.start(port=0)
        self.addCleanup(self.srv.stop)

    def post_raw(self, body, secret=SECRET, path="/webhook", method="POST"):
        headers = {"Content-Type": "application/json"}
        if secret is not None:
            headers["x-probe-secret"] = secret
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = Resp(r.status, r.read())
        except urllib.error.HTTPError as e:
            with e:
                resp = Resp(e.code, e.read())
        self.srv.drain()
        return resp

    def post(self, obj, secret=SECRET):
        return self.post_raw(json.dumps(obj).encode(), secret=secret)

    def test_wrong_secret_is_401_and_nothing_sent(self):
        self.assertEqual(self.post(TOOL_CALL_BODY, secret="nope").status, 401)
        self.assertEqual(self.control.sent, [])
        self.assertEqual([e.kind for e in read_events(self.runs / "unknown.jsonl")], ["rejected"])

    def test_missing_secret_is_401(self):
        self.assertEqual(self.post(TOOL_CALL_BODY, secret=None).status, 401)
        self.assertFalse((self.runs / "call_1.jsonl").exists())

    def test_ask_user_returns_result_and_sends_hold_line(self):
        resp = self.post(TOOL_CALL_BODY)
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.body), {"results": [
            {"name": "ask_user", "toolCallId": "tc_1",
             "result": "Waiting for Alex Chen to reply."}]})
        self.assertEqual(self.control.sent,
                         [("https://ctl/1", say("Let me check with Alex Chen, one moment."))])

    def test_ask_prints_question_reason_and_numbered_replies(self):
        self.post(TOOL_CALL_BODY)
        text = "\n".join(self.printed)
        for s in (ARGS["question"], ARGS["reason"], "1. Accept 4pm", "3. Decline", "type 1-3"):
            self.assertIn(s, text)

    def test_second_ask_merges_and_sends_nothing_more(self):
        self.post(TOOL_CALL_BODY)
        body = json.loads(json.dumps(TOOL_CALL_BODY))
        body["message"]["toolCallList"][0]["id"] = "tc_2"
        body["message"]["toolCallList"][0]["function"]["arguments"] = json.dumps(
            {"question": "Fri 9am?", "reason": "New offer.", "suggested_replies": ["Yes"]})
        resp = self.post(body)
        self.assertEqual(json.loads(resp.body)["results"][0]["toolCallId"], "tc_2")
        self.assertEqual(len(self.control.sent), 1)
        self.assertIn("type 1-4", self.printed[-1])

    def test_reply_after_ask_injects_into_call(self):
        self.post(TOOL_CALL_BODY)
        self.srv.reply("Ask for Fri")
        self.srv.drain()
        self.assertEqual(self.control.sent[-1][1]["type"], "add-message")
        self.assertIn("Alex Chen replied: Ask for Fri",
                      self.control.sent[-1][1]["message"]["content"])

    def test_numbered_reply_picks_suggestion(self):
        self.post(TOOL_CALL_BODY)
        self.srv.reply("2")
        self.srv.drain()
        self.assertIn("replied: Ask for Fri", self.control.sent[-1][1]["message"]["content"])

    def test_reply_with_no_ping_prints_each_notice_once(self):
        self.post(TRANSCRIPT_BODY)
        self.srv.reply("hello")
        self.srv.reply("again")
        self.srv.tick()
        self.srv.drain()
        self.assertEqual(self.control.sent, [])
        self.assertEqual(sum("no ping is open" in p for p in self.printed), 2)

    def test_tick_sends_filler_then_timeout(self):
        self.post(TOOL_CALL_BODY)
        self.clock.now += 15
        self.srv.tick()
        self.clock.now += 30
        self.srv.tick()
        self.srv.drain()
        self.assertEqual([m["type"] for _, m in self.control.sent], ["say", "say", "add-message"])
        self.srv.reply("too late")
        self.assertTrue(any("timed out" in p for p in self.printed))

    def test_tick_prints_seconds_left_tersely(self):
        self.post(TOOL_CALL_BODY)
        before = len(self.printed)
        for _ in range(4):
            self.clock.now += 1
            self.srv.tick()
        self.clock.now += 1
        self.srv.tick()
        self.assertEqual(self.printed[before:], ["40s left"])

    def test_events_are_logged_per_call(self):
        self.post(TRANSCRIPT_BODY)
        events = read_events(self.runs / "call_1.jsonl")
        self.assertEqual([e.kind for e in events], ["vapi:transcript"])
        self.assertEqual(events[0].payload, TRANSCRIPT_BODY["message"])

    def test_sends_are_logged_to_call_log(self):
        self.post(TOOL_CALL_BODY)
        kinds = [e.kind for e in read_events(self.runs / "call_1.jsonl")]
        self.assertEqual(kinds, ["vapi:tool-calls", "control-sent"])

    def test_other_type_is_200_empty(self):
        resp = self.post({"message": {"type": "status-update", "call": {"id": "call_1"}}})
        self.assertEqual((resp.status, json.loads(resp.body)), (200, {}))

    def test_tool_calls_without_ask_user_has_empty_results(self):
        body = {"message": {"type": "tool-calls", "call": {"id": "call_1"}, "toolCallList": [
            {"id": "tc_9", "type": "function", "function": {"name": "other", "arguments": "{}"}}]}}
        self.assertEqual(json.loads(self.post(body).body), {"results": []})
        self.assertEqual(self.control.sent, [])

    def test_bad_arguments_json_still_opens_ping(self):
        body = json.loads(json.dumps(TOOL_CALL_BODY))
        body["message"]["toolCallList"][0]["function"]["arguments"] = "{oops"
        self.assertEqual(len(json.loads(self.post(body).body)["results"]), 1)
        self.assertTrue(self.srv.ping_for("call_1").is_open)

    # Review Focus 4
    def test_malformed_json_is_400_and_server_keeps_running(self):
        self.assertEqual(self.post_raw(b"{not json").status, 400)
        self.assertEqual(self.post(TRANSCRIPT_BODY).status, 200)
        self.assertEqual([e.kind for e in read_events(self.runs / "unknown.jsonl")],
                         ["bad-request"])

    def test_other_path_or_method_is_404(self):
        self.assertEqual(self.post_raw(b"{}", path="/nope").status, 404)
        self.assertEqual(self.post_raw(None, method="GET").status, 404)

    def test_unknown_call_is_logged_not_sent(self):
        self.post({**TOOL_CALL_BODY,
                   "message": {**TOOL_CALL_BODY["message"], "call": {"id": "call_x"}}})
        self.assertEqual(self.control.sent, [])
        self.assertEqual([e.kind for e in read_events(self.runs / "call_x.jsonl")],
                         ["vapi:tool-calls"])
        self.assertFalse((self.runs / "unknown.jsonl").exists())
        self.assertIn("no control URL for call call_x", self.printed)

    def test_invalid_call_id_logs_to_unknown(self):
        self.post({"message": {"type": "status-update", "call": {"id": "../x"}}})
        self.assertEqual([e.kind for e in read_events(self.runs / "unknown.jsonl")],
                         ["vapi:status-update"])

    def test_early_events_then_meta_recovers_sending(self):
        # R13: webhooks can arrive before `call` saves the meta.
        self.post({"message": {"type": "status-update", "call": {"id": "call_2"}}})
        self.assertTrue((self.runs / "call_2.jsonl").exists())
        save_meta(self.runs, RunMeta(call_id="call_2", scenario_id="s1", to="+16175550101",
                                     control_url="https://ctl/2", listen_url="wss://l/2",
                                     started_at=0.0))
        self.post({**TOOL_CALL_BODY,
                   "message": {**TOOL_CALL_BODY["message"], "call": {"id": "call_2"}}})
        self.assertEqual(self.control.sent[0][0], "https://ctl/2")

    def test_empty_meta_file_does_not_break_tool_calls_or_tick(self):
        (self.runs / "call_1.meta.json").write_text("", encoding="utf-8")  # torn mid-write
        resp = self.post(TOOL_CALL_BODY)
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(resp.body)["results"][0]["toolCallId"], "tc_1")
        self.clock.now += 15
        self.srv.tick()  # must not raise
        self.assertIn("no control URL for call call_1", self.printed)
        save_meta(self.runs, RunMeta(call_id="call_1", scenario_id="s1", to="+16175550101",
                                     control_url="https://ctl/1", listen_url="wss://l/1",
                                     started_at=0.0))
        self.clock.now += 15
        self.srv.tick()
        self.srv.drain()
        self.assertEqual([u for u, _ in self.control.sent], ["https://ctl/1"])

    def raw_request(self, headers: dict, body: bytes):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/webhook")
            for k, v in headers.items():
                conn.putheader(k, v)
            conn.endheaders()
            conn.send(body)
            start = time.monotonic()
            resp = conn.getresponse()
            status = resp.status
            resp.read()
            return status, time.monotonic() - start
        finally:
            conn.close()

    def test_oversized_declared_length_is_413(self):
        status, _ = self.raw_request({"x-probe-secret": SECRET,
                                      "Content-Length": str(300 * 1024 * 1024)}, b"{}")
        self.assertEqual(status, 413)
        self.assertEqual(read_events(self.runs / "unknown.jsonl")[0].kind, "bad-request")

    def test_bad_secret_is_401_without_reading_body(self):
        status, took = self.raw_request({"x-probe-secret": "nope",
                                         "Content-Length": "100000"}, b"{}")
        self.assertEqual(status, 401)
        self.assertLess(took, 2.0)

    def test_non_integer_content_length_is_400(self):
        status, _ = self.raw_request({"x-probe-secret": SECRET,
                                      "Content-Length": "lots"}, b"{}")
        self.assertEqual(status, 400)
        self.assertEqual(read_events(self.runs / "unknown.jsonl")[0].kind, "bad-request")

    def test_missing_content_length_is_400(self):
        status, _ = self.raw_request({"x-probe-secret": SECRET}, b"")
        self.assertEqual(status, 400)

    # Review Focus 5
    def test_timeout_still_fires_when_control_sends_fail(self):
        self.control.fail = True
        self.post(TOOL_CALL_BODY)                      # hold line fails to send
        self.clock.now += 45
        self.srv.tick()
        self.srv.drain()
        self.assertFalse(self.srv.ping_for("call_1").is_open)
        kinds = [e.kind for e in read_events(self.runs / "call_1.jsonl")]
        self.assertEqual(kinds.count("control-failed"), 2)


if __name__ == "__main__":
    unittest.main()
