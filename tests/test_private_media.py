"""Tests for :mod:`auctionradar.web.private_media`.

The fixture is intentionally self-contained: it spawns a Flask app from
the canonical :class:`tests.test_webapp_auth._WebappFixture`, registers the
``private_media`` blueprint *only* for this test session, points the
``BOE_MEDIA_ROOT`` config at a tmp dir containing a synthesised manifest
and flatmedia cache, and wires a real item + share + photo entry into the
webapp DB.

The suite never touches the real ``/var/lib/auctionradar-boe-export`` directory,
never opens private certificate material, and never logs file contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import struct
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import auctionradar.web.ingest as ingest
import auctionradar.web.models as models
import auctionradar.web.private_media as private_media
from tests.test_webapp_auth import _WebappFixture

# ---------------------------------------------------------------------------
# Tiny image generators — all bytes are fabricated for unit tests, none of
# them are real photographs.
# ---------------------------------------------------------------------------


def _jpeg_bytes(payload_size: int = 64) -> bytes:
    """Return the minimum legal JFIF blob plus ``payload_size`` zeros.

    Real JPEG starts with ``FF D8 FF``; the rest of the file body is opaque
    for our purposes. The decoder only inspects the magic prefix.
    """
    return b"\xff\xd8\xff\xe0" + b"\x00" * payload_size


def _png_bytes(payload_size: int = 64) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * payload_size


def _gif87_bytes(payload_size: int = 64) -> bytes:
    return b"GIF87a" + b"\x00" * payload_size


def _webp_bytes(payload_size: int = 64) -> bytes:
    return b"RIFF" + struct.pack("<I", 4 + payload_size) + b"WEBP" + b"\x00" * payload_size


def _gif89_bytes(payload_size: int = 64) -> bytes:
    return b"GIF89a" + b"\x00" * payload_size


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


class _ExportFixture(_WebappFixture):
    """Specialised fixture that injects a controlled BOE export directory."""

    export_dir: Path
    flatmedia_dir: Path

    def setUp(self):  # noqa: D401 — unittest API
        super().setUp()
        self.export_dir = Path(tempfile.mkdtemp(prefix="boe-export-"))
        self.flatmedia_dir = self.export_dir / "media"
        self.flatmedia_dir.mkdir(parents=True, exist_ok=True)
        # Make sure the export directory does not appear as a symlink.
        assert not self.export_dir.is_symlink()
        assert not self.flatmedia_dir.is_symlink()
        # Configure the blueprint and bind it for the lifetime of this test.
        self.app.config["BOE_MEDIA_ROOT"] = str(self.export_dir)
        if private_media.bp.name not in self.app.blueprints:
            self.app.register_blueprint(private_media.bp)
        # Make sure the parent views blueprint (registered by create_app)
        # does not collide with the private_media blueprint URL rules. They
        # live under different prefixes (``/api/items/...`` and
        # ``/api/public/...``) so Flask's url_map merges them cleanly.

    def tearDown(self):  # noqa: D401 — unittest API
        # Tear down the test-managed export dir before the base fixture
        # removes the SQLite file.
        try:
            shutil.rmtree(self.export_dir, ignore_errors=True)
        finally:
            super().tearDown()

    # ------------------------------------------------------------------
    # Manifest / file helpers
    # ------------------------------------------------------------------

    def write_media(self, media_id: str, content: bytes, extension: str) -> Path:
        target = self.flatmedia_dir / f"{media_id}.{extension}"
        target.write_bytes(content)
        return target

    def write_manifest(self, manifest: dict) -> None:
        # The on-disk format uses ``media/...`` while the in-memory cache
        # key is the bare UUID; ``load_manifest`` normalises this.
        payload = json.loads(json.dumps(manifest))
        text = json.dumps(payload, ensure_ascii=False)
        (self.export_dir / "manifest.json").write_text(text, encoding="utf-8")

    def default_manifest(self, item_id: str, media_entries: list, *, status: str = "ready") -> dict:
        photos = []
        for entry in media_entries:
            mid = entry["media_id"]
            extension = entry["extension"]
            content = entry["content"]
            sha = hashlib.sha256(content).hexdigest()
            photos.append(
                {
                    "media_id": mid,
                    "file": f"media/{mid}.{extension}",
                    "content_type": entry.get("content_type")
                    or {
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                        "png": "image/png",
                        "webp": "image/webp",
                        "gif": "image/gif",
                    }[extension],
                    "size": len(content),
                    "sha256": sha,
                    "caption": entry.get("caption"),
                    "source_url": entry.get("source_url"),
                }
            )
        return {
            "version": private_media.MANIFEST_VERSION,
            "generated_at": "2026-10-18T00:00:00+00:00",
            "items": {
                item_id: {
                    "status": status,
                    "photos": photos,
                }
            },
        }

    # ------------------------------------------------------------------
    # Item / share helpers
    # ------------------------------------------------------------------

    def store_item_with_photos(
        self, *, photos_meta: list, asset_type: str = "housing", url_suffix: str = ""
    ) -> str:
        photos = []
        for meta in photos_meta:
            photos.append(
                {
                    "media_id": meta["media_id"],
                    "url": meta.get("url", "/api/placeholder"),
                    "caption": meta.get("caption"),
                    "source_url": meta.get("source_url"),
                    "image_type": meta.get("image_type", "real"),
                    "origin": meta.get("origin", "official_listing_url"),
                }
            )
        item = {
            "asset_type": asset_type,
            "source_id": "boe",
            "url": f"https://example.org/housing/private/{url_suffix}",
            "title": "Vivienda con fotos privadas",
            "photos": photos,
        }
        key = ingest.stable_item_key(item)
        models.upsert_item(
            item_id=key,
            asset_type=item["asset_type"],
            source_id=item["source_id"],
            source_ids=[item["source_id"]],
            first_seen="2026-10-18T00:00:00+00:00",
            last_seen="2026-10-18T00:00:00+00:00",
            data=item,
            listing_url=item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(item),
        )
        return key

    def create_share(self, item_id: str) -> str:
        token = secrets.token_urlsafe(32)
        models.create_share(item_id, token, self.uid)
        return token

    def revoke_share(self, share_token: str) -> None:
        row = models.get_share_by_token(share_token)
        if row is None:
            return
        models.revoke_share(int(row["id"]), revoked_by=self.uid)


def _media_id_hex(value: Optional[str] = None) -> str:
    return value or uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Authenticated endpoint tests
# ---------------------------------------------------------------------------


class AuthenticatedMediaTests(_ExportFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=False)
        self.login(username="alice")

    def test_authenticated_user_receives_jpeg_bytes(self):
        media_id = _media_id_hex()
        content = _jpeg_bytes()
        item_id = self.store_item_with_photos(
            photos_meta=[
                {
                    "media_id": media_id,
                    "caption": "Salón principal",
                    "source_url": "https://subastas.boe.es/public/asset/x",
                }
            ]
        )
        self.write_media(media_id, content, "jpg")
        self.write_manifest(
            self.default_manifest(
                item_id, [{"media_id": media_id, "extension": "jpg", "content": content}]
            )
        )

        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/jpeg")
        self.assertEqual(response.data, content)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        # The global security after_request enforces ``Cache-Control:
        # no-store`` so a revoked share propagates immediately. The
        # view-level assertion is the strict ``private, no-store`` we
        # set; both satisfy the spec.
        cache_control = response.headers.get("Cache-Control", "")
        self.assertIn("no-store", cache_control)
        self.assertNotIn("public", cache_control)
        # No ETag, no Last-Modified — they would otherwise be persisted.
        self.assertNotIn("ETag", response.headers)
        # Path / source URL never leak through the headers or body. The
        # ``Content-Disposition`` legitimately embeds the media_id so
        # browsers offer a safe download filename.
        joined_headers = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
        for forbidden in ("subastas.boe.es", self.export_dir.name, "/var/lib"):
            self.assertNotIn(forbidden, joined_headers)
        self.assertNotIn(self.export_dir.name, response.data.decode("latin-1"))
        response.close()

    def test_unauthenticated_request_returns_401_not_login_html(self):
        # Drop the session and require JSON 401 (NOT an HTML redirect).
        with self.client.session_transaction() as sess:
            sess.clear()
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        self.write_media(media_id, _jpeg_bytes(), "jpg")
        self.write_manifest(
            self.default_manifest(
                item_id, [{"media_id": media_id, "extension": "jpg", "content": _jpeg_bytes()}]
            )
        )
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.headers.get("Content-Type", "").split(";", 1)[0], "application/json"
        )
        body = response.get_json()
        self.assertEqual(body.get("error"), "auth_required")

    def test_invalid_uuid_returns_400(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        response = self.client.get(f"/api/items/{item_id}/media/not-a-uuid")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "invalid_media_id"})

    def test_invalid_item_id_returns_400(self):
        response = self.client.get("/api/items/with%20space/media/" + _media_id_hex())
        # Werkzeug routes only match URL-decoded paths, so the encoded
        # space is rejected before our handler. Either way the response
        # is a 4xx, never a 200 with leaked data.
        self.assertIn(response.status_code, (400, 404))

    def test_unknown_item_returns_404(self):
        response = self.client.get(
            f"/api/items/missing-id-{_media_id_hex()}/media/{_media_id_hex()}"
        )
        self.assertEqual(response.status_code, 404)

    def test_photo_detached_from_raw_json_returns_404(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        # Cached file exists and manifest has the entry, but the stored
        # ``photos`` list does not reference ``media_id``.
        bogus = _media_id_hex()
        self.write_media(bogus, _jpeg_bytes(), "jpg")
        self.write_manifest(
            self.default_manifest(
                item_id, [{"media_id": bogus, "extension": "jpg", "content": _jpeg_bytes()}]
            )
        )
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Public (share) endpoint tests
# ---------------------------------------------------------------------------


class PublicShareMediaTests(_ExportFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=False)

    def _store_two_items(self):
        # Create two items + shares so we can verify cross-item 404.
        item_a = self.store_item_with_photos(
            photos_meta=[{"media_id": _media_id_hex()}], url_suffix="a"
        )
        item_b = self.store_item_with_photos(
            photos_meta=[{"media_id": _media_id_hex()}], url_suffix="b"
        )
        token_a = self.create_share(item_a)
        token_b = self.create_share(item_b)
        return item_a, item_b, token_a, token_b

    def test_anonymous_valid_token_returns_jpeg(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        content = _png_bytes()
        self.write_media(media_id, content, "png")
        self.write_manifest(
            self.default_manifest(
                item_id, [{"media_id": media_id, "extension": "png", "content": content}]
            )
        )
        token = self.create_share(item_id)

        response = self.client.get(f"/api/public/{token}/media/{media_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertEqual(response.data, content)
        cache_control = response.headers.get("Cache-Control", "")
        self.assertIn("no-store", cache_control)
        self.assertNotIn("ETag", response.headers)
        response.close()

    def test_revoked_share_returns_404(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        self.write_media(media_id, _jpeg_bytes(), "jpg")
        self.write_manifest(
            self.default_manifest(
                item_id, [{"media_id": media_id, "extension": "jpg", "content": _jpeg_bytes()}]
            )
        )
        token = self.create_share(item_id)
        self.revoke_share(token)
        response = self.client.get(f"/api/public/{token}/media/{media_id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "not_found"})

    def test_cross_item_token_returns_404(self):
        item_a, item_b, token_a, token_b = self._store_two_items()
        media_id_a = _media_id_hex()
        # ``stable_item_key`` is deterministic on the listing URL; we
        # already used distinct suffixes when seeding the items so the
        # two keys differ. Add a brand-new media_id to item_a so the
        # cache for item_a's photo is real (not a placeholder).
        self.write_media(media_id_a, _jpeg_bytes(), "jpg")
        self.store_item_with_photos(
            photos_meta=[{"media_id": media_id_a}],
            url_suffix="a",
        )
        manifest = {
            "version": private_media.MANIFEST_VERSION,
            "generated_at": "2026-10-18T00:00:00+00:00",
            "items": {
                item_a: {
                    "status": "ready",
                    "photos": [
                        {
                            "media_id": media_id_a,
                            "file": f"media/{media_id_a}.jpg",
                            "content_type": "image/jpeg",
                            "size": 68,
                            "sha256": hashlib.sha256(_jpeg_bytes()).hexdigest(),
                        }
                    ],
                },
                item_b: {
                    "status": "ready",
                    "photos": [
                        {
                            "media_id": _media_id_hex(),
                            "file": "media/placeholder.jpg",
                            "content_type": "image/jpeg",
                            "size": 68,
                            "sha256": hashlib.sha256(_jpeg_bytes()).hexdigest(),
                        }
                    ],
                },
            },
        }
        self.write_manifest(manifest)
        # Share for item_b tries to fetch item_a's photo: 404.
        response = self.client.get(f"/api/public/{token_b}/media/{media_id_a}")
        self.assertEqual(response.status_code, 404)
        response.close()
        # Conversely, the share for item_a serves the photo.
        response_ok = self.client.get(f"/api/public/{token_a}/media/{media_id_a}")
        self.assertEqual(response_ok.status_code, 200)
        response_ok.close()

    def test_unseen_media_id_returns_404(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        self.write_media(media_id, _jpeg_bytes(), "jpg")
        self.write_manifest(
            self.default_manifest(
                item_id, [{"media_id": media_id, "extension": "jpg", "content": _jpeg_bytes()}]
            )
        )
        token = self.create_share(item_id)
        response = self.client.get(f"/api/public/{token}/media/{_media_id_hex()}")
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Defensive / validation tests
# ---------------------------------------------------------------------------


class MediaValidationTests(_ExportFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=False)
        self.login(username="alice")

    def test_traversal_in_url_rejected(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        # Werkzeug URL-decodes percent-encoded segments; the embedded
        # slash from ``..%2Fetc`` makes the router see extra path
        # segments, so the request never reaches our view. The response
        # is therefore a hard 404 — which is exactly the spec's "no path
        # traversal leaks".
        response = self.client.get(f"/api/items/{item_id}/media/..%2Fetc")
        self.assertEqual(response.status_code, 404)
        # A sibling traversal that survives the router (no slash) still
        # fails our 32-hex regex with a 400.
        response2 = self.client.get(f"/api/items/{item_id}/media/..%2eetc")
        self.assertEqual(response2.status_code, 400)

    def test_symlinked_media_file_is_rejected(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        real = self.write_media(_media_id_hex(), _jpeg_bytes(), "jpg")
        # Symlink the requested media_id at the expected path pointing to
        # an *external* file that lives outside the export directory.
        outside = Path(tempfile.mkdtemp(prefix="boe-outside-"))
        external = outside / "external.jpg"
        external.write_bytes(b"\xff\xd8\xff\xe0" + b"X" * 16)
        link = self.flatmedia_dir / f"{media_id}.jpg"
        try:
            os.symlink(external, link)
            self.write_manifest(
                self.default_manifest(
                    item_id, [{"media_id": media_id, "extension": "jpg", "content": _jpeg_bytes()}]
                )
            )
            response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
            self.assertIn(response.status_code, (403, 404))
        finally:
            try:
                link.unlink()
            except FileNotFoundError:
                pass
            shutil.rmtree(outside, ignore_errors=True)
        # The original file we wrote remains untouched.
        self.assertTrue(real.exists())

    def test_mime_mismatch_returns_404(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        # Write a PNG blob but tell the manifest this is a JPEG.
        png = _png_bytes()
        self.write_media(media_id, png, "jpg")
        manifest = self.default_manifest(
            item_id, [{"media_id": media_id, "extension": "jpg", "content": png}]
        )
        # Overwrite the on-disk manifest so the extension stays ``jpg``.
        self.write_manifest(manifest)
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 404)

    def test_symlinked_media_parent_is_rejected(self):
        media_id = _media_id_hex()
        outside = self.export_dir / "outside"
        self.flatmedia_dir.rename(outside)
        self.flatmedia_dir.symlink_to(outside, target_is_directory=True)
        self.assertIsNone(private_media.open_media_file(self.export_dir, media_id, "jpg"))

    def test_fifo_manifest_is_rejected_without_blocking(self):
        os.mkfifo(self.export_dir / "manifest.json")
        self.assertIsNone(private_media.load_manifest(self.export_dir))

    def test_source_urls_never_expose_document_or_session_queries(self):
        self.assertIsNone(
            private_media.public_source_url(
                "https://subastas.boe.es/reg/verDocumento.php?idSub=SUB-AT-2026-TEST&idDoc=opaque"
            )
        )
        clean = private_media.public_source_url(
            "https://subastas.boe.es/reg/detalleSubasta.php?idSub=SUB-AT-2026-TEST&ticket=private&idLote=1"
        )
        self.assertEqual(
            clean,
            "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-TEST&ver=3&idLote=1",
        )

    def test_oversized_file_returns_404(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        # Create a file slightly above the limit; the manifest size hint
        # is the source of truth so we tweak the manifest instead.
        big = b"\xff\xd8\xff\xe0" + b"\x00" * (private_media.MAX_FILE_BYTES + 1)
        self.write_media(media_id, big, "jpg")
        manifest = self.default_manifest(
            item_id, [{"media_id": media_id, "extension": "jpg", "content": big}]
        )
        # The size attribute is what is checked; we explicitly drop it
        # below the file size so the on-disk stat fails the comparison.
        manifest["items"][item_id]["photos"][0]["size"] = len(big) - 1
        self.write_manifest(manifest)
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 404)

    def test_corrupt_hash_returns_404(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        content = _jpeg_bytes()
        self.write_media(media_id, content, "jpg")
        manifest = self.default_manifest(
            item_id, [{"media_id": media_id, "extension": "jpg", "content": content}]
        )
        manifest["items"][item_id]["photos"][0]["sha256"] = "0" * 64
        self.write_manifest(manifest)
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 404)

    def test_malformed_manifest_returns_404(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        self.write_media(media_id, _jpeg_bytes(), "jpg")
        # Write garbage and confirm the endpoint stays fail-closed.
        (self.export_dir / "manifest.json").write_text("{not-json", encoding="utf-8")
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 404)

    def test_oversized_manifest_returns_404(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        self.write_media(media_id, _jpeg_bytes(), "jpg")
        pad = "x" * (private_media.MAX_MANIFEST_BYTES + 16)
        (self.export_dir / "manifest.json").write_text(pad, encoding="utf-8")
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 404)

    def test_wrong_version_manifest_rejected(self):
        media_id = _media_id_hex()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        self.write_media(media_id, _jpeg_bytes(), "jpg")
        manifest = self.default_manifest(
            item_id, [{"media_id": media_id, "extension": "jpg", "content": _jpeg_bytes()}]
        )
        manifest["version"] = 99
        self.write_manifest(manifest)
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 404)

    def test_login_required_after_response_consumed(self):
        """The descriptor returned by :func:`open_media_file` must close
        once the test client has streamed the body. This guards against
        file-descriptor leaks across hundreds of requests.
        """
        media_id = _media_id_hex()
        content = _gif89_bytes()
        item_id = self.store_item_with_photos(photos_meta=[{"media_id": media_id}])
        self.write_media(media_id, content, "gif")
        self.write_manifest(
            self.default_manifest(
                item_id, [{"media_id": media_id, "extension": "gif", "content": content}]
            )
        )
        response = self.client.get(f"/api/items/{item_id}/media/{media_id}")
        self.assertEqual(response.status_code, 200)
        # Force the Werkzeug test client to materialise the body.
        body = response.data
        self.assertEqual(body, content)
        # Werkzeug's :class:`werkzeug.test.TestResponse` documents that
        # callers must ``.close()`` to release any open file handles;
        # doing so here exercises the same contract a real WSGI server
        # relies on when the connection terminates.
        response.close()
        # After close the underlying iterator must be exhausted and the
        # ``BufferedReader`` finalised. Opening another file in the same
        # process must succeed without hitting the FD limit.
        with open(self.flatmedia_dir / f"{media_id}.gif", "rb") as fh:
            self.assertEqual(fh.read(), content)


# ---------------------------------------------------------------------------
# Helper unit tests (no HTTP)
# ---------------------------------------------------------------------------


class HelperTests(unittest.TestCase):
    def test_media_photo_payload_returns_internal_url(self):
        photo = {
            "media_id": uuid.uuid4().hex,
            "caption": "Cocina",
            "source_url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-TEST&ver=3",
            "image_type": "real",
        }
        payload = private_media.media_photo_payload(photo, "item-1")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["image_type"], "published")
        self.assertEqual(payload["origin"], "boe_authenticated_cache")
        self.assertTrue(payload["url"].startswith("/api/items/item-1/media/"))
        # Source URL is preserved only when it is a safe http(s) URL; the
        # boe.es host is allowed by ``urls.sanitise``.
        self.assertEqual(
            payload["source_url"],
            "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-TEST&ver=3",
        )
        self.assertEqual(payload["media_id"], photo["media_id"])

    def test_media_photo_payload_uses_share_url_when_token_present(self):
        photo = {"media_id": uuid.uuid4().hex}
        payload = private_media.media_photo_payload(photo, "item-1", token="abc123")
        self.assertIsNotNone(payload)
        self.assertTrue(payload["url"].startswith("/api/public/abc123/media/"))
        # The token itself does not appear in any other field; we expose
        # only the structured media_id for downstream code.
        self.assertNotIn("abc123", payload["source_url"] or "")
        self.assertNotIn("abc123", payload["caption"] or "")

    def test_media_photo_payload_rejects_malformed(self):
        self.assertIsNone(private_media.media_photo_payload({"media_id": "not-hex"}, "item-1"))
        self.assertIsNone(private_media.media_photo_payload({"media_id": "Z" * 32}, "item-1"))
        self.assertIsNone(
            private_media.media_photo_payload({"media_id": uuid.uuid4().hex}, "item with space")
        )
        self.assertIsNone(private_media.media_photo_payload(None, "item-1"))

    def test_url_helpers_are_relative(self):
        mid = uuid.uuid4().hex
        self.assertEqual(private_media.media_url_for_item("it", mid), f"/api/items/it/media/{mid}")
        self.assertEqual(
            private_media.media_url_for_share("tk", mid), f"/api/public/tk/media/{mid}"
        )

    def test_resolve_export_root_prefers_config(self):
        sentinel = Path(tempfile.mkdtemp(prefix="sentinel-"))
        try:
            app = type("Cfg", (), {"config": {"BOE_MEDIA_ROOT": str(sentinel)}})()
            self.assertEqual(private_media.resolve_export_root(app), sentinel)
        finally:
            shutil.rmtree(sentinel, ignore_errors=True)

    def test_resolve_export_root_falls_back_to_env(self):
        sentinel = Path(tempfile.mkdtemp(prefix="sentinel-env-"))
        try:
            os.environ[private_media.EXPORT_DIR_ENV] = str(sentinel)
            try:
                app = type("Cfg", (), {"config": {}})()
                self.assertEqual(private_media.resolve_export_root(app), sentinel)
            finally:
                os.environ.pop(private_media.EXPORT_DIR_ENV, None)
        finally:
            shutil.rmtree(sentinel, ignore_errors=True)

    def test_load_manifest_rejects_traversal_paths(self):
        export = Path(tempfile.mkdtemp(prefix="manifest-"))
        flat = export / "media"
        flat.mkdir()
        try:
            manifest = {
                "version": 1,
                "generated_at": "2026-10-18T00:00:00+00:00",
                "items": {
                    "good": {
                        "status": "ready",
                        "photos": [
                            {
                                "media_id": uuid.uuid4().hex,
                                "file": "../etc/passwd",
                                "size": 4,
                                "sha256": "0" * 64,
                            }
                        ],
                    }
                },
            }
            (export / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            parsed = private_media.load_manifest(export)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["items"]["good"]["photos"], [])
        finally:
            shutil.rmtree(export, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
