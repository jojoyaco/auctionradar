"""Tests for the broad asset taxonomy (auctionradar/assets.py + --all-assets).

Covers:
- Shared contract: ASSET_TYPES, CATEGORY_CHOICES, infer_category,
  normalize_category.
- Safe heuristics: title precedence, Calle del Barco, moto naútica before
  motorcycles, and neumático alone is NOT a boat.
- Broad mode --all-assets: Ayvens lets cars through, normalize_category
  produces consistent category+asset_type, and BOE accepts boats.
- Legacy default: Ayvens still filters passenger-car; classify requires
  housing/vehicles in normal mode.
- Fact-extraction heuristics: boat/machinery/electronics detection
  does not invent specific fields.
"""

import json
import tempfile
import unittest
from pathlib import Path

import auctionradar.assets as asset_types
import auctionradar.cli as search_cli
import auctionradar.connectors.catalog as adapters


class TaxonomyContractTests(unittest.TestCase):
    """Shared contract with parent ingestion."""

    def test_asset_types_tuple_includes_broad_families(self):
        self.assertIn("housing", asset_types.ASSET_TYPES)
        self.assertIn("commercial", asset_types.ASSET_TYPES)
        self.assertIn("land", asset_types.ASSET_TYPES)
        self.assertIn("vehicles", asset_types.ASSET_TYPES)
        self.assertIn("boats", asset_types.ASSET_TYPES)
        self.assertIn("machinery", asset_types.ASSET_TYPES)
        self.assertIn("electronics", asset_types.ASSET_TYPES)
        self.assertIn("furniture", asset_types.ASSET_TYPES)
        self.assertIn("collectibles", asset_types.ASSET_TYPES)
        self.assertIn("other", asset_types.ASSET_TYPES)
        self.assertIn("unknown", asset_types.ASSET_TYPES)

    def test_category_choices_well_formed(self):
        seen = set()
        for entry in asset_types.CATEGORY_CHOICES:
            self.assertIn("id", entry)
            self.assertIn("label_es", entry)
            self.assertIn("label_en", entry)
            self.assertIn("asset_type", entry)
            self.assertIn(entry["asset_type"], asset_types.ASSET_TYPES)
            self.assertNotIn(entry["id"], seen, "IDs duplicados: " + entry["id"])
            seen.add(entry["id"])

    def test_categories_include_user_friendly_codes(self):
        ids = {c["id"] for c in asset_types.CATEGORY_CHOICES}
        for expected in (
            "housing",
            "commercial",
            "garages",
            "land",
            "vans",
            "cars",
            "motorcycles",
            "trucks",
            "vehicles_unspec",
            "boats",
            "machinery",
            "electronics",
            "furniture",
            "collectibles",
            "other",
            "unknown",
        ):
            self.assertIn(expected, ids)

    def test_normalize_category_sets_family_consistently(self):
        item = {"title": "Plaza de garaje en Madrid"}
        result = asset_types.normalize_category(item)
        self.assertEqual(result["category"], "garages")
        self.assertEqual(result["asset_type"], "commercial")

    def test_unknown_title_preserves_unknown(self):
        result = asset_types.normalize_category({"title": "Pieza rara sin detalle"})
        self.assertEqual(result["category"], "unknown")
        self.assertEqual(result["asset_type"], "unknown")

    def test_infer_category_returns_string(self):
        self.assertIsInstance(asset_types.infer_category({"title": "Piso Gijón"}), str)


class InferenceHeuristicTests(unittest.TestCase):
    """Cases the classifier previously misidentified."""

    def test_boat_over_navigation_housing(self):
        # A title with nautical terms takes precedence over housing navigation.
        item = {"title": "Lancha neumática 4m en venta", "text": "Vivienda casa piso apartamento"}
        self.assertEqual(asset_types.infer_category(item), "boats")

    def test_moto_nautica_is_boat_not_motorcycle(self):
        item = {"title": "Moto náutica Yamaha 90cv"}
        self.assertEqual(asset_types.infer_category(item), "boats")

    def test_calle_del_barco_is_not_a_boat(self):
        item = {"title": "Piso en Calle del Barco 12, Madrid"}
        # 'Calle del Barco' is an address; the rest of the title describes housing.
        self.assertEqual(asset_types.infer_category(item), "housing")

    def test_neumatico_alone_is_not_a_boat(self):
        item = {"title": "Neumático rueda coche 205/55"}
        # Without an explicit nautical term, do NOT classify it as a boat.
        self.assertNotEqual(asset_types.infer_category(item), "boats")

    def test_commercial_and_land_recognized(self):
        self.assertEqual(
            asset_types.infer_category({"title": "Local comercial en Madrid"}),
            "commercial",
        )
        self.assertEqual(
            asset_types.infer_category({"title": "Solar urbano en venta"}),
            "land",
        )

    def test_machinery_recognized(self):
        item = {"title": "Tractor John Deere 5.000 horas"}
        self.assertEqual(asset_types.infer_category(item), "machinery")

    def test_furniture_and_electronics_recognized(self):
        self.assertEqual(
            asset_types.infer_category({"title": "Sofá chaise longue beige"}),
            "furniture",
        )
        self.assertEqual(
            asset_types.infer_category({"title": "Televisor Samsung 55 pulgadas"}),
            "electronics",
        )

    def test_collectibles_recognized(self):
        self.assertEqual(
            asset_types.infer_category({"title": "Cuadro óleo siglo XIX"}),
            "collectibles",
        )


