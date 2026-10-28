import json
import unittest

from probe.config import ConfigError
from probe.control import (ControlClient, ControlError, add_system_message, mute_assistant,
                           say, transfer_to, unmute_assistant)
from probe.vapi_api import VapiApi
from tests.helpers import FakeTransport


class BuildersTest(unittest.TestCase):
    def test_say_and_goodbye(self):
        self.assertEqual(say("hi"), {"type": "say", "content": "hi", "endCallAfterSpoken": False})
        self.assertTrue(say("bye", end_call_after=True)["endCallAfterSpoken"])

    def test_add_system_message(self):
        self.assertEqual(add_system_message("x", trigger_response=True), {
            "type": "add-message", "message": {"role": "system", "content": "x"},
            "triggerResponseEnabled": True})

    def test_mute_unmute_transfer(self):
        self.assertEqual(mute_assistant(), {"type": "control", "control": "mute-assistant"})
        self.assertEqual(unmute_assistant(), {"type": "control", "control": "unmute-assistant"})
        self.assertEqual(transfer_to("+16175550199"),
                         {"type": "transfer", "destination": {"type": "number", "number": "+16175550199"}})
        with self.assertRaises(ConfigError):
            transfer_to("911")


class ControlClientTest(unittest.TestCase):
    def test_retries_once_on_5xx_then_succeeds(self):
        t = FakeTransport([(502, b""), (200, b"{}")]); logged = []
        ok = ControlClient(t).send("https://c/u", say("hi"), lambda k, p: logged.append(k))
        self.assertTrue(ok); self.assertEqual(len(t.calls), 2); self.assertEqual(logged, ["control-sent"])

    def test_two_failures_return_false_and_log(self):
        t = FakeTransport([OSError("timeout"), OSError("timeout")]); logged = []
        ok = ControlClient(t).send("https://c/u", say("hi"), lambda k, p: logged.append(k))
        self.assertFalse(ok); self.assertEqual(logged, ["control-failed"])

    def test_4xx_is_not_retried(self):
        t = FakeTransport([(400, b"bad")])
        self.assertFalse(ControlClient(t).send("https://c/u", say("hi"), lambda k, p: None))
        self.assertEqual(len(t.calls), 1)

    def test_request_is_json_post_and_failure_payload_has_status_and_error(self):
        t = FakeTransport([(503, b""), (503, b"")]); logged = []
        msg = say("hi")
        ControlClient(t).send("https://c/u", msg, lambda k, p: logged.append((k, p)))
        method, url, headers, body = t.calls[0]
        self.assertEqual((method, url, headers["Content-Type"]), ("POST", "https://c/u", "application/json"))
        self.assertEqual(json.loads(body), msg)
        kind, payload = logged[0]
        self.assertEqual(kind, "control-failed")
        self.assertEqual((payload["url"], payload["message"], payload["status"]), ("https://c/u", msg, 503))


class VapiApiTest(unittest.TestCase):
    def test_create_call_body_and_auth(self):
        t = FakeTransport([(201, b'{"id": "call_1"}')])
        out = VapiApi("key", "https://api.vapi.ai", t).create_call({"a": 1}, "pn_1", "+16175550101")
        method, url, headers, body = t.calls[0]
        self.assertEqual((method, url), ("POST", "https://api.vapi.ai/call"))
        self.assertEqual(headers["Authorization"], "Bearer key")
        self.assertEqual(json.loads(body), {"assistant": {"a": 1}, "phoneNumberId": "pn_1",
                                            "customer": {"number": "+16175550101"}})
        self.assertEqual(out["id"], "call_1")

    def test_get_call_uses_get_on_call_path(self):
        t = FakeTransport([(200, b'{"id": "call_1"}')])
        VapiApi("key", "https://api.vapi.ai", t).get_call("call_1")
        method, url, _, body = t.calls[0]
        self.assertEqual((method, url, body), ("GET", "https://api.vapi.ai/call/call_1", None))

    def test_error_status_raises_with_body(self):
        t = FakeTransport([(401, b'{"message":"bad key"}')])
        with self.assertRaisesRegex(ControlError, "401.*bad key"):
            VapiApi("k", "https://api.vapi.ai", t).get_call("call_1")
