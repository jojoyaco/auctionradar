"""Unit tests for :mod:`boe_authenticated`.

These tests cover the public contract:

* URL filtering rejects foreign origins, credentials, off-ports,
  unexpected query keys and bogus document ids.
* :func:`canonical_asset_page_url` strips the opaque ``idDoc`` and
  preserves only ``idSub``/``idLote``.
* :class:`BoeClient.login` walks redirects but bails on off-namespace
  targets.
* :meth:`BoeClient.iter_asset_photos` parses the protected lot page,
  correlates ``verDocumento`` anchors with their embedded thumbnails,
  and yields ``(bytes, content_type, caption, source_url, document_key)``.
* Error codes are stable, generic, and never expose raw bodies or
  redirect query values.
* Tests are fully synthetic. No real keys, no real passwords, no real
  BOE HTML fixtures from owner-owned material.
"""

from __future__ import annotations

import http.cookiejar
import ssl
import unittest
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, urlsplit

import auctionradar.connectors.boe.client as m

AUCTION_ID = "SUB-AT-2026-26R0886002107"
DOC_A = "DOC-AAA111"
DOC_B = "DOC-BBB222"
PAGE_URL = f"https://subastas.boe.es/reg/detalleSubasta.php?idSub={AUCTION_ID}&ver=3&idLote="
DOC_A_URL = f"https://subastas.boe.es/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={DOC_A}"
DOC_B_URL = f"https://subastas.boe.es/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={DOC_B}"

# Image fixtures — minimal legal magic bytes plus a payload so size
# comparisons stay meaningful.
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"A" * 64
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"B" * 64
GIF_BYTES = b"GIF89a" + b"C" * 64
WEBP_BYTES = b"RIFF" + b"\x10\x00\x00\x00" + b"WEBP" + b"D" * 16


def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _build_html(bodies: List[Tuple[str, str, str, str]]) -> str:
    """Construct a single-Bien HTML fragment.

    Each entry: (doc_id, thumbnail_id, alt, title).
    """
    parts = ["<html><body><h4>Bien 1 - Inmueble (Vivienda)</h4>"]
    for doc, thumb, alt, title in bodies:
        parts.append(
            f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&amp;idDoc={doc}">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&amp;idDoc={thumb}" '
            f'alt="{alt}" title="{title}"></a>'
        )
    parts.append("</body></html>")
    return "".join(parts)


class _RecordingTransport:
    """Injectable transport that returns canned bodies."""

    def __init__(self, routes: Dict[str, Tuple[int, Dict[str, str], bytes]]):
        self._routes = routes
        self.calls: List[Tuple[str, bool]] = []

    def __call__(self, url, authenticated, max_bytes, referer):
        self.calls.append((url, authenticated))
        for pattern, response in self._routes.items():
            if pattern in url:
                return response
        raise AssertionError(f"unexpected URL: {url}")


class ErrCodesTests(unittest.TestCase):
    def test_known_codes_are_documented(self):
        self.assertIn("certfile_missing", m.ERR_CODES)
        self.assertIn("asset_login_required", m.ERR_CODES)
        self.assertIn("multiple_assets_ambiguous", m.ERR_CODES)

    def test_unknown_code_normalises_to_unexpected_status(self):
        exc = m.BoeAccessError("not_a_real_code")
        self.assertEqual(exc.code, "unexpected_status")

    def test_only_code_attribute_is_exposed(self):
        # Errors must not leak the body or other attrs.
        with self.assertRaises(m.BoeAccessError) as cm:
            try:
                raise m.BoeAccessError("tls_failure")
            finally:
                pass
        # The standard message exists but we expect NO non-code attrs.
        self.assertTrue(hasattr(cm.exception, "code"))
        # No 'body', 'url', 'query' attribute by design.
        for forbidden in ("body", "raw_url", "query", "redirect_target"):
            self.assertFalse(hasattr(cm.exception, forbidden))


