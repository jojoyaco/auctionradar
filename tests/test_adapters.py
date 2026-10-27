import unittest

import auctionradar.connectors.catalog as adapters


class ExtractionTests(unittest.TestCase):
    def test_structured_price_unknown_possession_and_official_photos(self):
        page = """<html><title>Casa en Asturias</title><script type="application/ld+json">{"@type":"Product","name":"Casa Asturias","sku":"ANT123","offers":{"price":"28300","priceCurrency":"EUR","availability":"https://schema.org/InStock"},"image":["/house.jpg"],"description":"54 m², 2 habitaciones"}</script><a href="/conditions.pdf">Condiciones</a></html>"""
        item = adapters.extract_item(
            page,
            "https://example.org/casa/123",
            {"id": "aliseda", "asset_types": ["housing"]},
            "2026-10-17T10:00:00Z",
        )
        self.assertEqual(item["prices"]["asking"], 28300)
        self.assertEqual(item["possession"]["status"], "unknown")
        self.assertEqual(item["photos"][0]["url"], "https://example.org/house.jpg")
        self.assertEqual(item["documents"][0]["read_status"], "not_read")
        self.assertEqual(item["reference"], "ANT123")

    def test_auction_amounts_are_not_asking_price(self):
        page = "<title>Lote vivienda Láchar</title><p>Precio de reserva: 48.246,00 €</p><p>Valor de tasación: 94.600 €</p><p>Depósito: 4.730 €</p><p>Comisión 5% mínimo 500 € más IVA. No visitable. Situación posesoria: No consta.</p>"
        item = adapters.extract_item(
            page,
            "https://gobid.es/es/subasta/33465/lote/1",
            {"id": "gobid", "asset_types": ["housing", "vehicles"]},
            "now",
        )
        self.assertIsNone(item["prices"]["asking"])
        self.assertEqual(item["prices"]["reserve"], 48246)
        self.assertEqual(item["prices"]["auction_value"], 94600)
        self.assertEqual(item["prices"]["deposit"], 4730)
        self.assertEqual(item["possession"]["status"], "unknown")
        self.assertEqual(item["fees"]["buyer_premium"]["minimum_eur"], 500)

    def test_hidden_aliseda_state_preserved_and_possession_is_seller_claim(self):
        page = '<title>Casa La Mortera</title><script id="serverApp-state" type="application/json">{"es:https://api.example/get-property/ant000":{"Referencia":"ANT000","posesion":"LIBRE","Proindiviso":0,"Activo":1,"Foto":"https://storage.googleapis.com/bucket/a.jpg"}}</script>'
        item = adapters.extract_item(
            page,
            "https://example.org/inmueble/ant000",
            {"id": "aliseda", "asset_types": ["housing"]},
            "now",
        )
        self.assertEqual(item["possession"]["status"], "seller_claim_free")
        self.assertFalse(item["possession"]["documented_delivery_free"])
        self.assertEqual(
            item["embedded_data"][0]["data"]["es:https://api.example/get-property/ant000"][
                "Activo"
            ],
            1,
        )
        self.assertEqual(len(item["photos"]), 1)

    def test_detail_discovery_rejects_homepage_and_keeps_boe_details(self):
        html = '<a href="/">Inicio</a><a href="/subastas_ava.php">Buscador</a><a href="/detalleSubasta.php?idSub=SUB-JA-2026-265760">Vivienda</a>'
        links = adapters.discover_links(html, "https://subastas.boe.es/", {"id": "boe"})
        self.assertEqual(
            links["details"],
            ["https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-265760"],
        )
        self.assertIn("https://subastas.boe.es/subastas_ava.php", links["listings"])

    def test_conflicting_visits_not_silently_resolved(self):
        item = adapters.extract_item(
            "<title>Vivienda</title><p>Inmueble visitable.</p><p>Condiciones: NO visitable.</p>",
            "https://example.org/lote/1",
            {"id": "gobid", "asset_types": ["housing"]},
            "now",
        )
        self.assertEqual(item["visits"]["status"], "conflict")

    def test_seed_listing_is_not_misrepresented_as_an_asset(self):
        pages = {
            "https://example.org/": "<title>Home</title>",
            "https://example.org/comprar-viviendas": '<a href="/inmueble/ant123">Casa</a>',
            "https://example.org/inmueble/ant123": "<title>Casa real</title><p>Vivienda independiente 54 m², dos dormitorios en Asturias.</p>",
        }

        def local_fetch(url, context):
            return {
                "url": url,
                "final_url": url,
                "http_status": 200,
                "body": pages[url],
                "error": None,
            }

        result = adapters.discover(
            {"id": "aliseda", "url": "https://example.org/", "asset_types": ["housing"]},
            {
                "max_pages": 2,
                "max_details": 3,
                "observed_at": "now",
                "seeds": ["https://example.org/comprar-viviendas"],
                "fetcher": local_fetch,
            },
        )
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["title"], "Casa real")
        self.assertEqual(result["items"][0]["discovery_url"], "https://example.org/inmueble/ant123")

    def test_aliseda_current_entity_excludes_related_and_translation_fields(self):
        data = {
            "transfer-translate-es": {"posesion": "OCUPADO"},
            "es:https://api.example/get-property/ant123": {
                "id": "ant123",
                "posesion": "LIBRE",
                "Activo": 1,
                "operacion": {"Precio": 28300},
                "imagenes": [{"Uri": "https://example.org/ant123.jpg"}],
            },
            "related": {
                "reference": "wrong",
                "Precio": 999999,
                "posesion": "OCUPADO",
                "image": "https://example.org/other.jpg",
            },
        }
        body = (
            '<title>Vivienda Asturias</title><script id="serverApp-state" type="application/json">'
            + __import__("json").dumps(data)
            + "</script>"
        )
        item = adapters.extract_item(
            body,
            "https://example.org/inmueble/ant123",
            {"id": "aliseda", "asset_types": ["housing"]},
            "now",
        )
        self.assertEqual(item["reference"], "ANT123")
        self.assertEqual(item["prices"]["asking"], 28300)
        self.assertEqual(item["possession"]["status"], "seller_claim_free")
        self.assertEqual(item["availability"]["status"], "seller_claim_available")
        self.assertEqual([p["url"] for p in item["photos"]], ["https://example.org/ant123.jpg"])

    def test_vehicle_title_wins_over_navigation_housing(self):
        item = adapters.extract_item(
            "<title>Ford Transit 2019 furgoneta</title><nav>Vivienda casa piso</nav>",
            "https://example.org/vehicle/123",
            {"id": "ayvens", "asset_types": ["housing", "vehicles"]},
            "now",
        )
        self.assertEqual(item["asset_type"], "vehicles")

    def test_shell_is_not_an_asset_and_contact_is_not_a_detail(self):
        def local_fetch(url, context):
            return {
                "url": url,
                "final_url": url,
                "http_status": 200,
                "body": '<title>Aste Industriali On-Line</title><div id="app"></div>',
                "error": None,
            }

        r = adapters.discover(
            {"id": "gobid", "url": "https://example.org/", "asset_types": ["housing", "vehicles"]},
            {
                "max_pages": 1,
                "max_details": 1,
                "seeds": ["https://example.org/lote/123"],
                "fetcher": local_fetch,
            },
        )
        self.assertEqual(r["items"], [])
        links = adapters.discover_links(
            '<a href="/es-es/car/contact">Contacto</a>', "https://example.org/", {"id": "ayvens"}
        )
        self.assertEqual(links["details"], [])

    def test_boe_unknown_visits_and_no_institutional_photo(self):
        i = adapters.extract_item(
            '<title>Portal de Subastas Electrónicas</title><img src="/imagenes/ayuda.png"><p>Bien 1 - Inmueble (Vivienda)</p><p>Situación posesoria</p><p>No consta</p><p>Visitable</p><p>No consta</p>',
            "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-JA-2026-1234",
            {"id": "boe", "asset_types": ["housing", "vehicles"]},
            "now",
        )
        self.assertEqual(i["asset_type"], "housing")
        self.assertEqual(i["visits"]["status"], "unknown")
        self.assertEqual(i["photos"], [])

    def test_solvia_current_entity_excludes_global_reference_labels(self):
        data = {
            "transfer-translate-es": {"reference": "Referencia del activo"},
            "propertyBasicDetail": {
                "id": "209414-171024-O",
                "precio": 255000,
                "m2": 110.41,
                "totalDormitorios": 3,
                "totalBanyos": 2,
                "textoDescripcion": "Piso en venta",
                "listaImagenesInmueble": [{"url": "https://example.org/current.jpg"}],
            },
            "related": {"price": 1, "image": "https://example.org/other.jpg"},
        }
        body = (
            '<title>Piso actual</title><script id="ng-state" type="application/json">'
            + __import__("json").dumps(data)
            + "</script>"
        )
        i = adapters.extract_item(
            body,
            "https://example.org/es/propiedades/comprar/piso-171024-209414",
            {"id": "solvia", "asset_types": ["housing"]},
            "now",
        )
        self.assertEqual(i["reference"], "209414-171024-O")
        self.assertEqual(i["prices"]["asking"], 255000)
        self.assertEqual(i["normalized_fields"]["built_area_m2"], 110.41)
        self.assertEqual(i["facts_scope"], "current_listing_entity")
        self.assertEqual([p["url"] for p in i["photos"]], ["https://example.org/current.jpg"])

    def test_deposit_minimum_is_not_a_buyer_premium_minimum(self):
        i = adapters.extract_item(
            "<title>Subasta vivienda</title><p>Depósito mínimo 100 euros. Comisión 5% más IVA.</p>",
            "https://example.org/lote/1",
            {"id": "gobid", "asset_types": ["housing"]},
            "now",
        )
        self.assertIsNone(i["fees"]["buyer_premium"]["minimum_eur"])
        self.assertEqual(i["fees"]["buyer_premium"]["rate_percent"], 5)

    def test_multiple_products_select_sku_matching_current_url(self):
        body = '<title>Casa actual</title><script type="application/ld+json">[{"@type":"Product","sku":"CURRENT","name":"Casa actual","offers":{"price":10000},"image":"https://example.org/current.jpg"},{"@type":"Product","sku":"OTHER","name":"Relacionada","offers":{"price":50000},"image":"https://example.org/other.jpg"}]</script>'
        i = adapters.extract_item(
            body,
            "https://example.org/inmueble/current",
            {"id": "test", "asset_types": ["housing"]},
            "now",
        )
        self.assertEqual(i["reference"], "CURRENT")
        self.assertEqual(i["prices"]["asking"], 10000)
        self.assertEqual([p["url"] for p in i["photos"]], ["https://example.org/current.jpg"])

    def test_explicit_other_asset_title_wins_over_housing_navigation(self):
        for title, expected in [
            ("Plaza de garaje en Gijón", "commercial"),
            ("Local comercial en venta", "commercial"),
            ("Solar urbano en venta", "land"),
        ]:
            with self.subTest(title=title):
                i = adapters.extract_item(
                    "<title>" + title + "</title><nav>Compra casa vivienda</nav>",
                    "https://example.org/activo/n/123",
                    {"id": "actcon", "asset_types": ["housing", "vehicles", "land", "commercial"]},
                    "now",
                )
                self.assertEqual(i["asset_type"], expected)

    def test_aeat_alphanumeric_reference_is_not_truncated(self):
        i = adapters.extract_item(
            "<title>Portal de Subastas Electrónicas</title>",
            "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R2896000014",
            {"id": "aeat", "asset_types": ["housing", "vehicles"]},
            "now",
        )
        self.assertEqual(i["reference"], "SUB-AT-2026-26R2896000014")

    def test_actcon_current_heading_supplies_specific_asset_title(self):
        i = adapters.extract_item(
            "<title>Activo - Subastas y venta - Actcon</title><h1>Activo</h1><h2>Subasta Concursal Plaza de Garaje en Gijón</h2><nav>Viviendas casas</nav>",
            "https://example.org/activo/n/plaza-garaje-gijon/",
            {"id": "actcon", "asset_types": ["housing", "commercial"]},
            "now",
        )
        self.assertEqual(i["title"], "Subasta Concursal Plaza de Garaje en Gijón")
        self.assertEqual(i["asset_type"], "commercial")

    def test_ayvens_filters_passenger_car_detail_urls(self):
        body = '<a href="/es-es/car/RENAULT/CAPTUR/Intens/123456">Captur</a><a href="/es-es/car/FORD/TRANSIT/Van/987654">Transit</a>'
        links = adapters.discover_links(body, "https://example.org/es-es/catalog", {"id": "ayvens"})
        self.assertEqual(
            links["details"], ["https://example.org/es-es/car/FORD/TRANSIT/Van/987654"]
        )

    def test_generic_diglo_shell_does_not_become_property(self):
        def fetcher(url, context):
            return {
                "url": url,
                "final_url": url,
                "http_status": 200,
                "body": "<title>Compra y alquiler de inmuebles | Diglo</title><p>Compra y alquiler de inmuebles. Contáctenos para recibir información.</p>",
                "error": None,
            }

        r = adapters.discover(
            {"id": "diglo", "url": "https://example.org/", "asset_types": ["housing"]},
            {
                "max_pages": 1,
                "max_details": 1,
                "seeds": ["https://example.org/venta-pisos/asturias/casa/abc123"],
                "fetcher": fetcher,
            },
        )
        self.assertEqual(r["items"], [])

    def test_explicit_no_possession_is_excluded_despite_unknown_field(self):
        i = adapters.extract_item(
            "<title>Piso en venta</title><p>Situación posesoria: No consta.</p><p>Venta de inmueble sin posesión (ocupado). La situación posesoria será asumida por el comprador.</p>",
            "https://example.org/venta/vivienda-piso/60492587",
            {"id": "servihabitat", "asset_types": ["housing"]},
            "now",
        )
        self.assertEqual(i["possession"]["status"], "occupied_or_tenanted")
        self.assertTrue(
            any("sin posesión" in e.get("quote", "") for e in i["possession"]["evidence"])
        )

    def test_boe_lots_in_same_auction_have_distinct_ids(self):
        source = {"id": "boe", "asset_types": ["housing", "vehicles"]}
        url = "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R2896000014&idLote="
        first = adapters.extract_item("<title>Subasta</title>", url + "1", source, "now")
        second = adapters.extract_item("<title>Subasta</title>", url + "2", source, "now")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["lot_reference"], "1")
        self.assertEqual(second["lot_reference"], "2")

    def test_plural_non_residential_titles_do_not_become_housing(self):
        for title, expected in [
            ("Locales comerciales en venta en Badajoz", "commercial"),
            ("Naves industriales en venta", "commercial"),
            ("Suelos urbanizables en venta", "land"),
        ]:
            with self.subTest(title=title):
                i = adapters.extract_item(
                    "<title>" + title + "</title><nav>Vivienda piso casa</nav>",
                    "https://example.org/inmueble/123",
                    {"id": "escogecasa", "asset_types": ["housing"]},
                    "now",
                )
                self.assertEqual(i["asset_type"], expected)

    def test_explicit_partial_full_domain_is_undivided_share(self):
        title = "Subasta Concursal 50% Pleno Dominio Vivienda en Tercera Planta"
        i = adapters.extract_item(
            "<title>Activo - Actcon</title><h2>" + title + "</h2>",
            "https://example.org/activo/n/cuota/",
            {"id": "actcon", "asset_types": ["housing"]},
            "now",
        )
        self.assertEqual(i["full_ownership_percentage"], 50)
        self.assertEqual(i["right_sold"], "undivided_share")
        self.assertIn("partial_ownership", i["risk_flags"])
        self.assertTrue(any("50%" in e.get("quote", "") for e in i["evidence"]))

    def test_vehicle_fuel_tank_is_not_a_monetary_deposit(self):
        for wording, expected in [
            ("Depósito 60 litros", None),
            ("Depósito 70 L", None),
            ("Depósito 80 Litres", None),
            ("Depósito 500 EUR", 500),
            ("Depósito: 500 €", 500),
        ]:
            with self.subTest(wording=wording):
                i = adapters.extract_item(
                    "<title>Ford Transit furgoneta</title><p>" + wording + "</p>",
                    "https://example.org/car/FORD/TRANSIT/Van/123456",
                    {"id": "ayvens", "asset_types": ["vehicles"]},
                    "now",
                )
                self.assertEqual(i["prices"]["deposit"], expected)


if __name__ == "__main__":
    unittest.main()
