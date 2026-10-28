import unittest

from probe.assistant import (
    ASK_USER_TOOL_NAME,
    SECRET_HEADER,
    SERVER_MESSAGES,
    build_assistant,
)
from probe.config import load_settings
from probe.scenario import load_scenario
from tests.helpers import FULL_ENV, SCENARIO_DIR

SETTINGS = load_settings(FULL_ENV)


class BuildAssistantTest(unittest.TestCase):
    def setUp(self):
        self.scenario = load_scenario(SCENARIO_DIR / "b_doctor_reschedule.json")
        self.a = build_assistant(self.scenario, SETTINGS)

    def test_disclosure_is_first_message_and_matches_dc1(self):
        self.assertEqual(self.a["firstMessageMode"], "assistant-speaks-first")
        self.assertEqual(
            self.a["firstMessage"],
            "Hi, I'm an AI assistant calling on behalf of Alex Chen. This call is transcribed. "
            + self.scenario.opening_request,
        )

    def test_audio_recording_off_transcript_on(self):
        self.assertIs(self.a["artifactPlan"]["recordingEnabled"], False)
        self.assertIs(self.a["artifactPlan"]["transcriptPlan"]["enabled"], True)

    def test_webhook_server_and_secret_header(self):
        self.assertEqual(self.a["server"]["url"], SETTINGS.public_url + "/webhook")
        self.assertEqual(self.a["server"]["headers"], {SECRET_HEADER: SETTINGS.webhook_secret})
        self.assertEqual(tuple(self.a["serverMessages"]), SERVER_MESSAGES)

    def test_monitor_listen_and_control_enabled(self):
        self.assertEqual(self.a["monitorPlan"], {"listenEnabled": True, "controlEnabled": True})

    def test_ask_user_tool_is_async_with_schema(self):
        (tool,) = [
            t for t in self.a["model"]["tools"]
            if t.get("function", {}).get("name") == ASK_USER_TOOL_NAME
        ]
        self.assertEqual(tool["type"], "function")
        self.assertIs(tool["async"], True)
        self.assertNotIn("messages", tool)
        self.assertEqual(tool["function"]["name"], ASK_USER_TOOL_NAME)
        params = tool["function"]["parameters"]
        self.assertEqual(set(params["required"]), {"question", "reason", "suggested_replies"})
        self.assertEqual(params["properties"]["suggested_replies"]["minItems"], 2)
        self.assertEqual(params["properties"]["suggested_replies"]["maxItems"], 4)
        self.assertEqual(tool["server"]["url"], self.a["server"]["url"])
        self.assertEqual(tool["server"]["headers"], {SECRET_HEADER: SETTINGS.webhook_secret})

    def test_end_call_tool_present(self):
        self.assertIn({"type": "endCall"}, self.a["model"]["tools"])

    def test_dtmf_tool_present_and_prompted(self):
        self.assertIn({"type": "dtmf"}, self.a["model"]["tools"])
        self.assertIn("If you reach an automated phone menu, use the dtmf tool to press the "
                      "option that reaches a person.", self.a["model"]["messages"][0]["content"])

    def test_never_agree_section_routes_through_ask_user(self):
        content = self.a["model"]["messages"][0]["content"]
        section = content.split("NEVER AGREE TO")[1].split("WHEN TO CALL")[0]
        self.assertIn("pay a fee", section)
        self.assertIn("ask_user", section)
        self.assertIn("endCall", content)

    def test_prompt_says_stay_quiet_after_ask_user(self):
        content = self.a["model"]["messages"][0]["content"]
        self.assertIn("After you call ask_user, say nothing further", content)
        self.assertIn("Wait until you are told that Alex Chen replied or did not reply.", content)
        self.assertIn("say only that you are still checking", content)

    def test_fixed_voice_transcriber_and_limits(self):
        self.assertEqual(
            self.a["transcriber"], {"provider": "deepgram", "model": "nova-3", "language": "en-US"}
        )
        self.assertEqual(self.a["voice"], {"provider": "vapi", "voiceId": "Clara"})
        self.assertEqual(self.a["maxDurationSeconds"], 600)

    def test_model_and_prompt(self):
        self.assertEqual(self.a["model"]["provider"], "openai")
        self.assertEqual(self.a["model"]["model"], "gpt-5.4-mini")
        prompt = self.a["model"]["messages"][0]
        self.assertEqual(prompt["role"], "system")
        for must in ("before 3pm", "Friday 10am", "date_of_birth", "ask_user",
                     "never claim to be human", "pay a fee"):
            self.assertIn(must, prompt["content"])
        self.assertNotIn("1999-04-12", prompt["content"].split("ASK FIRST")[0])
        self.assertIn("1999-04-12", prompt["content"].split("ASK FIRST")[1])


if __name__ == "__main__":
    unittest.main()