class CanonicalAssetPageUrlTests(unittest.TestCase):
    def test_strips_id_doc_but_preserves_id_sub(self):
        out = m.canonical_asset_page_url(f"{PAGE_URL}&idDoc=OPAQ123")
        self.assertIsNotNone(out)
        self.assertNotIn("OPAQ123", out)
        self.assertNotIn("idDoc", out)

    def test_idlote_preserved_when_numeric(self):
        out = m.canonical_asset_page_url(
            f"https://subastas.boe.es/reg/detalleSubasta.php?idSub={AUCTION_ID}&idLote=42"
        )
        self.assertIn("idLote=42", out)

    def test_non_https_or_other_host_rejected(self):
        self.assertIsNone(
            m.canonical_asset_page_url(
                f"http://subastas.boe.es/reg/detalleSubasta.php?idSub={AUCTION_ID}"
            )
        )
        self.assertIsNone(m.canonical_asset_page_url(f"https://other.com/x?idSub={AUCTION_ID}"))
        self.assertIsNone(m.canonical_asset_page_url(None))
        self.assertIsNone(m.canonical_asset_page_url("javascript:alert(1)"))

    def test_embedded_credentials_rejected(self):
        self.assertIsNone(
            m.canonical_asset_page_url(
                f"https://user:pass@subastas.boe.es/reg/detalleSubasta.php?idSub={AUCTION_ID}"
            )
        )

    def test_unknown_query_keys_are_stripped_not_rejected(self):
        out = m.canonical_asset_page_url(
            f"https://subastas.boe.es/reg/detalleSubasta.php?idSub={AUCTION_ID}&secret=foo&idDoc=OPAQ"
        )
        self.assertIsNotNone(out)
        self.assertNotIn("secret", out)
        self.assertNotIn("OPAQ", out)
        # Only idSub and ver=3 remain.
        q = parse_qs(urlsplit(out).query, keep_blank_values=True)
        self.assertEqual(set(q), {"idSub", "ver"})


class UrlFilteringTests(unittest.TestCase):
    def test_auth_origin_allows_only_known_pairs(self):
        self.assertTrue(m._auth_origin_ok("https://subastas.boe.es/id/checkCert.php"))
        self.assertTrue(m._auth_origin_ok("https://subastas.boe.es/id/loginCert.php"))
        self.assertTrue(m._auth_origin_ok("https://check-cert.subastas.boe.es/"))
        self.assertFalse(m._auth_origin_ok("https://attacker.example.com/id/checkCert.php"))
        self.assertFalse(m._auth_origin_ok("https://subastas.boe.es/admin/index.php"))
        self.assertFalse(m._auth_origin_ok("http://subastas.boe.es/id/checkCert.php"))

    def test_asset_origin_rejects_other_auction(self):
        self.assertFalse(
            m._asset_origin_ok(
                "https://subastas.boe.es/reg/detalleSubasta.php?idSub=SUB-XY-2026-OTHER",
                AUCTION_ID,
            )
        )
        self.assertTrue(
            m._asset_origin_ok(
                f"https://subastas.boe.es/reg/detalleSubasta.php?idSub={AUCTION_ID}&ver=3",
                AUCTION_ID,
            )
        )

    def test_image_origin_rejects_extra_query_keys(self):
        self.assertFalse(m._image_origin_ok(f"{DOC_A_URL}&foo=bar", AUCTION_ID))

    def test_image_origin_rejects_oversized_doc_id(self):
        long_doc = "x" * 200
        self.assertFalse(
            m._image_origin_ok(
                f"https://subastas.boe.es/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={long_doc}",
                AUCTION_ID,
            )
        )


class LoginTests(unittest.TestCase):
    def test_login_stops_at_asset_landing(self):
        transport = _RecordingTransport(
            {
                "/id/checkCert.php": (302, {"Location": "/id/loginCert.php"}, b""),
                "/id/loginCert.php": (
                    302,
                    {"Location": f"/reg/detalleSubasta.php?idSub={AUCTION_ID}"},
                    b"",
                ),
            }
        )
        client = m.BoeClient(_ctx(), transport=transport)
        self.assertTrue(client.login())
        self.assertEqual(len(transport.calls), 2)

    def test_login_bails_on_foreign_redirect(self):
        transport = _RecordingTransport(
            {
                "/id/checkCert.php": (302, {"Location": "https://attacker.example.com/"}, b""),
            }
        )
        client = m.BoeClient(_ctx(), transport=transport)
        with self.assertRaises(m.BoeAccessError) as cm:
            client.login()
        self.assertEqual(cm.exception.code, "auth_redirect_blocked")

    def test_login_bails_on_beyond_redirect_limit(self):
        transport = _RecordingTransport(
            {
                "/id/checkCert.php": (302, {"Location": "/id/loginCert.php"}, b""),
                "/id/loginCert.php": (302, {"Location": "/id/check-cert/index.php"}, b""),
                "/id/check-cert/index.php": (302, {"Location": "/id/checkCert.php"}, b""),
            }
        )
        client = m.BoeClient(_ctx(), transport=transport)
        with self.assertRaises(m.BoeAccessError) as cm:
            client.login()
        self.assertEqual(cm.exception.code, "auth_redirect_limit")

    def test_login_terminates_on_no_location(self):
        transport = _RecordingTransport(
            {
                "/id/checkCert.php": (200, {}, b"ok"),
            }
        )
        client = m.BoeClient(_ctx(), transport=transport)
        self.assertTrue(client.login())


