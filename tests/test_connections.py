"""Private source and certificate API tests; all data and identities are synthetic."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import unittest
from unittest.mock import patch

import auctionradar.network as public_network
import auctionradar.web.connections as connections
import auctionradar.web.db as db
from auctionradar.web import create_app, credential_store, models, workspace_boe, workspaces
from tests.test_credential_store import make_synthetic_pkcs12
from tests.test_webapp_auth import _WebappFixture


class ConnectionsTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        db.close_connection()
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "WORKSPACE_MODE": "private",
                "REGISTRATION_ENABLED": True,
                "SECRET_KEY": "test-secret",
                "TRUSTED_HOSTS": [],
            }
        )
        if "connections" not in self.app.blueprints:
            self.app.register_blueprint(connections.bp)
        self.client = self.app.test_client()
        self.key_file = self.data_dir / "outside-repo-credential-key.bin"
        self.key_file.write_bytes(os.urandom(32))
        self.key_file.chmod(0o600)
        self.key_env_patch = patch.dict(
            os.environ, {credential_store.KEY_ENV_NAME: str(self.key_file)}
        )
        self.key_env_patch.start()
        self.passphrase = b"synthetic-test-pass"
        self.p12 = make_synthetic_pkcs12(self.data_dir, self.passphrase)

    def tearDown(self):
        self.key_env_patch.stop()
        super().tearDown()

    def _workspace_path(self, user_id):
        """Use the configured per-account fixture path, not DB routing mocks."""
        return workspaces.workspace_data_dir(user_id, self.app.config["WEBAPP_CONFIG"])

    def _login_client(self, client, username: str):
        response = client.get("/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data.decode("utf-8", "replace"))
        token = match.group(1).decode()
        return client.post(
            "/login",
            data={"username": username, "password": "password1234", "csrf_token": token},
        )

    def _token(self, client):
        response = client.get("/")
        match = re.search(rb'name="csrf-token" content="([^"]+)"', response.data)
        if match:
            return match.group(1).decode()
        response = client.get("/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def _api(self, client, method, path, *, payload=None, headers=None):
        request_headers = dict(headers or {})
        if method.upper() not in {"GET", "HEAD"}:
            request_headers.setdefault("X-CSRFToken", self._token(client))
        return client.open(path, method=method, json=payload, headers=request_headers)

    def _public_resolution(self, url, **_kwargs):
        if url.startswith("https://127.0.0.1"):
            raise public_network.PublicNetworkError("non_public_destination")
        return url, "auctions.example", ["93.184.216.34"]

    def _source_payload(self, **overrides):
        return {
            "id": "my_portal",
            "name": "My Portal",
            "url": "https://auctions.example/list",
            "asset_types": ["housing", "vehicles"],
            "enabled": False,
            "adapter_id": "generic_html",
            **overrides,
        }

    def test_login_required_private_page_and_csrf(self):
        self.assertEqual(self.client.get("/api/connections/sources").status_code, 401)
        self.make_user(username="alice")
        self.login(username="alice")
        self.assertEqual(self.client.get("/connections").status_code, 200)
        response = self.client.post("/api/connections/sources", json=self._source_payload())
        self.assertEqual(response.status_code, 400)
        self.app.config["WEBAPP_CONFIG"].WORKSPACE_MODE = "home"
        self.assertIn(self.client.get("/connections").status_code, {303, 404})

    def test_connections_page_uses_csp_compatible_external_styles_and_photo_copy(self):
        self.make_user(username="alice")
        self.login(username="alice")
        response = self.client.get("/connections")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/static/connections.css"', page)
        self.assertNotIn("<style", page.lower())
        self.assertIn("style-src 'self'", response.headers.get("Content-Security-Policy", ""))
        self.assertIn("authenticated BOE asset photos", page)
        self.assertIn('value="machinery"', page)
        self.assertNotIn('value="unknown"', page)

    def test_source_crud_uses_empty_private_catalog_and_atomic_generation_paths(self):
        alice_id = self.make_user(username="alice")
        self.login(username="alice")
        with patch.object(public_network, "resolve_public", side_effect=self._public_resolution):
            before = self.client.get("/api/connections/sources").get_json()
            self.assertEqual(before["sources"], [])
            created = self._api(
                self.client, "POST", "/api/connections/sources", payload=self._source_payload()
            )
        self.assertEqual(created.status_code, 201, created.get_json())
        self.assertFalse(created.get_json()["source"]["enabled"])
        self.assertEqual(created.get_json()["source"]["url"], "https://auctions.example/list")
        created_revision = created.get_json()["revision"]
        root = self._workspace_path(alice_id)
        paths = connections.workspace_source_paths(root)
        self.assertTrue(paths["CATALOG_PATH"].is_file())
        self.assertTrue(paths["SEEDS_PATH"].is_file())
        catalog = json.loads(paths["CATALOG_PATH"].read_text())
        seeds = json.loads(paths["SEEDS_PATH"].read_text())
        self.assertEqual(catalog["revision"], seeds["_revision"])
        self.assertEqual(catalog["revision"], created_revision)
        self.assertEqual(seeds["my_portal"], [])
        self.assertEqual(paths["CATALOG_PATH"].stat().st_mode & 0o777, 0o600)

        updated = self._api(
            self.client,
            "PATCH",
            "/api/connections/sources/my_portal",
            payload={"name": "Updated Portal", "enabled": True},
        )
        self.assertEqual(updated.status_code, 200, updated.get_json())
        self.assertTrue(updated.get_json()["source"]["enabled"])
        self.assertNotEqual(updated.get_json()["revision"], created_revision)
        removed = self._api(self.client, "DELETE", "/api/connections/sources/my_portal")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/connections/sources").get_json()["sources"], [])

    def test_source_validation_rejects_secrets_private_addresses_and_unknown_fields(self):
        self.make_user(username="alice")
        self.login(username="alice")
        bad_inputs = [
            self._source_payload(url="http://auctions.example/list"),
            self._source_payload(url="https://user:password@auctions.example/list"),
            self._source_payload(url="https://auctions.example/list?access_token=secret"),
            self._source_payload(url="https://127.0.0.1/private"),
            self._source_payload(password="do-not-store"),
            self._source_payload(adapter_id="browser_login"),
        ]
        with patch.object(public_network, "resolve_public", side_effect=self._public_resolution):
            for payload in bad_inputs:
                with self.subTest(payload=payload):
                    response = self._api(
                        self.client, "POST", "/api/connections/sources", payload=payload
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertNotIn("do-not-store", response.get_data(as_text=True))
        with patch.object(
            public_network,
            "resolve_public",
            side_effect=public_network.PublicNetworkError("public_dns_timeout"),
        ):
            response = self._api(
                self.client, "POST", "/api/connections/sources", payload=self._source_payload()
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "source_dns_unavailable"})

        with patch.object(public_network, "resolve_public", side_effect=self._public_resolution):
            unknown_type = self._api(
                self.client,
                "POST",
                "/api/connections/sources",
                payload=self._source_payload(asset_types=["unknown"]),
            )
        self.assertEqual(unknown_type.status_code, 400)

    def test_source_save_uses_shared_five_second_dns_policy(self):
        self.make_user(username="alice")
        self.login(username="alice")
        with patch.object(
            public_network, "resolve_public", side_effect=self._public_resolution
        ) as resolver:
            response = self._api(
                self.client, "POST", "/api/connections/sources", payload=self._source_payload()
            )
        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(resolver.call_args.kwargs["timeout"], 5.0)

    def test_sources_are_isolated_between_accounts(self):
        alice_id = self.make_user(username="alice")
        bob_id = self.make_user(username="bob")
        self.login(username="alice")
        with patch.object(public_network, "resolve_public", side_effect=self._public_resolution):
            created = self._api(
                self.client, "POST", "/api/connections/sources", payload=self._source_payload()
            )
        self.assertEqual(created.status_code, 201)
        alice_workspace = self._workspace_path(alice_id)

        bob_client = self.app.test_client()
        self.assertEqual(self._login_client(bob_client, "bob").status_code, 302)
        self.assertEqual(bob_client.get("/api/connections/sources").get_json()["sources"], [])
        forbidden_delete = self._api(bob_client, "DELETE", "/api/connections/sources/my_portal")
        self.assertEqual(forbidden_delete.status_code, 404)
        self.assertEqual(len(self.client.get("/api/connections/sources").get_json()["sources"]), 1)
        bob_workspace = self._workspace_path(bob_id)
        self.assertNotEqual(alice_workspace, bob_workspace)
        self.assertNotEqual(
            alice_workspace / "config" / "current", bob_workspace / "config" / "current"
        )

    def test_source_fetch_uses_shared_transport_and_adapter_evidence_path(self):
        response = {
            "http_status": 200,
            "content_type": "text/html; charset=utf-8",
            "final_url": "https://auctions.example/list",
            "body_bytes": b"<html><title>synthetic public listing</title></html>",
            "error": None,
            "truncated": False,
        }
        with patch.object(public_network, "fetch_public", return_value=response) as transport:
            result = connections.safe_source_fetch("https://auctions.example/list", {"timeout": 11})
        self.assertEqual(result["http_status"], 200)
        self.assertIn("synthetic public listing", result["body"])
        self.assertEqual(result["url"], "https://auctions.example/list")
        transport.assert_called_once()
        self.assertEqual(transport.call_args.args[0], "https://auctions.example/list")
        self.assertEqual(transport.call_args.kwargs["timeout"], 11)

    def test_private_fetch_rejects_sensitive_json_fields_before_connecting(self):
        with patch.object(public_network, "fetch_public") as opened:
            result = connections.safe_source_fetch(
                "https://auctions.example/search",
                {"request_json": {"signature": "synthetic-secret"}},
            )
        self.assertEqual(result["error"], "request_rejected")
        opened.assert_not_called()

    def test_production_certificate_operations_require_https_and_secure_cookies(self):
        self.make_user(username="alice")
        self.login(username="alice")
        token = self._token(self.client)
        self.app.config["WEBAPP_CONFIG"].ENVIRONMENT = "production"

        insecure_status = self.client.get("/api/connections/certificate")
        self.assertEqual(insecure_status.status_code, 426)
        self.assertEqual(insecure_status.get_json(), {"error": "https_required"})

        secure_url_insecure_cookie = self.client.get(
            "/api/connections/certificate", base_url="https://localhost"
        )
        self.assertEqual(secure_url_insecure_cookie.status_code, 503)
        self.assertEqual(secure_url_insecure_cookie.get_json(), {"error": "secure_cookie_required"})

        self.app.config["SESSION_COOKIE_SECURE"] = True
        secure_status = self.client.get(
            "/api/connections/certificate", base_url="https://localhost"
        )
        self.assertEqual(secure_status.status_code, 200)
        self.assertFalse(secure_status.get_json()["configured"])

        upload_data = {
            "certificate": (io.BytesIO(self.p12), "synthetic.p12"),
            "passphrase": self.passphrase.decode(),
            "consent": "yes",
        }
        plain_upload = self.client.post(
            "/api/connections/certificate",
            data={
                "certificate": (io.BytesIO(self.p12), "synthetic.p12"),
                "passphrase": self.passphrase.decode(),
                "consent": "yes",
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(plain_upload.status_code, 426)
        self.assertEqual(plain_upload.get_json(), {"error": "https_required"})
        secure_upload = self.client.post(
            "/api/connections/certificate",
            data=upload_data,
            headers={"X-CSRFToken": token, "Referer": "https://localhost/"},
            base_url="https://localhost",
        )
        self.assertEqual(secure_upload.status_code, 201, secure_upload.get_json())

    def test_certificate_upload_status_disable_replace_and_delete(self):
        alice_id = self.make_user(username="alice")
        self.login(username="alice")
        csrf = self._token(self.client)
        upload = self.client.post(
            "/api/connections/certificate",
            data={
                "certificate": (io.BytesIO(self.p12), "synthetic.p12"),
                "passphrase": self.passphrase.decode(),
                "consent": "yes",
            },
            headers={"X-CSRFToken": csrf},
        )
        self.assertEqual(upload.status_code, 201, upload.get_json())
        body = upload.get_json()
        self.assertEqual(set(body), {"configured", "enabled", "expires_at"})
        self.assertNotIn(self.passphrase.decode(), upload.get_data(as_text=True))
        self.assertNotIn("p12", upload.get_data(as_text=True))
        root = self._workspace_path(alice_id)
        credential_file = root / "credentials" / credential_store.ENVELOPE_NAME
        encrypted_before = credential_file.read_bytes()
        self.assertNotIn(self.p12, encrypted_before)
        self.assertNotIn(self.passphrase, encrypted_before)
        self.assertEqual(credential_file.stat().st_mode & 0o777, 0o600)

        status = self.client.get("/api/connections/certificate").get_json()
        self.assertTrue(status["configured"])
        self.assertTrue(status["enabled"])
        self.assertNotIn("passphrase", json.dumps(status))
        disabled = self._api(
            self.client,
            "PATCH",
            "/api/connections/certificate",
            payload={"enabled": False, "consent": False},
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.get_json()["enabled"])
        denied_reenable = self._api(
            self.client,
            "PATCH",
            "/api/connections/certificate",
            payload={"enabled": True, "consent": False},
        )
        self.assertEqual(denied_reenable.status_code, 400)
        reenabled = self._api(
            self.client,
            "PATCH",
            "/api/connections/certificate",
            payload={"enabled": True, "consent": True},
        )
        self.assertEqual(reenabled.status_code, 200)

        replacement = self.client.post(
            "/api/connections/certificate",
            data={
                "certificate": (io.BytesIO(self.p12), "replacement.pfx"),
                "passphrase": self.passphrase.decode(),
                "consent": "yes",
            },
            headers={"X-CSRFToken": self._token(self.client)},
        )
        self.assertEqual(replacement.status_code, 201)
        self.assertNotEqual(credential_file.read_bytes(), encrypted_before)

        removed = self._api(self.client, "DELETE", "/api/connections/certificate")
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(credential_file.exists())
        self.assertFalse(self.client.get("/api/connections/certificate").get_json()["configured"])
        self.assertEqual(self.client.get("/api/connections/certificate/file").status_code, 404)

    def test_invalid_p12_and_wrong_passphrase_are_generic_and_not_stored(self):
        alice_id = self.make_user(username="alice")
        self.login(username="alice")
        for blob, password in ((b"not a p12", "secret-passphrase"), (self.p12, "wrong-passphrase")):
            response = self.client.post(
                "/api/connections/certificate",
                data={
                    "certificate": (io.BytesIO(blob), "bad.p12"),
                    "passphrase": password,
                    "consent": "yes",
                },
                headers={"X-CSRFToken": self._token(self.client)},
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json(), {"error": "invalid_certificate"})
            self.assertNotIn(password, response.get_data(as_text=True))
        self.assertFalse((self._workspace_path(alice_id) / "credentials").exists())

    def test_certificate_ownership_and_private_photo_export_are_workspace_scoped(self):
        alice_id = self.make_user(username="alice")
        bob_id = self.make_user(username="bob")
        self.login(username="alice")
        upload = self.client.post(
            "/api/connections/certificate",
            data={
                "certificate": (io.BytesIO(self.p12), "synthetic.p12"),
                "passphrase": self.passphrase.decode(),
                "consent": "yes",
            },
            headers={"X-CSRFToken": self._token(self.client)},
        )
        self.assertEqual(upload.status_code, 201)
        alice_workspace = self._workspace_path(alice_id)
        client = workspace_boe.create_boe_client(alice_workspace)
        self.assertIsInstance(
            client, __import__("auctionradar.connectors.boe.client", fromlist=["*"]).BoeClient
        )

        class FakeBoeClient:
            def login(self):
                return True

            def iter_asset_photos(self, _url, max_photos=50):
                self.assert_max = max_photos
                yield {
                    "data": b"\xff\xd8\xff" + b"synthetic-photo-data",
                    "content_type": "image/jpeg",
                    "caption": "synthetic test photo",
                    "source_url": "https://subastas.boe.es/reg/verDocumento.php?idSub=SUB-AT-2026-TEST&idDoc=DOC-SYNTHETIC",
                }

        results = workspace_boe.collect_workspace_photos(
            alice_workspace,
            [
                {
                    "item_id": "synthetic-lot",
                    "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-TEST",
                }
            ],
            client_factory=lambda _root: FakeBoeClient(),
        )
        self.assertEqual(results["photos"], 1)
        export = alice_workspace / "private-media" / "boe-authenticated"
        self.assertTrue((export / "manifest.json").is_file())
        self.assertEqual((export / "manifest.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((export / "media").stat().st_mode & 0o777, 0o700)

        bob_workspace = self._workspace_path(bob_id)
        bob_workspace.mkdir(mode=0o700, parents=True)
        # Copying ciphertext to a different workspace cannot give that account access.
        (bob_workspace / "credentials").mkdir(mode=0o700)
        copied = bob_workspace / "credentials" / credential_store.ENVELOPE_NAME
        shutil.copyfile(alice_workspace / "credentials" / credential_store.ENVELOPE_NAME, copied)
        copied.chmod(0o600)
        with self.assertRaises(workspace_boe.WorkspaceBoeError):
            workspace_boe.create_boe_client(bob_workspace)
        self.assertFalse((bob_workspace / "private-media").exists())

    def test_refresh_hook_is_bounded_and_imports_only_the_active_private_workspace(self):
        alice_id = self.make_user(username="alice")
        bob_id = self.make_user(username="bob")
        self.login(username="alice")
        upload = self.client.post(
            "/api/connections/certificate",
            data={
                "certificate": (io.BytesIO(self.p12), "synthetic.p12"),
                "passphrase": self.passphrase.decode(),
                "consent": "yes",
            },
            headers={"X-CSRFToken": self._token(self.client)},
        )
        self.assertEqual(upload.status_code, 201, upload.get_json())

        alice_workspace = self._workspace_path(alice_id)
        bob_workspace = self._workspace_path(bob_id)
        listing_url = "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-TEST&ver=3"

        def store_same_id(root, title):
            with db.data_dir_context(root):
                models.upsert_item(
                    item_id="same-private-item-id",
                    asset_type="housing",
                    source_id="boe",
                    source_ids=["boe"],
                    first_seen="2026-10-20T10:00:00+00:00",
                    last_seen="2026-10-21T10:00:00+00:00",
                    data={"title": title, "url": listing_url, "photos": []},
                    listing_url=listing_url,
                    raw_fingerprint="fp-" + title,
                )

        store_same_id(alice_workspace, "Alice private BOE listing")
        store_same_id(bob_workspace, "Bob private BOE listing")

        with db.data_dir_context(bob_workspace):
            mismatch = workspace_boe.refresh_if_configured(alice_workspace)
        self.assertEqual(mismatch["skipped"], "workspace_mismatch")

        class FakeBoeClient:
            def login(self):
                return True

            def iter_asset_photos(self, source_url, max_photos=50):
                self.max_photos = max_photos
                yield {
                    "data": b"\xff\xd8\xffsynthetic-private-photo",
                    "content_type": "image/jpeg",
                    "caption": "synthetic photo",
                    "source_url": source_url,
                }

        with (
            db.data_dir_context(alice_workspace),
            patch.object(
                workspace_boe, "create_boe_client", return_value=FakeBoeClient()
            ) as client_factory,
        ):
            refreshed = workspace_boe.refresh_if_configured(alice_workspace)
        self.assertTrue(refreshed["ok"], refreshed)
        self.assertEqual(refreshed["requested"], 1)
        self.assertEqual(refreshed["photos"], 1)
        self.assertEqual(refreshed["updated"], 1)
        self.assertLessEqual(refreshed["requested"], workspace_boe.MAX_REFRESH_ITEMS)
        client_factory.assert_called_once()

        with db.data_dir_context(alice_workspace):
            alice_item = models.get_item("same-private-item-id")
            alice_data = json.loads(alice_item["data_json"])
        with db.data_dir_context(bob_workspace):
            bob_item = models.get_item("same-private-item-id")
            bob_data = json.loads(bob_item["data_json"])
        self.assertEqual(alice_data["photos"][0]["origin"], "boe_authenticated_cache")
        self.assertEqual(bob_data["photos"], [])
        self.assertFalse((self.data_dir / "boe-photo-refresh.request").exists())
        self.assertEqual(
            list((alice_workspace / "private-media" / "boe-authenticated").glob(".refresh-*")), []
        )


if __name__ == "__main__":
    unittest.main()
