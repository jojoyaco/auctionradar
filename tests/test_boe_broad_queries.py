"""Tests for BOE/AEAT broad partitioned queries (mode --all-assets + --category).

Covers:
- Mapping taxonomy UI codes to (kind, subtype) for the public form
  subastas_ava.php (verified against the live page).
- Partition wrapper with a bounded budget and fair share per group.
- Default broad mode (without --category) issues the 3 I/V/M buckets without a subtype.
- Explicit --category includes boats (M/14) — no starvation with max_pages=2.
- AEAT retains the source filter (dato[0]='A').
- CLI parser accepts repeatable --category, validates up to 16 codes, and rejects
  unknown codes.
- Separate raw_dir per partition to avoid artifact-cache collisions.
- Report combination preserves depth and basic deduplication.
- BOE enrichment detects 'Buque' in the official asset tab and marks boats.
"""

import json
import unittest
from urllib.parse import parse_qsl, urlsplit

import auctionradar.connectors.catalog as adapters
from auctionradar.connectors.boe.detail import enrich_boe

# ---------------------------------------------------------------------------
# Fixture helpers: minimal HTML recognized by the adapter parser.
# Listings return a detalleSubasta.php detail link; detail pages return
# a title containing the category keyword.
# ---------------------------------------------------------------------------


def _listing_html(detail_urls):
    anchors = "".join('<a href="{}">Lote</a>'.format(url) for url in detail_urls)
    return (
        "<html><head><title>Portal de Subastas Electrónicas</title></head>"
        "<body><table>"
        "<tr><td>{}</td></tr>"
        "</table></body></html>"
    ).format(anchors)


def _house_detail():
    return (
        "<html><head><title>Subasta Concursal. Vivienda en Gijón.</title></head>"
        "<body><p>Vivienda 80 m², 2 dormitorios. Valor de subasta 95.000 €.</p>"
        '<a href="/reg/verDocumento.php?idDoc=abc">Nota simple</a></body></html>'
    )


def _van_detail():
    return (
        "<html><head><title>Subasta Concursal. Furgoneta Citroen Berlingo.</title></head>"
        "<body><p>Furgoneta industrial. Valor de subasta 4.500 €.</p>"
        '<a href="/reg/verDocumento.php?idDoc=def">Ficha técnica</a></body></html>'
    )


def _boat_detail():
    return (
        "<html><head><title>Subasta Concursal. Buque-embarcacion deportiva.</title></head>"
        "<body><p>Embarcación a motor, 8 metros. Valor de subasta 22.000 €.</p>"
        '<a href="/reg/verDocumento.php?idDoc=ghi">Registro</a></body></html>'
    )


def _boe_partition_request(detail_urls, base_url="https://subastas.boe.es"):
    """Return listing HTML for the group search URL."""
    body = _listing_html(detail_urls)
    return {"url": base_url, "final_url": base_url, "http_status": 200, "body": body, "error": None}


def _parse_query(url):
    """Return a {key: value} dictionary of query-string parameters."""
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


def _assert_group_in(urls, kind, subtype, message):
    """Verify that at least one listing URL contains (kind, subtype)."""
    for url in urls:
        if "subastas_ava.php" not in url:
            continue
        params = _parse_query(url)
        if params.get("dato[3]") == kind and params.get("dato[4]", "") == subtype:
            return url
    raise AssertionError(
        "{}: ninguna URL contiene dato[3]={} dato[4]={}".format(message, kind, subtype or "")
    )


# ---------------------------------------------------------------------------
# Mapping tests
# ---------------------------------------------------------------------------


