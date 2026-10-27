"""The source repository and built product must start without personal data."""

import json
import unittest
from pathlib import Path

from auctionradar.web import searches

ROOT = Path(__file__).resolve().parents[1]


class DistributionPolicyTests(unittest.TestCase):
    def test_generic_configuration_has_no_sources_feedback_seeds_or_budget(self):
        self.assertEqual(
            json.loads((ROOT / "auctionradar/defaults/source-catalog.json").read_text())["sources"],
            [],
        )
        self.assertEqual(
            json.loads((ROOT / "auctionradar/defaults/search-profile.json").read_text()), {}
        )
        self.assertEqual(json.loads((ROOT / "auctionradar/defaults/seeds.json").read_text()), {})
        self.assertEqual(
            json.loads((ROOT / "auctionradar/defaults/selection-feedback.json").read_text())[
                "records"
            ],
            [],
        )
        for filename in (
            "HISTORY.md",
            "CONTINUE_PROMPT.md",
            "config/access-profile.json",
            "config/registration-status.json",
            "config/seeds-provenance.json",
            "deploy/nginx.conf",
        ):
            self.assertFalse((ROOT / filename).exists(), filename)

    def test_noncommercial_terms_cover_financial_use_of_auction_results(self):
        license_text = (ROOT / "LICENSE").read_text()
        self.assertIn("LicenseRef-AuctionRadar-Noncommercial-1.0", license_text)
        self.assertIn("investments", license_text)
        self.assertIn("intention of earning a profit", license_text)
        self.assertIn("Redistribution must be free of charge", license_text)
        self.assertTrue((ROOT / "THIRD_PARTY_NOTICES.md").is_file())

    def test_starter_preset_is_neutral_and_does_not_suggest_commercial_investment(self):
        preset = searches.presets({}, neutral=True)[0]
        self.assertEqual(preset["label_en"], "Housing")
        self.assertIn("noncommercial", preset["prompt"])
        self.assertIn("no comercial", preset["prompt_es"])
        self.assertEqual(preset["name"], "Housing")
        self.assertNotIn("130000", preset["prompt"])


if __name__ == "__main__":
    unittest.main()
