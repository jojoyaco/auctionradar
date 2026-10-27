import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.find_spec("auctionradar.cli")
core = importlib.import_module("auctionradar.cli") if spec else None


class CoreTests(unittest.TestCase):
    def test_core_exists(self):
        self.assertIsNotNone(core, "CLI core must exist")

    def test_feedback_url_survives_id_and_tracking_changes(self):
        if core is None:
            self.fail("core missing")
        item = {
            "id": "renamed",
            "source_id": "ayvens",
            "url": "https://example.com/car/168616?utm_source=x",
        }
        feedback = {
            "records": [
                {
                    "candidate_id": "old",
                    "source_id": "ayvens",
                    "listing_url": "https://example.com/old-slug/168616?financetype=cash",
                    "reason": "sold",
                    "apply_to_future_searches": True,
                }
            ]
        }
        self.assertEqual(core.classify(item, {}, feedback)["selection"]["status"], "excluded")

    def test_unknown_possession_and_auction_value_not_budget_exclusion(self):
        if core is None:
            self.fail("core missing")
        item = {"asset_type": "housing", "prices": [{"type": "auction_value", "amount": 193925}]}
        got = core.classify(item, {"housing": {"budget": {"max_all_in": 130000}}}, {})
        self.assertEqual(got["selection"]["status"], "pending_review")
        self.assertIsNone(got["all_in_cost_estimate"])

    def test_vehicle_does_not_use_housing_cap(self):
        if core is None:
            self.fail("core missing")
        got = core.classify(
            {"asset_type": "vehicles", "all_in_cost_estimate": 200000},
            {"housing": {"budget": {"max_all_in": 130000}}},
            {},
        )
        self.assertNotIn("all_in_over_budget", got["selection"]["reasons"])

    def test_occupied_excluded(self):
        if core is None:
            self.fail("core missing")
        self.assertEqual(
            core.classify({"asset_type": "housing", "possession": "occupied"}, {}, {})["selection"][
                "status"
            ],
            "excluded",
        )

    def test_dedup_boe_across_origins(self):
        if core is None:
            self.fail("core missing")
        got = core.deduplicate(
            [
                {
                    "source_id": "boe",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-265760",
                },
                {
                    "source_id": "aeat",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-265760&ver=1",
                },
            ]
        )
        self.assertEqual(len(got), 1)
        self.assertEqual(set(got[0]["source_ids"]), {"boe", "aeat"})

    def test_conflicting_duplicate_occupied_is_excluded(self):
        if core is None:
            self.fail("core missing")
        got = core.classify(
            {
                "asset_type": "housing",
                "possession": "vacant",
                "availability": "available",
                "all_in_cost_estimate": 100000,
                "duplicate_observations": [{"asset_type": "housing", "possession": "occupied"}],
            },
            {},
            {},
        )
        self.assertEqual(got["selection"]["status"], "excluded")

    def test_history_ignores_nested_observation_timestamps(self):
        if core is None:
            self.fail("core missing")
        with tempfile.TemporaryDirectory() as d:
            core.save_history(
                {
                    "run_id": "a",
                    "items": [
                        {"id": "x", "photos": [{"url": "https://x/photo", "observed_at": "a"}]}
                    ],
                },
                Path(d),
            )
            run = {
                "run_id": "b",
                "items": [{"id": "x", "photos": [{"url": "https://x/photo", "observed_at": "b"}]}],
            }
            core.save_history(run, Path(d))
            self.assertEqual(run["changes"]["changed_ids"], [])

    def test_adapter_exclusion_statuses(self):
        if core is None:
            self.fail("core missing")
        for item in (
            {"asset_type": "housing", "possession": {"status": "occupied_or_tenanted"}},
            {"asset_type": "vehicles", "availability": {"status": "unavailable"}},
        ):
            self.assertEqual(core.classify(item, {}, {})["selection"]["status"], "excluded")

    def test_photo_contract_normalized_without_inference(self):
        if core is None:
            self.fail("core missing")
        got = core.classify(
            {
                "asset_type": "housing",
                "url": "https://example.org/detail",
                "photos": [{"url": "https://example.org/a.jpg", "kind": "floorplan"}],
            },
            {},
            {},
        )
        self.assertEqual(got["photos"][0]["image_type"], "plan")
        self.assertEqual(got["photos"][0]["source_url"], "https://example.org/detail")
        self.assertIn("caption", got["photos"][0])

    def test_reference_collision_across_unrelated_sources_not_deduplicated(self):
        got = core.deduplicate(
            [
                {
                    "id": "one-house",
                    "source_id": "one",
                    "reference": "12345",
                    "url": "https://one.org/house/1",
                },
                {
                    "id": "two-house",
                    "source_id": "two",
                    "reference": "12345",
                    "url": "https://two.org/house/2",
                },
            ]
        )
        self.assertEqual(len(got), 2)

    def test_cadastral_reference_remains_global(self):
        got = core.deduplicate(
            [
                {
                    "id": "one-house",
                    "source_id": "one",
                    "cadastral_reference": "B00301100TN69G0001XA",
                    "url": "https://one.org/house/1",
                },
                {
                    "id": "two-house",
                    "source_id": "two",
                    "cadastral_reference": "B00301100TN69G0001XA",
                    "url": "https://two.org/house/2",
                },
            ]
        )
        self.assertEqual(len(got), 1)

    def test_alphanumeric_aeat_ids_do_not_collapse_prefix(self):
        got = core.deduplicate(
            [
                {
                    "id": "a",
                    "source_id": "aeat",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R12345",
                },
                {
                    "id": "b",
                    "source_id": "aeat",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R99999",
                },
            ]
        )
        self.assertEqual(len(got), 2)

    def test_alphanumeric_aeat_ids_match_same_boe_across_sources(self):
        got = core.deduplicate(
            [
                {
                    "id": "a",
                    "source_id": "aeat",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R12345",
                },
                {
                    "id": "b",
                    "source_id": "boe",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R12345&ver=3",
                },
            ]
        )
        self.assertEqual(len(got), 1)

    def test_boe_lots_of_same_auction_remain_distinct(self):
        got = core.deduplicate(
            [
                {
                    "id": "old-shared",
                    "source_id": "boe",
                    "reference": "SUB-JA-2026-265760",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-265760&idLote=1",
                },
                {
                    "id": "old-shared",
                    "source_id": "boe",
                    "reference": "SUB-JA-2026-265760",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-265760&idLote=2",
                },
            ]
        )
        self.assertEqual(len(got), 2)

    def test_boe_same_lot_across_origins_deduplicates(self):
        got = core.deduplicate(
            [
                {
                    "id": "a",
                    "source_id": "boe",
                    "reference": "SUB-JA-2026-265760",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-265760&idLote=1",
                },
                {
                    "id": "b",
                    "source_id": "aeat",
                    "reference": "SUB-JA-2026-265760",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-265760&idLote=1&ver=3",
                },
            ]
        )
        self.assertEqual(len(got), 1)

    def test_dedup_source_priority_stable_despite_completion_order(self):
        boe = {
            "id": "boe-primary",
            "source_id": "boe",
            "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R0686001041",
            "reference": "SUB-AT-2026-26R0686001041",
        }
        aeat = {
            "id": "aeat-secondary",
            "source_id": "aeat",
            "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R0686001041&ver=3",
            "reference": "SUB-AT-2026-26R0686001041",
        }
        first = core.deduplicate([aeat, boe], source_order={"boe": 0, "aeat": 1})
        second = core.deduplicate([boe, aeat], source_order={"boe": 0, "aeat": 1})
        self.assertEqual(first, second)
        self.assertEqual(first[0]["id"], "boe-primary")
        self.assertEqual(first[0]["source_ids"], ["boe", "aeat"])

    def test_history_absence_never_means_sold(self):
        if core is None:
            self.fail("core missing")
        with tempfile.TemporaryDirectory() as d:
            a = {"run_id": "a", "items": [{"id": "x", "url": "https://x.test/1"}]}
            core.save_history(a, Path(d))
            b = {"run_id": "b", "items": []}
            core.save_history(b, Path(d))
            self.assertEqual(b["changes"]["not_seen_ids"], ["x"])
            self.assertEqual(b["changes"]["sold_ids"], [])