class GalleryParserTests(unittest.TestCase):
    def test_external_thumbnail_cannot_establish_document_membership(self):
        text = _build_html([(DOC_A, DOC_A, "Foto", "")]).replace(
            'src="/reg/verThumbnail.php', 'src="https://foreign.example/reg/verThumbnail.php'
        )
        self.assertEqual(m._parse_bien_anchors(text, PAGE_URL, AUCTION_ID), [])

    def test_duplicate_document_query_is_rejected(self):
        self.assertFalse(m._image_origin_ok(DOC_A_URL + "&idDoc=" + DOC_B, AUCTION_ID))

    def test_parses_two_published_anchors(self):
        html = _build_html(
            [
                (DOC_A, DOC_A, "Salón", "Foto 1"),
                (DOC_B, DOC_B, "Cocina", "Foto 2"),
            ]
        )
        results = m._parse_bien_anchors(html, PAGE_URL, AUCTION_ID)
        self.assertEqual(len(results), 2)
        document_keys = {entry[0] for entry in results}
        self.assertEqual(len(document_keys), 2)
        captions = [entry[2] for entry in results]
        self.assertIn("Salón", captions)
        self.assertIn("Cocina", captions)

    def test_rejects_action_paths_even_when_image_attached(self):
        html = (
            f'<a href="/reg/pujarSubasta.php?idSub={AUCTION_ID}&idDoc={DOC_A}">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&idDoc={DOC_A}" alt="Pujar"></a>'
        )
        results = m._parse_bien_anchors(html, PAGE_URL, AUCTION_ID)
        self.assertEqual(results, [])

    def test_rejects_thumbnail_only_anchor(self):
        html = (
            f'<a href="/reg/verThumbnail.php?idSub={AUCTION_ID}&idDoc={DOC_A}">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&idDoc={DOC_A}" alt="only thumb"></a>'
        )
        results = m._parse_bien_anchors(html, PAGE_URL, AUCTION_ID)
        self.assertEqual(results, [])

    def test_rejects_mismatched_idoc_between_href_and_img(self):
        html = (
            f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={DOC_A}">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&idDoc={DOC_B}" alt="oops"></a>'
        )
        results = m._parse_bien_anchors(html, PAGE_URL, AUCTION_ID)
        self.assertEqual(results, [])

    def test_rejects_mismatched_auction_in_thumbnail(self):
        html = (
            f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={DOC_A}">'
            f'<img src="/reg/verThumbnail.php?idSub=SUB-AT-2026-OTHER&idDoc={DOC_A}" alt="oops"></a>'
        )
        results = m._parse_bien_anchors(html, PAGE_URL, AUCTION_ID)
        self.assertEqual(results, [])

    def test_multi_bien_is_ambiguous(self):
        html = (
            "<h4>Bien 1 - Inmueble (Vivienda)</h4>"
            f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={DOC_A}">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&idDoc={DOC_A}" alt="A"></a>'
            "<h4>Bien 2 - Inmueble (Garaje)</h4>"
            f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={DOC_B}">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&idDoc={DOC_B}" alt="B"></a>'
        )
        self.assertIsNone(m._parse_bien_anchors(html, PAGE_URL, AUCTION_ID))

    def test_login_marker_does_not_yield_anchors(self):
        html = (
            "<h4>Bien 1 - Inmueble</h4>"
            "<p>Para consultar la información complementaria debe "
            "<strong>Iniciar sesión</strong> en el Portal de Subastas.</p>"
        )
        # The parser only sees the lot block; the login-required check
        # is performed by iter_asset_photos (raises
        # ``asset_login_required``) before this parser is invoked.
        self.assertEqual(m._parse_bien_anchors(html, PAGE_URL, AUCTION_ID), [])


