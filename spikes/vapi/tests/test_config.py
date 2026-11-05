import unittest

from probe.config import ConfigError, load_settings, parse_dotenv, validate_us_number
from tests.helpers import FULL_ENV


class ParseDotenvTest(unittest.TestCase):
    def test_parses_pairs_and_ignores_comments_blanks_and_quotes(self):
        text = '# c\nA=1\n\nB="two words"\nexport C=3\n'
        self.assertEqual(parse_dotenv(text), {"A": "1", "B": "two words", "C": "3"})


class LoadSettingsTest(unittest.TestCase):
    def test_missing_variables_are_all_named(self):
        with self.assertRaises(ConfigError) as cm:
            load_settings({"VAPI_API_KEY": "k"})
        for name in ("VAPI_PHONE_NUMBER_ID", "VAPI_WEBHOOK_SECRET", "PROBE_PUBLIC_URL",
                     "PROBE_RECEPTIONIST_NUMBERS", "PROBE_OPERATOR_NUMBER"):
            self.assertIn(name, str(cm.exception))

    def test_defaults_and_number_list(self):
        s = load_settings(FULL_ENV)
        self.assertEqual(s.model, "gpt-5.4-mini")
        self.assertEqual(s.spend_cap_usd, 20.0)
        self.assertEqual(s.receptionist_numbers, ("+16175550101", "+16175550102"))
        self.assertIn("+16175550199", s.allowed_numbers)

    def test_public_url_trailing_slash_is_stripped(self):
        s = load_settings({**FULL_ENV, "PROBE_PUBLIC_URL": "https://x.trycloudflare.com/"})
        self.assertEqual(s.public_url, "https://x.trycloudflare.com")

    def test_optional_overrides(self):
        s = load_settings({**FULL_ENV, "VAPI_MODEL": "m", "PROBE_SPEND_CAP_USD": "5"})
        self.assertEqual((s.model, s.spend_cap_usd), ("m", 5.0))

    def test_bad_receptionist_number_is_rejected(self):
        with self.assertRaises(ConfigError):
            load_settings({**FULL_ENV, "PROBE_RECEPTIONIST_NUMBERS": "+16175550101, 911"})


class ValidateUsNumberTest(unittest.TestCase):
    def test_accepts_us_e164(self):
        self.assertEqual(validate_us_number("+16175550101"), "+16175550101")

    def test_rejects_bad_formats(self):
        for bad in ("6175550101", "+1617555010", "+446175550101", "+1 617 555 0101", ""):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                validate_us_number(bad)

    def test_rejects_emergency_numbers(self):
        for bad in ("911", "+1911", "112", "+19115550101"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError) as cm:
                validate_us_number(bad)
            self.assertIn("emergency", str(cm.exception).lower())


if __name__ == "__main__":
    unittest.main()