class NormalizationTests(unittest.TestCase):
    def test_scoped_entity_fields_and_zero_elevator(self):
        self.assertTrue(hasattr(core, "normalize_fields"), "normalizer must exist")
        item = {
            "asset_type": "housing",
            "facts_scope": "current_listing_entity",
            "facts": [
                {"field": "property.SuperficieConstruida", "value": "54"},
                {"field": "property.SuperficieUtil", "value": 0},
                {"field": "property.NumeroHabitaciones", "value": 2},
                {"field": "property.NumeroBanos", "value": 1},
                {"field": "property.Ascensor", "value": 0},
                {"field": "property.AnoConstruccion", "value": 1950},
            ],
            "description": "Superficie útil: 48,5 m²",
        }
        got = core.normalize_fields(item)
        self.assertEqual(got["built_area_m2"], 54)
        self.assertEqual(got["usable_area_m2"], 48.5)
        self.assertEqual(got["bedrooms"], 2)
        self.assertEqual(got["bathrooms"], 1)
        self.assertEqual(got["construction_year"], 1950)
        self.assertIsNone(got["elevator"])

    def test_global_facts_and_recommendations_do_not_become_current_fields(self):
        self.assertTrue(hasattr(core, "normalize_fields"), "normalizer must exist")
        got = core.normalize_fields(
            {
                "facts": [
                    {"field": "recommendations[0].bedrooms", "value": 6},
                    {"field": "Organization.address.addressLocality", "value": "Madrid"},
                ],
                "published_text": "Superficie construida: 900 m²",
            }
        )
        self.assertIsNone(got["built_area_m2"])
        self.assertIsNone(got["bedrooms"])
        self.assertIsNone(got["location"])

    def test_explicit_normalized_fields_preferred_and_price_types_retained(self):
        self.assertTrue(hasattr(core, "normalize_fields"), "normalizer must exist")
        got = core.normalize_fields(
            {
                "normalized_fields": {"bedrooms": 2, "location": {"municipality": "Laviana"}},
                "prices": {"asking": None, "reserve": 6210, "auction_value": 24841},
            }
        )
        self.assertEqual(got["bedrooms"], 2)
        self.assertIsNone(got["exact_price"])
        self.assertIsNone(got["price_type"])
        self.assertEqual(got["prices"]["reserve"], 6210)

    def test_unknown_asset_never_candidate(self):
        got = core.classify({"asset_type": "unknown", "availability": "available"}, {}, {})
        self.assertEqual(got["selection"]["status"], "pending_review")
        self.assertIn("asset_type_unconfirmed", got["selection"]["reasons"])