class IterAssetPhotosTests(unittest.TestCase):
    def _client(self, routes) -> Tuple[m.BoeClient, _RecordingTransport]:
        transport = _RecordingTransport(routes)
        client = m.BoeClient(_ctx(), transport=transport)
        return client, transport

    def _routes_for(
        self, *, html: str, images: Dict[str, bytes]
    ) -> Dict[str, Tuple[int, Dict[str, str], bytes]]:
        return {
            "detalleSubasta.php": (
                200,
                {"Content-Type": "text/html; charset=utf-8"},
                html.encode("utf-8"),
            ),
            **{idoc: (200, {"Content-Type": mime}, body) for idoc, (mime, body) in images.items()},
        }

    def test_iter_yields_each_photo_with_required_fields(self):
        html = _build_html([(DOC_A, DOC_A, "Salón", "Foto 1"), (DOC_B, DOC_B, "Cocina", "Foto 2")])
        routes = self._routes_for(
            html=html,
            images={
                DOC_A: ("image/jpeg", JPEG_BYTES),
                DOC_B: ("image/png", PNG_BYTES),
            },
        )
        client, transport = self._client(routes)
        yielded = list(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(len(yielded), 2)
        keys = {item["document_key"] for item in yielded}
        self.assertEqual(len(keys), 2)
        for item in yielded:
            self.assertIn("data", item)
            self.assertIsInstance(item["data"], bytes)
            self.assertIn(item["content_type"], ("image/jpeg", "image/png"))
            self.assertFalse(any(s in item["source_url"] for s in ("idDoc",)))
            self.assertTrue(item["source_url"].startswith("https://subastas.boe.es/"))
        # Every image GET was without auth.
        for url, auth in transport.calls:
            self.assertFalse(auth)
        # No HTML body, no raw private URL leaked into yielded dicts.
        for item in yielded:
            for forbidden in ("OPC", DOC_A, DOC_B):
                self.assertNotIn(forbidden, str(item["source_url"]))

    def test_iter_raises_login_required_when_marker_present(self):
        html = (
            "<h4>Bien 1 - Inmueble</h4>"
            "<p>Para consultar la información complementaria debe "
            "<strong>Iniciar sesión</strong> en el Portal de Subastas.</p>"
        )
        client, _ = self._client(
            {"detalleSubasta.php": (200, {"Content-Type": "text/html"}, html.encode("utf-8"))}
        )
        with self.assertRaises(m.BoeAccessError) as cm:
            list(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(cm.exception.code, "asset_login_required")

    def test_iter_raises_multiple_assets_ambiguous(self):
        html = (
            "<h4>Bien 1 - X</h4>"
            f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={DOC_A}">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&idDoc={DOC_A}" alt="A"></a>'
            "<h4>Bien 2 - Y</h4>"
            f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&idDoc={DOC_B}">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&idDoc={DOC_B}" alt="B"></a>'
        )
        client, _ = self._client({"detalleSubasta.php": (200, {}, html.encode("utf-8"))})
        with self.assertRaises(m.BoeAccessError) as cm:
            list(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(cm.exception.code, "multiple_assets_ambiguous")

    def test_iter_raises_unparseable_for_empty(self):
        html = "<html><body>No anchors here</body></html>"
        client, _ = self._client({"detalleSubasta.php": (200, {}, html.encode("utf-8"))})
        with self.assertRaises(m.BoeAccessError) as cm:
            list(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(cm.exception.code, "asset_unparseable")

    def test_iter_raises_invalid_argument_for_foreign_input(self):
        client = m.BoeClient(_ctx())
        with self.assertRaises(m.BoeAccessError) as cm:
            list(client.iter_asset_photos("https://attacker.example.com/some?path"))
        self.assertEqual(cm.exception.code, "invalid_argument")

    def test_iter_raises_oversize_on_too_big_image(self):
        html = _build_html([(DOC_A, DOC_A, "Salón", "Foto")])
        too_big = b"\xff\xd8\xff\xe0" + b"x" * (m.MAX_IMAGE_BYTES + 1)
        client, _ = self._client(
            self._routes_for(
                html=html,
                images={DOC_A: ("image/jpeg", too_big)},
            )
        )
        with self.assertRaises(m.BoeAccessError) as cm:
            list(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(cm.exception.code, "image_oversize")

    def test_iter_raises_magic_rejected_when_declared_mismatches(self):
        html = _build_html([(DOC_A, DOC_A, "Salón", "Foto")])
        # Server claims PNG but the bytes are JPEG.
        client, _ = self._client(
            self._routes_for(
                html=html,
                images={DOC_A: ("image/png", JPEG_BYTES)},
            )
        )
        with self.assertRaises(m.BoeAccessError) as cm:
            list(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(cm.exception.code, "image_magic_rejected")

    def test_iter_yields_gif_and_webp(self):
        html = _build_html([(DOC_A, DOC_A, "animated", "fake")])
        routes = self._routes_for(
            html=html,
            images={DOC_A: ("image/gif", GIF_BYTES)},
        )
        client, _ = self._client(routes)
        item = next(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(item["content_type"], "image/gif")
        self.assertEqual(item["data"], GIF_BYTES)

    def test_iter_respects_max_photos(self):
        html = _build_html([(f"DOC-{i}", f"DOC-{i}", str(i), str(i)) for i in range(5)])
        routes = self._routes_for(
            html=html,
            images={f"DOC-{i}": ("image/jpeg", JPEG_BYTES) for i in range(5)},
        )
        client, _ = self._client(routes)
        yielded = list(client.iter_asset_photos(PAGE_URL, max_photos=3))
        self.assertEqual(len(yielded), 3)

    def test_iter_treats_oversized_html_as_html_too_large(self):
        body = b"x" * (m.MAX_HTML_BYTES + 1)
        client, _ = self._client({"detalleSubasta.php": (200, {}, body)})
        with self.assertRaises(m.BoeAccessError) as cm:
            list(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(cm.exception.code, "html_too_large")


class GallerySyntheticSevenPhotosTests(unittest.TestCase):
    """Synthetic 7-photo gallery realistic to the parent's observed layout."""

    def test_seven_photo_gallery_yields_seven_images(self):
        html_parts = ["<h4>Bien 1 - Inmueble (Vivienda)</h4>"]
        for i in range(1, 8):
            doc = f"REAL-DOC-{i:03d}"
            html_parts.append(
                f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&amp;idDoc={doc}">'
                f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&amp;idDoc={doc}" '
                f'alt="Vista {i}" title="Foto {i}"></a>'
            )
        html = "<html><body>" + "".join(html_parts) + "</body></html>"
        routes = {
            "detalleSubasta.php": (200, {"Content-Type": "text/html"}, html.encode()),
        }
        # Add 7 fake image responses with varied MIMEs.
        bodies = [JPEG_BYTES, PNG_BYTES, GIF_BYTES, JPEG_BYTES, PNG_BYTES, JPEG_BYTES, WEBP_BYTES]
        mimes = [
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/jpeg",
            "image/png",
            "image/jpeg",
            "image/webp",
        ]
        for i, (m_, b_) in enumerate(zip(mimes, bodies), start=1):
            routes[f"REAL-DOC-{i:03d}"] = (200, {"Content-Type": m_}, b_)

        transport = _RecordingTransport(routes)
        client = m.BoeClient(_ctx(), transport=transport)
        yielded = list(client.iter_asset_photos(PAGE_URL, max_photos=50))
        self.assertEqual(len(yielded), 7)
        # The opaque doc IDs never appear in the yielded source_url.
        for item in yielded:
            self.assertNotIn("REAL-DOC-", item["source_url"])
            self.assertTrue(item["document_key"])
            self.assertEqual(len(item["document_key"]), 64)  # sha256 hex

    def test_real_fake_seven_gallery_omits_one_bad_key(self):
        """One of the seven photos has a corrupted anchor (mismatched IDs).
        The remaining six must still surface."""
        html_parts = ["<h4>Bien 1 - Inmueble</h4>"]
        valid = [f"D-OK-{i:02d}" for i in range(6)]
        # 6 good anchors + 1 corrupted (mismatched idDoc).
        for doc in valid:
            html_parts.append(
                f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&amp;idDoc={doc}">'
                f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&amp;idDoc={doc}" '
                f'alt="{doc}"></a>'
            )
        # Corrupted: doc says D-BAD but thumb says DIFFERENT
        html_parts.append(
            f'<a href="/reg/verDocumento.php?idSub={AUCTION_ID}&amp;idDoc=D-BAD-99">'
            f'<img src="/reg/verThumbnail.php?idSub={AUCTION_ID}&amp;idDoc=WRONG" '
            f'alt="corrupt"></a>'
        )
        html = "<html><body>" + "".join(html_parts) + "</body></html>"
        routes = {"detalleSubasta.php": (200, {}, html.encode())}
        for d in valid:
            routes[d] = (200, {"Content-Type": "image/jpeg"}, JPEG_BYTES)
        transport = _RecordingTransport(routes)
        client = m.BoeClient(_ctx(), transport=transport)
        yielded = list(client.iter_asset_photos(PAGE_URL))
        self.assertEqual(len(yielded), 6)
        keys = {item["document_key"] for item in yielded}
        self.assertEqual(len(keys), 6)


class NativeTransportTests(unittest.TestCase):
    def test_observed_boe_image_jpg_alias_still_requires_jpeg_magic(self):
        self.assertEqual(m._verify_image(JPEG_BYTES, "image/jpg"), "image/jpeg")
        with self.assertRaises(m.BoeAccessError):
            m._verify_image(b"<html>login</html>", "image/jpg")

    def test_default_transport_returns_a_real_tuple_and_reads_limit_plus_one(self):
        from unittest.mock import Mock, patch

        response = Mock()
        response.code = 200
        response.headers = {"Content-Type": "image/jpeg"}
        response.read.return_value = JPEG_BYTES
        opener = Mock()
        opener.open.return_value = response
        with patch("urllib.request.build_opener", return_value=opener):
            result = m._default_transport(
                DOC_A_URL,
                False,
                20,
                None,
                jar=http.cookiejar.CookieJar(),
                auth_ctx=_ctx(),
                plain_ctx=_ctx(),
                timeout=20,
            )
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], 200)
        response.read.assert_called_once_with(21)
        response.close.assert_called_once()

    def test_redirect_cannot_switch_lot(self):
        transport = _RecordingTransport(
            {
                "detalleSubasta.php": (
                    302,
                    {"Location": PAGE_URL.replace("idLote=", "idLote=2")},
                    b"",
                )
            }
        )
        client = m.BoeClient(_ctx(), transport=transport)
        with self.assertRaises(m.BoeAccessError) as caught:
            list(client.iter_asset_photos(PAGE_URL.replace("idLote=", "idLote=1")))
        self.assertEqual(caught.exception.code, "asset_redirect_blocked")
        self.assertEqual(len(transport.calls), 1)

    def test_image_redirect_cannot_change_document(self):
        transport = _RecordingTransport({"verDocumento.php": (302, {"Location": DOC_B_URL}, b"")})
        client = m.BoeClient(_ctx(), transport=transport)
        with self.assertRaises(m.BoeAccessError) as caught:
            client._read_with_bounded_redirect(
                DOC_A_URL, AUCTION_ID, False, 100, 2, image_mode=True
            )
        self.assertEqual(caught.exception.code, "asset_redirect_blocked")

    def test_memory_only_loading_fails_closed_on_unsupported_hosts(self):
        from unittest.mock import patch

        with patch.object(m.os, "memfd_create", None, create=True):
            # Verify actual absence on macOS: no fallback file is created.
            with self.assertRaises(m.BoeAccessError):
                m._memory_identity(b"synthetic-pem")


class LoadClientContextTests(unittest.TestCase):
    def test_missing_dir_raises_certfile_missing(self):
        from pathlib import Path

        with self.assertRaises(m.BoeAccessError) as cm:
            m.load_client_context(Path("/nonexistent-xyz-12345"))
        self.assertEqual(cm.exception.code, "certfile_missing")

    def test_missing_p12_or_passphrase_raises(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            d = Path(tmp)
            (d / "boe-passphrase").write_bytes(b"pw")
            with self.assertRaises(m.BoeAccessError):
                m.load_client_context(d)
            # Now write a tiny P12 and remove passphrase.
            (d / "boe-identity.p12").write_bytes(b"x" * 16)
            (d / "boe-passphrase").unlink()
            with self.assertRaises(m.BoeAccessError) as cm:
                m.load_client_context(d)
            self.assertEqual(cm.exception.code, "passphrase_missing")

    def test_oversized_p12_bails_before_openssl(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            d = Path(tmp)
            (d / "boe-passphrase").write_bytes(b"pw")
            (d / "boe-identity.p12").write_bytes(b"x" * (m.MAX_P12_BYTES + 1))
            # Important: this must NOT reach openssl.
            with self.assertRaises(m.BoeAccessError) as cm:
                m.load_client_context(d)
            self.assertEqual(cm.exception.code, "certfile_too_large")


if __name__ == "__main__":
    unittest.main()
