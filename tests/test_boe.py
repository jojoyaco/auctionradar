import unittest

import auctionradar.connectors.catalog as adapters
from auctionradar.connectors.boe.detail import enrich_boe


class BoeTests(unittest.TestCase):
    def item(self):
        return adapters.extract_item(
            "<title>Subasta vivienda</title><p>Valor de subasta: 193.925 €</p>",
            "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-265760",
            {"id": "boe", "asset_types": ["housing"]},
            "now",
        )

    def test_only_published_bienes_link_and_preserve_general_price(self):
        calls = []
        general = '<a href="./detalleSubasta.php?idSub=SUB-JA-2026-265760&amp;ver=3&amp;idLote=">Bienes</a><a href="?ver=4">Relacionados</a>'
        tab = '<h4>Bien 1 - Inmueble (Vivienda)</h4><table><tr><th>Dirección</th><td>C/ La Xana 11</td></tr><tr><th>Provincia</th><td>Asturias</td></tr><tr><th>Situación posesoria</th><td>No consta</td></tr><tr><th>Visitable</th><td>No consta</td></tr></table><a href="/reg/verDocumento.php?idDoc=abc">Nota simple</a>'

        def fetch(url, context):
            calls.append(url)
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, requests = enrich_boe(self.item(), {"boe_html": general}, fetch)
        self.assertEqual(len(calls), 1)
        self.assertIn("ver=3", calls[0])
        self.assertEqual(item["boe_asset_fields"]["Dirección"], "C/ La Xana 11")
        self.assertEqual(item["possession"]["status"], "unknown")
        self.assertEqual(item["prices"]["auction_value"], 193925)
        self.assertEqual(item["documents"][0]["read_status"], "not_read")
        self.assertEqual(requests[0]["purpose"], "asset_tab")

    def test_occupied_and_unknown_visits_remain_independent(self):
        tab = "<h4>Bien 1 - Inmueble (Vivienda)</h4><table><tr><th>Situación posesoria</th><td>Ocupantes sin derecho de permanencia</td></tr><tr><th>Visitable</th><td>No consta</td></tr></table>"
        i, _ = enrich_boe(
            self.item(),
            {"boe_html": '<a href="?idSub=SUB-JA-2026-265760&ver=3">Bienes</a>'},
            lambda u, c: {"url": u, "http_status": 200, "body": tab},
        )
        self.assertEqual(i["possession"]["status"], "occupied_or_tenanted")
        self.assertEqual(i["visits"]["status"], "unknown")

    def test_desocupado_is_claim_free_not_occupied(self):
        tab = "<table><tr><th>Situación posesoria</th><td>Desocupado</td></tr></table>"
        i, _ = enrich_boe(
            self.item(),
            {"boe_html": '<a href="?idSub=SUB-JA-2026-265760&ver=3">Bienes</a>'},
            lambda u, c: {"url": u, "http_status": 200, "body": tab},
        )
        self.assertEqual(i["possession"]["status"], "seller_claim_free")
        self.assertFalse(i["possession"]["documented_delivery_free"])

    def test_missing_tab_not_guessed_and_failed_tab_preserved(self):
        i, r = enrich_boe(
            self.item(),
            {"boe_html": "<title>Sin pestañas</title>"},
            lambda u, c: self.fail("No guess"),
        )
        self.assertEqual(r, [])
        i, r = enrich_boe(
            self.item(),
            {"boe_html": '<a href="?ver=3&idSub=SUB-JA-2026-265760">Bienes</a>'},
            lambda u, c: {"url": u, "http_status": 403, "body": "Blocked", "error": None},
        )
        self.assertEqual(r[0]["http_status"], 403)
        self.assertEqual(i["possession"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