class CLIIntegrationTests(unittest.TestCase):
    def test_region_filter_uses_normalized_location(self):
        import contextlib
        import io
        import json
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for key, value in {
                "catalog": {
                    "sources": [
                        {"id": "test", "url": "https://example.org", "asset_types": ["housing"]}
                    ]
                },
                "profile": {},
                "feedback": {},
            }.items():
                (folder / (key + ".json")).write_text(json.dumps(value))
            item = {
                "id": "asturias-house",
                "asset_type": "housing",
                "url": "https://example.org/house",
                "normalized_fields": {"location": {"region": "Asturias"}},
            }
            result = {
                "status": "checked",
                "items": [item],
                "requests": [],
                "limitations": [],
                "depth": "detail",
            }
            args = [
                "--catalog",
                str(folder / "catalog.json"),
                "--profile",
                str(folder / "profile.json"),
                "--feedback",
                str(folder / "feedback.json"),
                "--seeds",
                str(folder / "absent.json"),
                "--history-dir",
                str(folder / "history"),
                "--region",
                "Asturias",
                "--max-documents",
                "0",
            ]
            stdout = io.StringIO()
            with (
                patch("auctionradar.connectors.catalog.discover", return_value=result),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                core.main(args)
            self.assertEqual(len(json.loads(stdout.getvalue())["items"]), 1)

    def test_real_http_json_stdout_partial_errors_and_repeat_history(self):
        import http.server
        import json
        import subprocess
        import sys
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/fail"):
                    self.send_error(503, "temporary failure")
                    return
                body = b'<html><title>Casa Asturias</title><script type="application/ld+json">{"@type":"Product","name":"Casa Asturias","sku":"ANT123","offers":{"price":"28300","priceCurrency":"EUR"}}</script><a href="/inmueble/ant123">Casa</a></html>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                base = f"http://127.0.0.1:{server.server_port}"
                values = {
                    "catalog": {
                        "sources": [
                            {
                                "id": "aliseda",
                                "url": base,
                                "enabled": True,
                                "asset_types": ["housing"],
                            },
                            {
                                "id": "broken",
                                "url": base + "/fail",
                                "enabled": True,
                                "asset_types": ["housing"],
                            },
                        ]
                    },
                    "profile": {},
                    "feedback": {},
                    "seeds": {"aliseda": [base + "/inmueble/ant123"]},
                }
                for key, value in values.items():
                    (folder / (key + ".json")).write_text(json.dumps(value))
                args = [
                    sys.executable,
                    "-m",
                    "auctionradar.cli",
                    "--catalog",
                    str(folder / "catalog.json"),
                    "--profile",
                    str(folder / "profile.json"),
                    "--feedback",
                    str(folder / "feedback.json"),
                    "--seeds",
                    str(folder / "seeds.json"),
                    "--history-dir",
                    str(folder / "history"),
                    "--timeout",
                    "2",
                    "--max-pages",
                    "1",
                    "--max-details",
                    "1",
                    "--max-documents",
                    "0",
                ]
                first = subprocess.run(args, capture_output=True, text=True, timeout=30)
                self.assertEqual(first.returncode, 0, first.stderr)
                run = json.loads(first.stdout)
                self.assertEqual(len(run["sources"]), 2)
                self.assertTrue(run["items"], "Debe extraer ficha real servida por HTTP")
                self.assertEqual(run["items"][0]["source_id"], "aliseda")
                self.assertTrue(first.stderr)
                broken = next(v for v in run["sources"] if v["source_id"] == "broken")
                self.assertNotEqual(broken["status"], "no_matches")
                self.assertTrue(broken["requests"] or broken["limitations"])
                second = subprocess.run(args, capture_output=True, text=True, timeout=30)
                self.assertEqual(second.returncode, 0, second.stderr)
                repeated = json.loads(second.stdout)
                self.assertEqual(repeated["changes"]["previous_run_id"], run["run_id"])
                self.assertNotEqual(repeated["run_id"], run["run_id"])
                self.assertFalse(repeated["changes"]["absence_means_sold"])
                self.assertEqual(repeated["changes"]["changed_ids"], [])
                self.assertEqual(repeated["changes"]["new_ids"], [])
                self.assertEqual(len(list((folder / "history").glob("*.json"))), 3)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
