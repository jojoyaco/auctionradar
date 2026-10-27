import json
import unittest
from unittest import mock

import auctionradar.connectors.catalog as adapters

URL = "https://www.escogecasa.es/inmueble/vivienda-aislada-en-venta-villaornate-leon/90841/51049"
ROOT = "https://www.escogecasa.es/"
API = ROOT + "nodejs/getFicha"
PHOTO = "https://ngb-repositorio.urbaniza.com/inmuebles/fotos/grandes/1/51049_1.jpg"
SECOND = PHOTO.replace("_1.jpg", "_2.jpg")
SOURCE = {"id": "escogecasa", "url": ROOT, "asset_types": ["housing"]}


def html(props=None):
    props = props or {
        "idInmueble": "51049",
        "idPromocion": None,
        "esFichaPromo": False,
        "lang": 1,
        "idGestion": 1,
    }
    flight = "8:" + json.dumps(["$", "$L12", None, props], ensure_ascii=False) + "\n"
    return (
        "<title>Viviendas aisladas en venta en Villaornate (León), 90841</title>"
        '<meta property="og:url" content="'
        + URL
        + '"><meta property="og:image" content="'
        + PHOTO
        + '">'
        '<img src="/images/logo.png"><div>Cargando...</div><script>self.__next_f.push('
        + json.dumps([1, flight])
        + ")</script>"
    )


def response(url, body, status=200):
    return {"url": url, "final_url": url, "http_status": status, "body": body, "error": None}


class EscogecasaPhotoTests(unittest.TestCase):
    def test_public_client_gallery_is_collected_without_browser_or_cookies(self):
        calls = []

        def fetch(url, context):
            calls.append((url, context.get("request_json")))
            if url == ROOT:
                return response(url, '<a href="' + URL + '">Vivienda</a>')
            if url == URL:
                return response(url, html())
            if url == API:
                return response(
                    url,
                    json.dumps(
                        {
                            "idInmueble": 51049,
                            "referencia": "90841",
                            "esFichaPromo": False,
                            "fotos": [
                                {"urlFoto": PHOTO, "descripcion": "VÍA"},
                                {"urlFoto": SECOND, "descripcion": "FACHADA"},
                            ],
                            "similares": [
                                {
                                    "idInmueble": 999,
                                    "fotos": [{"urlFoto": "https://example.org/wrong.jpg"}],
                                }
                            ],
                        }
                    ),
                )
            raise AssertionError("Unexpected URL " + url)

        result = adapters.discover(
            SOURCE, {"max_pages": 1, "max_details": 1, "observed_at": "now", "fetcher": fetch}
        )
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual([photo["url"] for photo in result["items"][0]["photos"]], [PHOTO, SECOND])
        self.assertEqual(
            next(payload for url, payload in calls if url == API),
            {
                "idIdioma": 1,
                "idInmueble": "51049",
                "idPromocion": None,
                "esFichaPromo": False,
                "idGestion": 1,
            },
        )
        self.assertTrue(all(photo["source_url"] == URL for photo in result["items"][0]["photos"]))

    def enrich(self, payload=None, body=None, status=200):
        from auctionradar.connectors.escogecasa.detail import enrich_escogecasa

        item = adapters.extract_item(body or html(), URL, SOURCE, "now")

        def fetch(url, context):
            return response(url, json.dumps(payload), status)

        return enrich_escogecasa(
            item, {"escogecasa_html": body or html(), "observed_at": "now"}, fetch
        )

    def test_related_images_and_private_contact_data_are_not_promoted(self):
        item, _ = self.enrich(
            {
                "idInmueble": 51049,
                "referencia": "90841",
                "esFichaPromo": False,
                "fotos": [{"urlFoto": PHOTO}],
                "similares": [{"fotos": [{"urlFoto": "https://example.org/related.jpg"}]}],
                "direccionVisible01": 0,
                "direccionCalle": "Not published street",
                "email": "hidden@example.org",
            }
        )
        self.assertEqual([p["url"] for p in item["photos"]], [PHOTO])
        self.assertNotIn("Not published street", json.dumps(item))
        self.assertNotIn("hidden@example.org", json.dumps(item))
        self.assertEqual(
            item["facts_scope"],
            "unscoped_public_page",
            "A verified gallery does not validate the rest of the page",
        )

    def test_wrong_property_or_reference_cannot_supply_gallery(self):
        for identifier, reference in ((999, "90841"), (51049, "WRONG")):
            item, _ = self.enrich(
                {
                    "idInmueble": identifier,
                    "referencia": reference,
                    "fotos": [{"urlFoto": "https://example.org/wrong.jpg"}],
                }
            )
            self.assertNotIn("https://example.org/wrong.jpg", [p["url"] for p in item["photos"]])

    def test_failure_uses_only_the_explicit_current_property_og_photo(self):
        item, _ = self.enrich({}, status=403)
        self.assertEqual([p["url"] for p in item["photos"]], [PHOTO])
        self.assertTrue(item.get("enrichment_limitations"))

    def test_unsafe_images_and_duplicates_are_rejected(self):
        item, _ = self.enrich(
            {
                "idInmueble": 51049,
                "referencia": "90841",
                "fotos": [
                    {"urlFoto": PHOTO},
                    {"urlFoto": PHOTO},
                    {"urlFoto": "javascript:alert(1)"},
                    {"urlFoto": "https://www.escogecasa.es/images/logo.png"},
                ],
            }
        )
        self.assertEqual([p["url"] for p in item["photos"]], [PHOTO])

    def test_other_properties_flight_props_do_not_trigger_request(self):
        from auctionradar.connectors.escogecasa.detail import enrich_escogecasa

        body = html({"idInmueble": "999", "esFichaPromo": False, "lang": 1, "idGestion": 1})
        fetch = mock.Mock()
        item = adapters.extract_item(body, URL, SOURCE, "now")
        enrich_escogecasa(item, {"escogecasa_html": body}, fetch)
        fetch.assert_not_called()

    def test_public_json_transport_is_cookie_free_bounded_and_preserves_distinct_evidence(self):
        import tempfile
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from pathlib import Path

        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append(
                    (payload, self.headers.get("Cookie"), self.headers.get("Authorization"))
                )
                body = json.dumps(
                    {
                        "idInmueble": payload["idInmueble"],
                        "fotos": [],
                        "direccionCalle": "Not published",
                        "email": "secret@example.org",
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

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
                responses = [
                    adapters.fetch(
                        url,
                        {
                            "timeout": 2,
                            "raw_dir": directory,
                            "request_json": {"idInmueble": identifier},
                            "json_response_keys": ["idInmueble", "fotos"],
                        },
                    )
                    for identifier in ("1", "2")
                ]
            finally:
                server.shutdown()
                thread.join()
            self.assertTrue(all(r["http_status"] == 200 for r in responses))
            self.assertEqual(
                received, [({"idInmueble": "1"}, None, None), ({"idInmueble": "2"}, None, None)]
            )
            files = list(Path(directory).glob("*.json"))
            self.assertEqual(len(files), 2)
            for file in files:
                data = json.loads(file.read_text())
                self.assertEqual(set(data), {"idInmueble", "fotos"})


if __name__ == "__main__":
    unittest.main()