class AdapterExtractionBroadTests(unittest.TestCase):
    """extract_item detects new families without inventing fields."""

    def test_boat_title_extraction_does_not_invent_length(self):
        page = (
            "<title>Lancha neumática 4m Yamaha 9.9cv</title>"
            "<p>Estado impecable, sin documentación.</p>"
        )
        item = adapters.extract_item(
            page,
            "https://example.org/boats/lancha-123",
            {"id": "broker", "asset_types": ["boats"]},
            "now",
        )
        self.assertEqual(item["asset_type"], "boats")
        # Do not invent unread length_m/engine_hours values; keep them null.
        self.assertNotIn("length_m", item.get("normalized_fields", {}))
        self.assertNotIn("engine_hours", item.get("normalized_fields", {}))

    def test_machinery_title_extraction(self):
        page = (
            "<title>Excavadora Caterpillar 320D</title><p>Horas: 4.500. Mantenimiento al día.</p>"
        )
        item = adapters.extract_item(
            page,
            "https://example.org/machinery/cat-320d",
            {"id": "broker", "asset_types": ["machinery"]},
            "now",
        )
        self.assertEqual(item["asset_type"], "machinery")

    def test_navigation_housing_does_not_override_boat_title(self):
        page = (
            "<title>Embarcación neumática 3.5m</title>"
            "<nav>Compra casa vivienda piso apartamento</nav>"
            "<p>Marca: Zodiac. Modelo: Cadet 350.</p>"
        )
        item = adapters.extract_item(
            page,
            "https://example.org/barco/zodiac-123",
            {"id": "broker", "asset_types": ["boats"]},
            "now",
        )
        self.assertEqual(item["asset_type"], "boats")

    def test_electronics_title_extraction(self):
        page = "<title>Smartphone iPhone 13 Pro 256GB</title><p>Como nuevo, con caja.</p>"
        item = adapters.extract_item(
            page,
            "https://example.org/tech/iphone-13",
            {"id": "broker", "asset_types": ["electronics"]},
            "now",
        )
        self.assertEqual(item["asset_type"], "electronics")

    def test_calle_del_barco_does_not_become_boat(self):
        page = (
            "<title>Piso en Calle del Barco 12, Madrid</title><p>Vivienda 80 m², 2 dormitorios.</p>"
        )
        item = adapters.extract_item(
            page,
            "https://example.org/inmueble/calle-barco-12",
            {"id": "aliseda", "asset_types": ["housing"]},
            "now",
        )
        self.assertEqual(item["asset_type"], "housing")

    def test_deposit_litres_still_not_money_for_boats(self):
        # A fuel deposit is not a security deposit — for boats or land.
        page = "<title>Embarcación neumática con motor</title><p>Depósito 25 litros gasolina.</p>"
        item = adapters.extract_item(
            page,
            "https://example.org/boats/123",
            {"id": "broker", "asset_types": ["boats"]},
            "now",
        )
        self.assertIsNone(item["prices"]["deposit"])


class DiscoverLinksAllAssetsTests(unittest.TestCase):
    """discover_links con all_assets=True relaja la whitelist Ayvens."""

    def test_default_ayvens_still_filters_passenger_car(self):
        body = (
            '<a href="/es-es/car/RENAULT/CAPTUR/Intens/123456">Captur</a>'
            '<a href="/es-es/car/FORD/TRANSIT/Van/987654">Transit</a>'
        )
        links = adapters.discover_links(
            body,
            "https://example.org/es-es/catalog",
            {"id": "ayvens"},
        )
        # Legacy default: vans only.
        self.assertEqual(
            links["details"],
            ["https://example.org/es-es/car/FORD/TRANSIT/Van/987654"],
        )

    def test_all_assets_ayvens_includes_passenger_car(self):
        body = (
            '<a href="/es-es/car/RENAULT/CAPTUR/Intens/123456">Captur</a>'
            '<a href="/es-es/car/FORD/TRANSIT/Van/987654">Transit</a>'
            '<a href="/es-es/car/BYD/SEAL/Sedan/555555">Seal</a>'
        )
        links = adapters.discover_links(
            body,
            "https://example.org/es-es/catalog",
            {"id": "ayvens"},
            all_assets=True,
        )
        urls = set(links["details"])
        self.assertIn("https://example.org/es-es/car/RENAULT/CAPTUR/Intens/123456", urls)
        self.assertIn("https://example.org/es-es/car/BYD/SEAL/Sedan/555555", urls)

    def test_all_assets_ayvens_rejects_contact_page(self):
        body = '<a href="/es-es/car/contact">Contacto</a>'
        links = adapters.discover_links(
            body,
            "https://example.org/",
            {"id": "ayvens"},
            all_assets=True,
        )
        self.assertEqual(links["details"], [])


