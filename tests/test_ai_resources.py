"""Selected memory limits must reach the provider and invalidate the cache."""

import unittest

from auctionradar.web import ai_filter


class AIResourceTests(unittest.TestCase):
    def settings(self, **extra):
        return ai_filter.get_settings(
            {
                "AUCTIONRADAR_AI_PROVIDER": "ollama",
                "AUCTIONRADAR_AI_BASE_URL": "http://127.0.0.1:11434",
                "AUCTIONRADAR_AI_MODEL": "small-local",
                "AUCTIONRADAR_AI_CONTEXT": "8192",
                "AUCTIONRADAR_AI_MAX_INPUT_CHARS": "14000",
                "AUCTIONRADAR_AI_THINKING": "false",
                **extra,
            }
        )

    def item(self):
        return {
            "id": "synthetic",
            "asset_type": "boats",
            "title": "Lancha de prueba",
            "facts_scope": "current_listing_entity",
            "prices": {"asking": 12000},
            "facts": [{"field": "prices.asking", "value": 12000}],
        }

    def test_small_model_controls_are_explicit(self):
        settings = self.settings()
        self.assertEqual(settings.num_ctx, 8192)
        self.assertEqual(settings.max_input_chars, 14000)
        self.assertFalse(settings.thinking)
        _, payload, _, _ = ai_filter.build_provider_request(
            "Keep boats under 15000 EUR", self.item(), {}, settings
        )
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(payload["options"]["num_predict"], 2048)
        self.assertEqual(payload["keep_alive"], "2m")

    def test_auto_thinking_does_not_force_unsupported_false(self):
        settings = self.settings(AUCTIONRADAR_AI_THINKING="auto")
        _, payload, _, _ = ai_filter.build_provider_request("Keep boats", self.item(), {}, settings)
        self.assertNotIn("think", payload)

    def test_config_is_bounded_and_reducing_input_invalidates_cache(self):
        self.assertEqual(self.settings(AUCTIONRADAR_AI_CONTEXT="999999").num_ctx, 16384)
        self.assertEqual(self.settings(AUCTIONRADAR_AI_CONTEXT="-10").num_ctx, 4096)
        self.assertEqual(
            self.settings(AUCTIONRADAR_AI_MAX_INPUT_CHARS="broken").max_input_chars, 24000
        )
        fingerprint = ai_filter.evaluation_fingerprint("Keep", self.item(), {}, self.settings())
        changed = ai_filter.evaluation_fingerprint(
            "Keep", self.item(), {}, self.settings(AUCTIONRADAR_AI_MAX_INPUT_CHARS="4000")
        )
        self.assertNotEqual(fingerprint, changed)

    def test_long_descriptions_are_scoped_excerpts_and_json_remains_valid(self):
        import json

        item = {**self.item(), "description": "Published current listing facts. " * 500}
        prepared = ai_filter.prepare_input("Keep boats", item, {}, self.settings())
        self.assertTrue(prepared.truncated)
        payload = json.loads(prepared.facts_payload_str)
        self.assertLessEqual(len(payload.get("item", {}).get("description", "")), 3000)
        self.assertEqual(payload["item"]["prices"]["asking"], 12000)

    def test_numeric_serialization_does_not_reject_identical_published_values(self):
        self.assertTrue(ai_filter._values_equivalent("12000", 12000))
        self.assertFalse(ai_filter._values_equivalent("12000", 12001))
        self.assertFalse(ai_filter._values_equivalent("12.000 €", 12000))
        self.assertFalse(ai_filter._values_equivalent(False, 0))

    def test_missing_required_fact_is_review_not_an_automatic_rejection(self):
        import json

        item = {**self.item(), "length_m": None}
        criteria = {"required_fields": ["prices.asking", "length_m"]}
        _, _, _, prepared = ai_filter.build_provider_request(
            "Keep boats below 15000 and below 6 metres", item, criteria, self.settings()
        )
        length = next(
            entry
            for entry in prepared.facts_payload["normalization_evidence"]
            if entry["field"] == "length_m"
        )
        output = {
            "status": "evaluated",
            "verdict": "reject",
            "score": 10,
            "reason": "The length is not published.",
            "missing_fields": [],
            "limitations": [],
            "factual_claims": [
                {
                    "claim": "Published price",
                    "field": "prices.asking",
                    "value": 12000,
                    "evidence_id": "fact_1",
                },
                {
                    "claim": "Missing length",
                    "field": "length_m",
                    "value": None,
                    "evidence_id": length["id"],
                },
            ],
        }
        body = json.dumps({"message": {"content": json.dumps(output)}}).encode()

        class Response:
            def read(self, n):
                return body[:n]

            def close(self):
                pass

        result = ai_filter.evaluate_item(
            "Keep boats below 15000 and below 6 metres",
            item,
            criteria,
            self.settings(),
            opener=lambda *args, **kwargs: Response(),
        )
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["verdict"], "needs_review")
        self.assertIsNone(result["score"])
        self.assertIn("length_m", result["missing_fields"])


if __name__ == "__main__":
    unittest.main()
