import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import auctionradar.connectors.catalog as adapters
from auctionradar.connectors.escogecasa.detail import enrich_escogecasa

URL = "https://www.escogecasa.es/inmueble/vivienda-aislada-en-venta-illa-de-arousa-pontevedra/9311334/51029"
API = "https://www.escogecasa.es/nodejs/getFicha"
SOURCE = {"id": "escogecasa", "url": "https://www.escogecasa.es/", "asset_types": ["housing"]}


class EscogecasaPriceTests(unittest.TestCase):
    def enrich(self, changes=None, management=1):
        props = {
            "idInmueble": "51029",
            "idPromocion": None,
            "esFichaPromo": False,
            "lang": 1,
            "idGestion": management,
        }
        flight = "8:" + json.dumps(["$", "$L12", None, props]) + "\n"
        html = (
            "<title>Ficha sintética</title><script>self.__next_f.push("
            + json.dumps([1, flight])
            + ")</script>"
        )
        payload = {
            "idInmueble": 51029,
            "referencia": "9311334",
            "esFichaPromo": False,
            "idGestion": 1,
            "precio": 450000,
            "precioVisible01": 1,
            "fotos": [],
        }
        payload.update(changes or {})

        def fetch(url, context):
            self.assertEqual(url, API)
            return {
                "http_status": 200,
                "final_url": API,
                "body": json.dumps(payload),
                "error": None,
            }

        item = adapters.extract_item(html, URL, SOURCE, "2026-10-18T00:00:00+00:00")
        return enrich_escogecasa(
            item, {"escogecasa_html": html, "observed_at": "2026-10-18T00:00:00+00:00"}, fetch
        )[0]

    def test_published_sale_price_is_extracted_from_the_identified_property(self):
        item = self.enrich(
            {"precioAnterior": 600000, "precioGestion2": 700, "similares": [{"precio": 1000}]}
        )
        self.assertEqual(item["prices"]["asking"], 450000)
        self.assertEqual(item["currency"], "EUR")
        evidence = [entry for entry in item["evidence"] if entry.get("field") == "prices.asking"]
        self.assertEqual(evidence[-1]["value"], 450000)
        self.assertEqual(evidence[-1]["source_url"], URL)
        self.assertEqual(evidence[-1]["property_id"], "51029")
        self.assertEqual(
            item["facts_scope"],
            "unscoped_public_page",
            "A price must not validate unrelated HTML facts",
        )

    def test_hidden_or_unknown_visibility_cannot_supply_a_price(self):
        for visibility in (0, None, False, "1", 2):
            with self.subTest(visibility=visibility):
                item = self.enrich({"precioVisible01": visibility})
                self.assertIsNone(item["prices"]["asking"])
                self.assertNotIn("450000", json.dumps(item))

    def test_rent_or_inconsistent_management_is_not_a_sale_price(self):
        for requested, returned in ((2, 2), (1, 2), (2, 1)):
            item = self.enrich(
                {"idGestion": returned, "precio": 700, "precioGestion1": 450000},
                management=requested,
            )
            self.assertIsNone(item["prices"]["asking"])

    def test_wrong_identity_or_promotion_cannot_supply_a_price(self):
        for fields in ({"idInmueble": 999}, {"referencia": "OTHER"}, {"esFichaPromo": True}):
            self.assertIsNone(self.enrich(fields)["prices"]["asking"])

    def test_invalid_prices_fail_closed(self):
        for value in (
            0,
            -1,
            True,
            False,
            None,
            [],
            {},
            10**400,
            float("nan"),
            float("inf"),
            "antes 450000 ahora 400000",
            "450000 €/mes",
        ):
            with self.subTest(value=value):
                self.assertIsNone(self.enrich({"precio": value})["prices"]["asking"])

    def test_numeric_and_spanish_formatted_prices(self):
        for value in (450000, 450000.0, "450.000 €", "450000", "450000.00"):
            with self.subTest(value=value):
                self.assertEqual(self.enrich({"precio": value})["prices"]["asking"], 450000)

    def test_price_does_not_depend_on_a_gallery(self):
        self.assertEqual(self.enrich({"fotos": None})["prices"]["asking"], 450000)

    def test_raw_evidence_omits_hidden_prices_contacts_and_related_properties(self):
        from auctionradar.connectors.escogecasa.detail import _public_response_keys

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                value = {
                    "idInmueble": 51029,
                    "referencia": "9311334",
                    "esFichaPromo": False,
                    "idGestion": 1,
                    "precio": 450000,
                    "precioVisible01": request["visible"],
                    "fotos": [],
                    "email": "not-public@example.org",
                    "direccionCalle": "Not published street",
                    "similares": [{"precio": 1000}],
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def log_message(self, *args):
                pass

        with (
            ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server,
            tempfile.TemporaryDirectory() as directory,
        ):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = "http://127.0.0.1:" + str(server.server_port) + "/getFicha"
                for visible in (0, 1):
                    response = adapters.fetch(
                        url,
                        {
                            "timeout": 2,
                            "raw_dir": directory,
                            "request_json": {"visible": visible},
                            "json_response_keys": _public_response_keys,
                        },
                    )
                    saved = json.loads(Path(response["raw_path"]).read_text())
                    self.assertEqual("precio" in saved, visible == 1)
                    for hidden in (
                        "email",
                        "direccionCalle",
                        "similares",
                        "precioAnterior",
                        "precioGestion2",
                    ):
                        self.assertNotIn(hidden, saved)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