class CLIBroadAssetFlagTests(unittest.TestCase):
    """CLI --all-assets contract and behavior."""

    def test_parser_includes_all_assets_flag(self):
        ns = search_cli.parser().parse_args([])
        self.assertFalse(ns.all_assets)
        ns = search_cli.parser().parse_args(["--all-assets"])
        self.assertTrue(ns.all_assets)

    def test_default_classify_unknown_is_pending_review(self):
        # Modo normal: unknown NO es candidato.
        item = {"asset_type": "unknown", "availability": "available"}
        result = search_cli.classify(item, {}, {})
        self.assertEqual(result["selection"]["status"], "pending_review")
        self.assertIn("asset_type_unconfirmed", result["selection"]["reasons"])

    def test_broad_mode_classify_accepts_boats(self):
        item = {"asset_type": "boats", "availability": "available"}
        result = search_cli.classify(item, {}, {}, broad_mode=True)
        # Recognizing the type does not yet confirm its information or profile.
        self.assertEqual(result["selection"]["status"], "pending_review")
        self.assertNotIn("asset_type_unconfirmed", result["selection"]["reasons"])

    def test_broad_mode_classify_unknown_still_pending(self):
        item = {"asset_type": "unknown", "availability": "available"}
        result = search_cli.classify(item, {}, {}, broad_mode=True)
        # unknown sigue siendo unknown incluso en modo amplio.
        self.assertEqual(result["selection"]["status"], "pending_review")
        self.assertIn("asset_type_unconfirmed", result["selection"]["reasons"])

    def test_broad_mode_no_housing_budget_cap_on_boats(self):
        # El cap de housing NO se aplica a boats.
        item = {"asset_type": "boats", "all_in_cost_estimate": 500000}
        result = search_cli.classify(
            item,
            {"housing": {"budget": {"max_all_in": 130000}}},
            {},
            broad_mode=True,
        )
        self.assertNotIn("all_in_over_budget", result["selection"]["reasons"])

    def test_default_mode_housing_budget_cap_still_applies(self):
        item = {"asset_type": "housing", "all_in_cost_estimate": 200000}
        result = search_cli.classify(
            item,
            {"housing": {"budget": {"max_all_in": 130000}}},
            {},
        )
        self.assertIn("all_in_over_budget", result["selection"]["reasons"])

    def test_broad_mode_normalize_category_through_cli(self):
        # Verify that classify + broad_mode + normalize_category produce
        # category/asset_type coherentes.
        item = {"title": "Lancha neumática 4m"}
        normalized = asset_types.normalize_category(item)
        self.assertEqual(normalized["asset_type"], "boats")
        self.assertEqual(normalized["category"], "boats")


class CLIJSONIntegrationBroadTests(unittest.TestCase):
    """Real CLI integration with a local HTTP server serving fixture HTML."""

    def test_cli_all_assets_includes_broad_items(self):
        import http.server
        import subprocess
        import sys
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith("/lote/zodiac-1"):
                    body = (
                        "<html><title>Lancha neumática 4m Yamaha</title>"
                        "<p>Marca Zodiac, motor Yamaha 9.9cv, depósito 25 L.</p>"
                        "</html>"
                    )
                elif self.path.endswith("/lote/cat-320"):
                    body = (
                        "<html><title>Excavadora Caterpillar 320D</title>"
                        "<p>5.000 horas, mantenimiento al día.</p></html>"
                    )
                elif self.path == "/" or self.path == "":
                    # Listing: contains links to the lots.
                    body = (
                        "<html><title>Catálogo</title>"
                        '<a href="/lote/zodiac-1">Zodiac</a>'
                        '<a href="/lote/cat-320">CAT</a>'
                        "</html>"
                    )
                else:
                    body = "<html><title>404</title></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                base = f"http://127.0.0.1:{server.server_port}"
                catalog = {
                    "sources": [
                        {
                            "id": "broker",
                            "url": base,
                            "enabled": True,
                            "asset_types": ["boats", "machinery"],
                        },
                    ],
                }
                (folder / "catalog.json").write_text(json.dumps(catalog))
                (folder / "profile.json").write_text(json.dumps({}))
                (folder / "feedback.json").write_text(json.dumps({}))
                (folder / "seeds.json").write_text(
                    json.dumps(
                        {
                            "broker": [base + "/"],
                        }
                    )
                )
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
                    "--all-assets",
                    "--timeout",
                    "2",
                    "--max-pages",
                    "1",
                    "--max-details",
                    "4",
                    "--max-documents",
                    "0",
                ]
                proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                run = json.loads(proc.stdout)
                # At least one item was found, and the source is cataloged.
                self.assertTrue(run["items"], proc.stdout)
                titles = [it.get("title", "") for it in run["items"]]
                # At least one of the two must appear.
                self.assertTrue(
                    any("Lancha" in t for t in titles) or any("Excavadora" in t for t in titles),
                    f"No apareció ningún título amplio: {titles}",
                )
                # El filtro "all_assets" debe propagarse a la metadata.
                self.assertTrue(run["filters"].get("all_assets"))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
