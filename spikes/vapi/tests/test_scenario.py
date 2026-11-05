import json
import tempfile
import unittest
from pathlib import Path

from probe.config import ConfigError
from probe.scenario import load_scenario
from tests.helpers import SCENARIO_DIR


class LoadScenarioTest(unittest.TestCase):
    def test_all_five_scenarios_load(self):
        ids = sorted(load_scenario(p).id for p in SCENARIO_DIR.glob("*.json"))
        self.assertEqual(ids, ["a", "b", "c", "d", "e"])

    def test_defaults_match_prd_ask_first_fields(self):
        b = load_scenario(SCENARIO_DIR / "b_doctor_reschedule.json")
        self.assertEqual(set(b.ask_first_fields), {"date_of_birth", "address", "insurance"})
        self.assertIn("before 3pm", " ".join(b.constraints))
        self.assertIn("Friday 10am", " ".join(b.fallbacks))

    def test_every_probe_1_to_10_is_covered(self):
        covered = {n for p in SCENARIO_DIR.glob("*.json") for n in load_scenario(p).probes}
        self.assertTrue(set(range(1, 11)) <= covered)

    def test_bad_share_value_is_rejected(self):
        bad = json.loads((SCENARIO_DIR / "a_restaurant.json").read_text())
        bad["profile"][0]["share"] = "maybe"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.json"
            path.write_text(json.dumps(bad))
            with self.assertRaises(ConfigError):
                load_scenario(path)

    def test_missing_key_is_rejected(self):
        bad = json.loads((SCENARIO_DIR / "a_restaurant.json").read_text())
        del bad["goal"]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.json"
            path.write_text(json.dumps(bad))
            with self.assertRaises(ConfigError):
                load_scenario(path)

    def test_opening_request_is_a_spoken_sentence(self):
        for path in SCENARIO_DIR.glob("*.json"):
            s = load_scenario(path)
            self.assertTrue(s.opening_request[0].isupper(), s.id)
            self.assertIn(s.opening_request[-1], ".?", s.id)
            self.assertNotEqual(s.opening_request, s.goal, s.id)


if __name__ == "__main__":
    unittest.main()