class BoeCategoryMappingTests(unittest.TestCase):
    """Coverage for UI code to (kind, subtype) mappings."""

    def test_housing_maps_to_I_501(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        groups = _boe_category_groups(["housing"])
        kinds_subtypes = [(k, s) for k, s, _ in groups]
        self.assertIn(("I", "501"), kinds_subtypes)

    def test_vans_maps_to_V_9102(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        groups = _boe_category_groups(["vans"])
        kinds_subtypes = [(k, s) for k, s, _ in groups]
        self.assertIn(("V", "9102"), kinds_subtypes)

    def test_boats_maps_to_M_14(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        groups = _boe_category_groups(["boats"])
        kinds_subtypes = [(k, s) for k, s, _ in groups]
        self.assertIn(("M", "14"), kinds_subtypes)

    def test_three_categories_include_boats(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        groups = _boe_category_groups(["housing", "vans", "boats"])
        kinds_subtypes = {(k, s) for k, s, _ in groups}
        self.assertEqual(kinds_subtypes, {("I", "501"), ("V", "9102"), ("M", "14")})

    def test_default_buckets_emit_IVM_without_subtype(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        groups = _boe_category_groups([])
        kinds_subtypes = [(k, s) for k, s, _ in groups]
        self.assertEqual(kinds_subtypes, [("I", ""), ("V", ""), ("M", "")])

    def test_other_or_unknown_falls_back_to_default_buckets(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        groups = _boe_category_groups(["other"])
        kinds_subtypes = [(k, s) for k, s, _ in groups]
        self.assertEqual(kinds_subtypes, [("I", ""), ("V", ""), ("M", "")])

    def test_max_sixteen_categories_are_accepted(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        all_codes = [
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
            "furniture",
            "collectibles",
            "other",
            "unknown",
        ]
        groups = _boe_category_groups(all_codes)
        # _BOE_CATEGORY_QUERY tiene 13 entradas; el resto cae a buckets default.
        # Cap a 16 grupos.
        self.assertLessEqual(len(groups), 16)

    def test_duplicates_are_collapsed(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        groups = _boe_category_groups(["housing", "housing", "vans"])
        kinds_subtypes = [(k, s) for k, s, _ in groups]
        self.assertEqual(kinds_subtypes.count(("I", "501")), 1)

    def test_unknown_codes_are_dropped(self):
        from auctionradar.connectors.catalog import _boe_category_groups

        groups = _boe_category_groups(["housing", "__nope__", "vans"])
        kinds_subtypes = [(k, s) for k, s, _ in groups]
        self.assertEqual(sorted(kinds_subtypes), [("I", "501"), ("V", "9102")])


class BoeQueryUrlTests(unittest.TestCase):
    """Public URL built from the verified form."""

    def test_boe_query_contains_required_published_fields(self):
        from auctionradar.connectors.catalog import _boe_partition_query

        url = _boe_partition_query("I", "501", aeat_origin="")
        params = _parse_query(url)
        self.assertEqual(params["campo[0]"], "SUBASTA.ORIGEN")
        self.assertEqual(params["campo[2]"], "SUBASTA.ESTADO.CODIGO")
        self.assertEqual(params["dato[2]"], "EJ")
        self.assertEqual(params["campo[3]"], "BIEN.TIPO")
        self.assertEqual(params["dato[3]"], "I")
        self.assertEqual(params["dato[4]"], "501")
        self.assertEqual(params["accion"], "Buscar")
        self.assertEqual(params["page_hits"], "50")

    def test_aeat_origin_filter_is_A(self):
        from auctionradar.connectors.catalog import _boe_partition_query

        url = _boe_partition_query("V", "9102", aeat_origin="A")
        params = _parse_query(url)
        self.assertEqual(params["dato[0]"], "A")

    def test_kind_bucket_omits_subtype(self):
        from auctionradar.connectors.catalog import _boe_partition_query

        url = _boe_partition_query("M", "", aeat_origin="")
        params = _parse_query(url)
        self.assertEqual(params["dato[3]"], "M")
        self.assertNotIn("dato[4]", params)

    def test_boats_query_targets_M_14(self):
        from auctionradar.connectors.catalog import _boe_partition_query

        url = _boe_partition_query("M", "14", aeat_origin="")
        params = _parse_query(url)
        self.assertEqual(params["dato[3]"], "M")
        self.assertEqual(params["dato[4]"], "14")


# ---------------------------------------------------------------------------
# Partition wrapper integration
# ---------------------------------------------------------------------------


class BroadPartitionDiscoverTests(unittest.TestCase):
    """The discover wrapper partitions by category and distributes the budget."""

    SOURCE = {
        "id": "boe",
        "url": "https://subastas.boe.es/",
        "asset_types": ["housing", "vehicles"],
    }

    def _fetcher(self, captured, fixture_per_query):
        """Build a fetcher that records visited URLs and returns fixtures."""
        seen = []

        def fetch(url, context):
            seen.append(url)
            captured.append(
                {
                    "url": url,
                    "raw_dir": context.get("raw_dir"),
                    "partition_label": context.get("partition_label"),
                    "max_pages": context.get("max_pages"),
                    "max_details": context.get("max_details"),
                    "broad_partition": context.get("broad_partition"),
                    "seeds": context.get("seeds"),
                }
            )
            if "subastas_ava.php" in url:
                # Listing — return one link for each available detail page.
                params = _parse_query(url)
                kind = params.get("dato[3]", "")
                subtype = params.get("dato[4]", "")
                detail_urls = fixture_per_query.get((kind, subtype), [])
                return _boe_partition_request(detail_urls, base_url=url)
            # Detalle.
            for label, body in fixture_per_query.get("__details__", {}).items():
                if label in url:
                    return {
                        "url": url,
                        "final_url": url,
                        "http_status": 200,
                        "body": body,
                        "error": None,
                    }
            return {
                "url": url,
                "final_url": url,
                "http_status": 200,
                "body": "<html><head><title>404</title></head></html>",
                "error": None,
            }

        return fetch, seen

    def test_three_categories_produce_three_queries_with_fair_budget(self):
        captured = []
        fixture = {
            ("I", "501"): ["https://subastas.boe.es/detalleSubasta.php?idSub=SUB-HOUSE-1"],
            ("V", "9102"): ["https://subastas.boe.es/detalleSubasta.php?idSub=SUB-VAN-1"],
            ("M", "14"): ["https://subastas.boe.es/detalleSubasta.php?idSub=SUB-BOAT-1"],
            "__details__": {
                "SUB-HOUSE-1": _house_detail(),
                "SUB-VAN-1": _van_detail(),
                "SUB-BOAT-1": _boat_detail(),
            },
        }
        fetcher, seen = self._fetcher(captured, fixture)
        result = adapters.discover(
            self.SOURCE,
            {
                "max_pages": 2,
                "max_details": 6,
                "observed_at": "2026-10-17T00:00:00Z",
                "fetcher": fetcher,
                "all_assets": True,
                "categories": ["housing", "vans", "boats"],
                "raw_dir": "/tmp/boe_raw",
            },
        )
        # 3 queries I/501, V/9102, M/14 generadas.
        listing_requests = [c for c in captured if "subastas_ava.php" in c["url"]]
        self.assertEqual(len(listing_requests), 3)
        _assert_group_in([c["url"] for c in listing_requests], "I", "501", "housing missing")
        _assert_group_in([c["url"] for c in listing_requests], "V", "9102", "vans missing")
        _assert_group_in([c["url"] for c in listing_requests], "M", "14", "boats missing")
        # Presupuesto fair share: per_pages=1, per_details=2 con 3 grupos
        # y caps 2/6.
        for entry in listing_requests:
            self.assertEqual(entry["max_pages"], 1)
            self.assertEqual(entry["max_details"], 2)
            self.assertTrue(entry["broad_partition"])
            self.assertEqual(len(entry["seeds"]), 1)
            self.assertIn("subastas_ava.php", entry["seeds"][0])
        # Each partition has its own raw_dir.
        labels = {entry["partition_label"] for entry in listing_requests}
        self.assertEqual(len(labels), 3)
        for entry in listing_requests:
            self.assertIn(entry["partition_label"], entry["raw_dir"])
        # The combined result contains all 3 items.
        self.assertEqual(result["counts"]["items"], 3)
        # Profundidad propagada.
        self.assertEqual(result["depth"], "listing_and_details")
        # Topes combinados reportados.
        self.assertEqual(result["query"]["combined_pages_cap"], 3)
        self.assertEqual(result["query"]["combined_details_cap"], 6)
        self.assertEqual(result["query"]["per_group_pages"], 1)
        self.assertEqual(result["query"]["per_group_details"], 2)

    def test_boats_not_starved_with_default_budget(self):
        """With max_pages=2 and max_details=6, the boats group still returns
        its link and one detail page. Before the fix, V/M ran out of budget.
        """
        captured = []
        fixture = {
            ("I", "501"): ["https://subastas.boe.es/detalleSubasta.php?idSub=SUB-HOUSE-1"],
            ("V", "9102"): ["https://subastas.boe.es/detalleSubasta.php?idSub=SUB-VAN-1"],
            ("M", "14"): ["https://subastas.boe.es/detalleSubasta.php?idSub=SUB-BOAT-1"],
            "__details__": {
                "SUB-HOUSE-1": _house_detail(),
                "SUB-VAN-1": _van_detail(),
                "SUB-BOAT-1": _boat_detail(),
            },
        }
        fetcher, seen = self._fetcher(captured, fixture)
        result = adapters.discover(
            self.SOURCE,
            {
                "max_pages": 2,
                "max_details": 6,
                "observed_at": "2026-10-17T00:00:00Z",
                "fetcher": fetcher,
                "all_assets": True,
                "categories": ["housing", "vans", "boats"],
                "raw_dir": "/tmp/boe_raw",
            },
        )
        # All 3 groups issue a query and fetch at least one detail page.
        listing_requests = [c for c in captured if "subastas_ava.php" in c["url"]]
        self.assertEqual(len(listing_requests), 3)
        detail_requests = [c for c in captured if "detalleSubasta.php" in c["url"]]
        self.assertGreaterEqual(len(detail_requests), 3)
        # items contains a boat and a van (not only housing).
        titles = [item.get("title", "") for item in result["items"]]
        self.assertTrue(
            any(
                "Buque" in t
                or "Embarcacion" in t
                or "Embarcación" in t.lower()
                or "lancha" in t.lower()
                or "barco" in t.lower()
                or "boat" in t.lower()
                for t in titles
            ),
            "boats no detectado en titles={}".format(titles),
        )

    def test_default_buckets_emit_three_queries_when_no_categories(self):
        captured = []
        fixture = {
            ("I", ""): [],
            ("V", ""): [],
            ("M", ""): [],
        }
        fetcher, _ = self._fetcher(captured, fixture)
        adapters.discover(
            self.SOURCE,
            {
                "max_pages": 2,
                "max_details": 6,
                "observed_at": "now",
                "fetcher": fetcher,
                "all_assets": True,
                "categories": [],
                "raw_dir": "/tmp/raw",
            },
        )
        listing_requests = [c for c in captured if "subastas_ava.php" in c["url"]]
        kinds = {_parse_query(c["url"]).get("dato[3]") for c in listing_requests}
        self.assertEqual(kinds, {"I", "V", "M"})

    def test_aeat_origin_A_preserved_in_partition_query(self):
        captured = []
        fixture = {("I", "501"): []}
        fetcher, _ = self._fetcher(captured, fixture)
        adapters.discover(
            {"id": "aeat", "url": "https://subastas.boe.es/", "asset_types": ["housing"]},
            {
                "max_pages": 2,
                "max_details": 6,
                "observed_at": "now",
                "fetcher": fetcher,
                "all_assets": True,
                "categories": ["housing"],
                "raw_dir": "/tmp/aeat",
            },
        )
        listing = next(c for c in captured if "subastas_ava.php" in c["url"])
        self.assertEqual(_parse_query(listing["url"])["dato[0]"], "A")

    def test_legacy_narrow_mode_still_uses_I501_and_V9102(self):
        captured = []
        fixture = {("I", "501"): [], ("V", "9102"): []}
        fetcher, _ = self._fetcher(captured, fixture)
        adapters.discover(
            self.SOURCE,
            {
                "max_pages": 2,
                "max_details": 3,
                "observed_at": "now",
                "fetcher": fetcher,
                "all_assets": False,
                "asset_type": "housing",
                "raw_dir": "/tmp/raw",
            },
        )
        # Narrow mode without --all-assets does not partition.
        partition_entries = [c for c in captured if c["broad_partition"]]
        self.assertEqual(partition_entries, [])
        listing = [c for c in captured if "subastas_ava.php" in c["url"]]
        kinds = {_parse_query(c["url"]).get("dato[3]") for c in listing}
        self.assertEqual(kinds, {"I"})

    def test_single_category_full_budget(self):
        captured = []
        fixture = {("M", "14"): []}
        fetcher, _ = self._fetcher(captured, fixture)
        adapters.discover(
            self.SOURCE,
            {
                "max_pages": 2,
                "max_details": 6,
                "observed_at": "now",
                "fetcher": fetcher,
                "all_assets": True,
                "categories": ["boats"],
                "raw_dir": "/tmp/raw",
            },
        )
        # Only the group query is issued; the detail queue is empty.
        partition_listings = [
            c for c in captured if c["broad_partition"] and "subastas_ava.php" in c["url"]
        ]
        self.assertEqual(len(partition_listings), 1)
        # Con 1 grupo, el presupuesto completo aplica.
        self.assertEqual(partition_listings[0]["max_pages"], 2)
        self.assertEqual(partition_listings[0]["max_details"], 6)

    def test_partition_raw_dirs_are_distinct(self):
        captured = []
        fixture = {
            ("I", "501"): [],
            ("V", "9102"): [],
            ("M", "14"): [],
        }
        fetcher, _ = self._fetcher(captured, fixture)
        adapters.discover(
            self.SOURCE,
            {
                "max_pages": 2,
                "max_details": 6,
                "observed_at": "now",
                "fetcher": fetcher,
                "all_assets": True,
                "categories": ["housing", "vans", "boats"],
                "raw_dir": "/tmp/boe_root",
            },
        )
        listing = [c for c in captured if "subastas_ava.php" in c["url"]]
        raw_dirs = [c["raw_dir"] for c in listing]
        # Each partition uses its own subdirectory; none overlap.
        self.assertEqual(len(raw_dirs), 3)
        self.assertEqual(len(set(raw_dirs)), 3)
        for raw_dir in raw_dirs:
            self.assertTrue(raw_dir.startswith("/tmp/boe_root/"))

    def test_partition_query_omits_canonical_root(self):
        captured = []
        fixture = {("I", "501"): []}
        fetcher, _ = self._fetcher(captured, fixture)
        adapters.discover(
            self.SOURCE,
            {
                "max_pages": 1,
                "max_details": 1,
                "observed_at": "now",
                "fetcher": fetcher,
                "all_assets": True,
                "categories": ["housing"],
                "raw_dir": "/tmp/raw",
                "seeds": [
                    "https://subastas.boe.es/",  # canonical root (NO debe aparecer)
                    "https://subastas.boe.es/index.php?ver=1",  # legacy root (NO)
                    "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-LEGACY",  # detail seed
                ],
            },
        )
        # The wrapper replaces seeds so only the group URL is present.
        listing = [c for c in captured if "subastas_ava.php" in c["url"]]
        self.assertEqual(len(listing), 1)
        # The seeds injected into the partition contain only the group URL.
        self.assertEqual(len(listing[0]["seeds"]), 1)
        self.assertIn("subastas_ava.php", listing[0]["seeds"][0])

    def test_combined_status_partial_with_items(self):
        captured = []
        fixture = {
            ("I", "501"): ["https://subastas.boe.es/detalleSubasta.php?idSub=SUB-H"],
            "__details__": {"SUB-H": _house_detail()},
        }
        fetcher, _ = self._fetcher(captured, fixture)
        result = adapters.discover(
            self.SOURCE,
            {
                "max_pages": 1,
                "max_details": 1,
                "observed_at": "now",
                "fetcher": fetcher,
                "all_assets": True,
                "categories": ["housing"],
                "raw_dir": "/tmp/raw",
            },
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["counts"]["items"], 1)
        self.assertEqual(result["query"]["broad_partition"], True)


# ---------------------------------------------------------------------------
# Enrichment: the official asset label identifies boats.
# ---------------------------------------------------------------------------


class BoeBuqueLabelTests(unittest.TestCase):
    """Enrichment: 'Buque' in the official tab sets asset_type='boats'."""

    def _item(self, base="https://subastas.boe.es/detalleSubasta.php?idSub=SUB-BOAT-99"):
        return adapters.extract_item(
            "<title>Subasta Concursal</title>",
            base,
            {"id": "boe", "asset_types": ["housing", "vehicles"]},
            "now",
        )

    def test_buque_in_bien_tab_marks_boats(self):
        bien = (
            "<h4>Bien 1 - Buque</h4>"
            "<table><tr><th>Dirección</th><td>Puerto de Gijón</td></tr>"
            "<tr><th>Situación posesoria</th><td>No consta</td></tr></table>"
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": bien, "error": None}

        item, _ = enrich_boe(
            self._item(), {"boe_html": '<a href="?ver=3&idSub=SUB-BOAT-99">Bienes</a>'}, fetch
        )
        self.assertEqual(item["asset_type"], "boats")
        self.assertIn("Buque", item.get("boe_bien_labels", []))
        notes = item.get("enrichment_notes", [])
        self.assertTrue(
            any(n.get("note") == "asset_type_boats_from_official_bien_label" for n in notes)
        )

    def test_inmueble_vivienda_still_marks_housing(self):
        bien = (
            "<h4>Bien 1 - Inmueble (Vivienda)</h4>"
            "<table><tr><th>Dirección</th><td>C/ La Xana 11</td></tr></table>"
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": bien, "error": None}

        item, _ = enrich_boe(
            self._item(), {"boe_html": '<a href="?ver=3&idSub=SUB-BOAT-99">Bienes</a>'}, fetch
        )
        self.assertEqual(item["asset_type"], "housing")

    def test_buque_does_not_apply_when_only_navigation_mentions_it(self):
        # El nav aparece en published_text global; NO debe disparar la regla.
        bien = (
            "<nav>Subastas de Buques y otros activos</nav>"
            "<h4>Bien 1 - Vehículo turismo</h4>"
            "<table><tr><th>Tipo</th><td>Turismo</td></tr></table>"
        )

        def fetch(url, context):
            return {"url": url, "http_status": 200, "body": bien, "error": None}

        item, _ = enrich_boe(
            self._item(), {"boe_html": '<a href="?ver=3&idSub=SUB-BOAT-99">Bienes</a>'}, fetch
        )
        self.assertNotEqual(item["asset_type"], "boats")


# ---------------------------------------------------------------------------
# CLI parser: repeatable --category option and validation.
# ---------------------------------------------------------------------------


class CLICategoryParserTests(unittest.TestCase):
    """auctionradar.cli.parser expone --category repetible y lo valida."""

    def test_default_category_empty_list(self):
        from auctionradar.cli import parser

        ns = parser().parse_args([])
        self.assertEqual(ns.category, [])

    def test_repeatable_category_argument(self):
        from auctionradar.cli import parser

        ns = parser().parse_args(["--category", "housing", "--category", "boats"])
        self.assertEqual(ns.category, ["housing", "boats"])

    def test_more_than_sixteen_categories_rejected(self):
        from auctionradar.cli import parser

        codes = ["housing"] * 17
        with self.assertRaises(SystemExit):
            parser().parse_args(["--category"] + codes)

    def test_unknown_category_rejected(self):
        """Code validation happens in main() after parse_args."""
        import tempfile
        from pathlib import Path

        import auctionradar.cli as search_cli

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "catalog.json").write_text(json.dumps({"sources": []}))
            (folder / "profile.json").write_text(json.dumps({}))
            (folder / "feedback.json").write_text(json.dumps({}))
            (folder / "seeds.json").write_text(json.dumps({}))
            import io
            import sys

            backup_argv = sys.argv
            try:
                sys.argv = [
                    "auctionradar",
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
                    "--category",
                    "__nope__",
                    "--timeout",
                    "1",
                    "--max-pages",
                    "1",
                    "--max-details",
                    "1",
                    "--workers",
                    "1",
                ]
                sys.stdout = io.StringIO()
                sys.stderr = io.StringIO()
                with self.assertRaises(SystemExit) as ctx:
                    search_cli.main()
                self.assertEqual(ctx.exception.code, 2)
            finally:
                sys.argv = backup_argv
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__

    def test_context_categories_passed_to_adapters(self):
        """El wrapper principal inyecta categories en context."""
        import json
        import tempfile
        from pathlib import Path

        import auctionradar.cli as search_cli

        catalog = {"sources": []}
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "catalog.json").write_text(json.dumps(catalog))
            (folder / "profile.json").write_text(json.dumps({}))
            (folder / "feedback.json").write_text(json.dumps({}))
            (folder / "seeds.json").write_text(json.dumps({}))
            import io
            import sys

            backup_argv = sys.argv
            try:
                sys.argv = [
                    "auctionradar",
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
                    "--category",
                    "housing",
                    "--category",
                    "boats",
                    "--category",
                    "vans",
                    "--timeout",
                    "1",
                    "--max-pages",
                    "1",
                    "--max-details",
                    "1",
                    "--workers",
                    "1",
                ]
                buf = io.StringIO()
                sys.stdout = buf
                sys.stderr = io.StringIO()
                search_cli.main()
                sys.stdout = backup_argv if False else sys.__stdout__
            finally:
                sys.argv = backup_argv
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__
            output = buf.getvalue()
            run = json.loads(output)
            self.assertEqual(run["filters"].get("categories"), ["housing", "boats", "vans"])
            self.assertTrue(run["filters"].get("all_assets"))


if __name__ == "__main__":
    unittest.main()
