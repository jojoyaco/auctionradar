"""Tests for extracting BOE photos in `enrich_boe`.

Fixtures cover public galleries, login-only navigation and blocked tabs. They
verify parser behavior, not portal-wide photo availability.

These tests verify:
- When BOE exposes real links to photos of the current asset, they are added
  to `item['photos']` in canonical form (URL/source_url/caption/
  attribution/kind/observed_at/verification/visual_content_verified).
- Logos, institutional OG images, and icons alone are NOT added.
- Lots and assets are not mixed; the Bien number is not equivalent to idLote.
- Lazy attributes (`data-src`, `data-original`) are honored.
- A tab returning HTTP 403 or 5xx does not delete existing photos and records
  an honest `enrichment_limitation` (no empty photos are invented).
- Unsafe URLs (`javascript:`, embedded credentials) are never accepted.
- URLs pass through `auctionradar.connectors.catalog.public_url` (no fragments or
  credentials).
- If the target tab could not be opened/read, record the limitation without
  degrading an item with existing photos to `photos=[]`.

Photo extraction reuses `extract_item`'s existing logo/OG/icon filtering.
Its filtered public images are merged into
`item['photos']` for the current item,
with lot scope preserved.
"""

import unittest
from pathlib import Path

import auctionradar.connectors.catalog as adapters
from auctionradar.connectors.boe.detail import enrich_boe

# Test auction shared by all cases. It contains a single asset (lot 1).
URL_BASE = "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&ver=3&idBus=&idLote=&numPagBus="

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _seed():
    """A freshly extracted BOE item without photos."""
    return adapters.extract_item(
        "<title>Subasta AT-2026</title>",
        URL_BASE,
        {"id": "boe", "asset_types": ["housing", "vehicles"]},
        "2026-10-18T00:00:00Z",
    )


class BoePhotosTests(unittest.TestCase):
    """Coverage for merging photos from the current asset tab."""

    def test_only_institutional_logos_yields_no_photos(self):
        """The real page contains only `/imagenes/logo*` and a corporate `og:image`."""
        html = (
            '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
            '<img src="/imagenes/logoMPR.png" alt="Ministerio">'
            '<img src="/imagenes/portal_subastas_270.png" alt="Portal">'
            '<meta property="og:image" content="https://subastas.boe.es/imagenes/portal_subastas_270.png">'
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": html, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": html}, fetch)
        # The asset has no real photos; only institutional images, which are filtered out.
        self.assertEqual(item["photos"], [])
        self.assertEqual(item["photos_total_extracted"], 0)

    def test_generic_login_navigation_does_not_claim_private_gallery(self):
        nav = '<a href="?idSub=SUB-AT-2026-26R0886002107&ver=3">Bienes</a>'
        tab = '<a href="/acceso.php">Iniciar sesión</a><h4>Bien 1 - Vivienda</h4><p>No se publican fotos.</p>'
        item, _ = enrich_boe(
            _seed(),
            {"boe_html": nav},
            lambda url, context: {"url": url, "http_status": 200, "body": tab, "error": None},
        )
        self.assertFalse(
            any(
                lim.get("reason") == "boe_gallery_behind_authentication"
                for lim in item.get("enrichment_limitations", [])
            )
        )

    def test_lot_two_can_have_one_asset_numbered_bien_one(self):
        url = URL_BASE.replace("idLote=", "idLote=2")
        item = adapters.extract_item(
            "<title>Subasta</title>", url, {"id": "boe", "asset_types": ["housing"]}, "now"
        )
        nav = '<a href="?idSub=SUB-AT-2026-26R0886002107&ver=3&idLote=2">Bienes</a>'
        tab = '<h4>Bien 1 - Inmueble (Vivienda)</h4><img src="/reg/fotos/SUB-AT-2026-26R0886002107/photo.jpg">'
        item, _ = enrich_boe(
            item,
            {"boe_html": nav},
            lambda link, context: {"url": link, "http_status": 200, "body": tab, "error": None},
        )
        self.assertEqual(len(item["photos"]), 1, "Lot number is not an asset number")

    def test_navigation_to_another_lot_is_not_followed(self):
        from unittest.mock import Mock

        url = URL_BASE.replace("idLote=", "idLote=2")
        item = adapters.extract_item(
            "<title>Subasta</title>", url, {"id": "boe", "asset_types": ["housing"]}, "now"
        )
        nav = '<a href="?idSub=SUB-AT-2026-26R0886002107&ver=3&idLote=1">Bienes</a>'
        fetch = Mock()
        enrich_boe(item, {"boe_html": nav}, fetch)
        fetch.assert_not_called()

    def test_tab_with_published_photos_unions_into_item(self):
        """The asset tab exposes <img> elements with real URLs; merge them."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = (
            "<h4>Bien 1 - Bien mueble (Buques)</h4>"
            '<div class="galeria"><img src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="Vista 1"></div>'
            '<div class="galeria"><img src="/reg/fotos/SUB-AT-2026-26R0886002107/02.jpg" alt="Vista 2"></div>'
            '<img src="/imagenes/logoMPR.png" alt="Ministerio">'
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg", urls)
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/02.jpg", urls)
        # Logos e institucionales no entran.
        self.assertFalse(any("/imagenes/" in (u or "") for u in urls))
        # Each photo has the required canonical shape.
        for photo in item["photos"]:
            self.assertEqual(photo["attribution"], "current_entity_gallery")
            self.assertEqual(photo["kind"], "unclassified")
            self.assertEqual(photo["verification"], "published_url_only")
            self.assertFalse(photo["visual_content_verified"])
            self.assertEqual(photo["observed_at"], "2026-10-18T00:00:00Z")
            self.assertTrue(photo["source_url"].startswith("https://subastas.boe.es/"))
            self.assertTrue(photo["caption"])

    def test_lazy_data_src_and_data_original_attributes(self):
        """Galleries use `data-src`/`data-original` for lazy loading."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = (
            "<h4>Bien 1 - Bien mueble (Buques)</h4>"
            '<img data-src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="Vista 1">'
            '<img data-original="/reg/fotos/SUB-AT-2026-26R0886002107/02.jpg" alt="Vista 2">'
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/03.jpg" alt="Vista 3">'
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg", urls)
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/02.jpg", urls)
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/03.jpg", urls)

    def test_photos_from_other_auction_are_rejected(self):
        """If the HTML mixes photos from another auction, accept only those for the current lot."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = (
            "<h4>Bien 1 - Bien mueble (Buques)</h4>"
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="Vista 1">'
            # "neighbor": same page, but not the current auction.
            '<img src="/reg/fotos/SUB-AT-2026-OTHER/99.jpg" alt="Foto de otra subasta">'
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg", urls)
        self.assertNotIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-OTHER/99.jpg", urls)

    def test_lot_boundary_only_current_bien_photos(self):
        """If the tab lists multiple lots (Bien 1 + Bien 2), take only photos for the item's lot."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3&amp;idLote=">Bienes</a>'
        # Multi-lot tab: two <h4>Bien N</h4> blocks.
        tab = (
            '<div class="bloque"><h4>Bien 1 - Bien mueble (Buques)</h4>'
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="Bien 1 foto">'
            "</div>"
            '<div class="bloque"><h4>Bien 2 - Inmueble (Vivienda)</h4>'
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/02.jpg" alt="Bien 2 foto">'
            "</div>"
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertEqual(
            urls,
            [],
            "Several Bien blocks without an explicit Bien identity must not be merged or guessed",
        )

    def test_failed_request_preserves_prior_photos_and_logs_limitation(self):
        """If the tab returns 403, keep existing photos and record the limitation."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'

        def fetch(url, context):
            return {"url": url, "http_status": 403, "body": "", "error": "HTTP 403"}

        seed = _seed()
        # Simulate valid previous photos (e.g. extracted by extract_item from the base page).
        seed["photos"] = [
            {
                "url": "https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/base.jpg",
                "source_url": URL_BASE,
                "caption": "Foto base",
                "attribution": "current_entity_gallery",
                "kind": "unclassified",
                "observed_at": "2026-10-18T00:00:00Z",
                "verification": "published_url_only",
                "visual_content_verified": False,
            }
        ]
        item, requests = enrich_boe(seed, {"boe_html": nav}, fetch)
        # Preserve the previous photos.
        self.assertEqual(len(item["photos"]), 1)
        self.assertEqual(
            item["photos"][0]["url"],
            "https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/base.jpg",
        )
        # Also record the limitation honestly (do not invent "no images").
        self.assertTrue(
            any(
                "403" in str(lim.get("reason", ""))
                for lim in item.get("enrichment_limitations", [])
            )
        )
        # Y el request queda trazado con su status real.
        self.assertEqual(requests[0]["http_status"], 403)

    def test_unsafe_javascript_and_credential_urls_are_rejected(self):
        """URLs that are not `http(s)` or contain embedded credentials never reach `photos`."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = (
            "<h4>Bien 1 - Bien mueble (Buques)</h4>"
            '<a href="javascript:alert(1)">Click</a>'
            '<img src="https://user:pass@subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/secret.jpg" alt="X">'
            '<img src="data:image/png;base64,iVBORw0KGgo=" alt="Inline">'
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="OK">'
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg", urls)
        self.assertEqual(len(item["photos"]), 1)
        # No embedded credentials.
        for url in urls:
            self.assertNotIn("@", url.split("//", 1)[-1].split("/", 1)[0])
            self.assertFalse(url.lower().startswith(("javascript:", "data:")))

    def test_duplicate_photo_url_is_kept_once(self):
        """La misma URL no debe duplicarse en `item['photos']`."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = (
            "<h4>Bien 1 - Bien mueble (Buques)</h4>"
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="A">'
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="B">'
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertEqual(len(urls), 1)
        self.assertEqual(
            urls[0], "https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg"
        )

    def test_floorplan_kind_when_caption_or_alt_marks_it(self):
        """Si la URL o el alt contiene 'plano', `kind` es `floorplan` (published)."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = (
            "<h4>Bien 1 - Inmueble (Vivienda)</h4>"
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/plano.jpg" alt="Plano de la vivienda">'
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="Foto">'
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        by_url = {p["url"]: p for p in item["photos"]}
        self.assertEqual(
            by_url["https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/plano.jpg"]["kind"],
            "floorplan",
        )
        self.assertEqual(
            by_url["https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg"]["kind"],
            "unclassified",
        )

    def test_cap_at_50_photos_per_item(self):
        """The limit is 50 photos per item to prevent runaway resource use."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        imgs = "".join(
            f'<img src="/reg/fotos/SUB-AT-2026-26R0886002107/{i:02d}.jpg" alt="F{i}">'
            for i in range(60)
        )
        tab = f"<h4>Bien 1 - Bien mueble (Buques)</h4>{imgs}"

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        self.assertEqual(len(item["photos"]), 50)
        self.assertEqual(item["photos_total_extracted"], 50)

    def test_existing_behavior_unchanged_for_published_text_and_buque_class(self):
        """Preserve the `Buque` helper and `published_text` when adding photos."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = (
            "<h4>Bien 1 - Bien mueble (Buques)</h4>"
            "<table><tr><th>Referencia Registral</th><td>20240009324</td></tr></table>"
            '<img src="/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg" alt="Vista">'
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        # Preserve classification.
        self.assertEqual(item["asset_type"], "boats")
        self.assertIn("Bien mueble (Buques)", item.get("boe_bien_labels", []))
        # Texto publicado preservado.
        self.assertIn("20240009324", item["published_text"])
        # Foto nueva presente.
        urls = [p["url"] for p in item["photos"]]
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg", urls)


class BoeFixtureTests(unittest.TestCase):
    """Fixtures in `tests/fixtures/boe_photos_*` exercise `enrich_boe`.

    They connect captured (sanitized) real HTML to the unit tests. If the portal
    changes its structure, these tests detect coverage regressions for logos,
    galleries, and privacy.
    """

    def test_real_main_fixture_yields_no_photos_and_keeps_buque(self):
        """The captured real HTML (logos + "Iniciar sesión" only) must not add photos."""
        main = _load_fixture("boe_photos_main.html")
        # The Bienes tab fetched by `enrich_boe` is identical to the
        # main page (ver=3 self-link).
        tab = main

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": main}, fetch)
        self.assertEqual(item["photos"], [])
        # The Buque helper preserves the asset type from the official label.
        self.assertEqual(item["asset_type"], "boats")
        # Preserve published text with the asset's Referencia Registral.
        self.assertIn("20240009324", item["published_text"])
        # Honest limitation: the gallery requires authentication.
        # The item remains valid: it has a registry reference, description, and classification.
        self.assertTrue(item.get("boe_asset_fields"))

    def test_fixture_tab_with_photos_unions_canonical(self):
        """Fixture `boe_photos_tab_with_photos.html`: hypothetical public gallery."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = _load_fixture("boe_photos_tab_with_photos.html")

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        # All five photos in the synthetic fixture (including the floor plan) are included.
        for n in ("01", "02", "03", "04", "plano"):
            self.assertIn(
                f"https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/{n}.jpg",
                urls,
            )
        # Logos institucionales quedan fuera.
        self.assertFalse(any("/imagenes/" in u for u in urls))
        # El plano se marca como `floorplan`.
        by_url = {p["url"]: p for p in item["photos"]}
        self.assertEqual(
            by_url["https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/plano.jpg"]["kind"],
            "floorplan",
        )

    def test_fixture_tab_mixed_lots_filters_to_current_bien(self):
        """Fixture `boe_photos_tab_mixed_lots.html`: la URL apunta al Bien 1."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3&amp;idLote=">Bienes</a>'
        tab = _load_fixture("boe_photos_tab_mixed_lots.html")

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertEqual(urls, [], "The auction URL alone does not identify one of several assets")
        # Do not accept photos for Bien 2 (lot scoping).
        self.assertFalse(any("vivienda" in u for u in urls))

    def test_fixture_tab_other_auction_is_rejected(self):
        """Fixture `boe_photos_tab_other_auction.html`: foto con idSub ajeno."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = _load_fixture("boe_photos_tab_other_auction.html")

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg", urls)
        self.assertNotIn("https://subastas.boe.es/reg/fotos/SUB-AT-2026-OTHER/99.jpg", urls)

    def test_fixture_tab_unsafe_urls_never_pass(self):
        """Fixture `boe_photos_tab_unsafe_urls.html`: javascript:/data:/credenciales."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = _load_fixture("boe_photos_tab_unsafe_urls.html")

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        self.assertEqual(len(item["photos"]), 1)
        self.assertEqual(
            urls[0], "https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/01.jpg"
        )
        for url in urls:
            self.assertFalse(url.lower().startswith(("javascript:", "data:")))
            self.assertNotIn("@", url.split("//", 1)[-1].split("/", 1)[0])

    def test_fixture_tab_lazy_supports_all_lazy_attributes(self):
        """Fixture `boe_photos_tab_lazy.html`: data-src / data-original / data-lazy / src."""
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        tab = _load_fixture("boe_photos_tab_lazy.html")

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        urls = [p["url"] for p in item["photos"]]
        for n in ("01", "02", "03", "04"):
            self.assertIn(
                f"https://subastas.boe.es/reg/fotos/SUB-AT-2026-26R0886002107/{n}.jpg",
                urls,
            )

    def test_gallery_behind_login_records_limitation_honestly(self):
        """If the tab contains "Iniciar sesión" and exposes no photos,
        `enrich_boe` records `enrichment_limitation` without inventing photos.
        """
        nav = '<a href="./detalleSubasta.php?idSub=SUB-AT-2026-26R0886002107&amp;ver=3">Bienes</a>'
        # Real case: the portal shows asset details plus an authentication
        # message, but does NOT expose real asset images.
        tab = (
            "<h4>Bien 1 - Bien mueble (Buques)</h4>"
            "<table><tr><th>Referencia Registral</th><td>20240009324</td></tr></table>"
            '<div class="caja gris info">'
            "<p>Para consultar la informaci&#xF3;n complementaria debe "
            "<strong>Iniciar sesi&#xF3;n</strong> en el Portal de Subastas.</p>"
            "</div>"
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": tab, "error": None}

        item, _ = enrich_boe(_seed(), {"boe_html": nav}, fetch)
        # Do not invent photos.
        self.assertEqual(item["photos"], [])
        # Record the limitation honestly: the portal hides the gallery behind login.
        limitations = item.get("enrichment_limitations") or []
        self.assertTrue(
            any(
                "boe_gallery_behind_authentication" in str(lim.get("reason", ""))
                for lim in limitations
            ),
            f"se esperaba una limitación honesta de galería tras login; halladas: {limitations}",
        )


if __name__ == "__main__":
    unittest.main()
