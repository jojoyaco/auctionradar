"""Actual private-mode request/worker contracts, not mocked workspace routing."""

import json
import os
import re
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

from auctionradar.web import create_app, db, models, workspaces
from tests.test_webapp_auth import _WebappFixture


class ProductPipelineTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.data_dir,
                "WORKSPACE_MODE": "private",
                "REGISTRATION_ENABLED": True,
                "SECRET_KEY": "product-synthetic-secret",
                "TRUSTED_HOSTS": [],
            }
        )
        self.client = self.app.test_client()
        self.alice = self.signup(self.client, "pipeline-alice")
        self.bob_client = self.app.test_client()
        self.bob = self.signup(self.bob_client, "pipeline-bob")

    def token(self, client):
        body = client.get("/").data
        match = re.search(rb'name="csrf-token" content="([^"]+)"', body)
        if not match:
            body = client.get("/login").data
            match = re.search(rb'name="csrf_token" value="([^"]+)"', body)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def signup(self, client, name):
        body = client.get("/register").data
        token = re.search(rb'name="csrf_token" value="([^"]+)"', body).group(1).decode()
        response = client.post(
            "/api/register",
            json={"username": name, "password": "pipeline-fixture-password"},
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["user"]["id"]

    def api(self, client, method, path, body=None):
        return client.open(
            path, method=method, json=body, headers={"X-CSRFToken": self.token(client)}
        )

    def source(self, enabled=True):
        payload = {
            "id": "my_portal",
            "name": "My public portal",
            "url": "https://example.org/auctions",
            "asset_types": ["housing"],
            "adapter_id": "generic_html",
            "enabled": enabled,
        }
        record = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        with patch.object(socket, "getaddrinfo", return_value=[record]):
            response = self.api(self.client, "POST", "/api/connections/sources", payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_source_onboarding_reaches_catalog_searches_settings_and_detached_runner(self):
        self.source()
        self.assertEqual(
            self.client.get("/api/sources").get_json()["sources"][0]["id"], "my_portal"
        )
        self.assertEqual(self.bob_client.get("/api/sources").get_json()["sources"], [])
        self.assertIn(
            "my_portal", self.client.get("/api/settings").get_json()["settings"]["sources"]
        )
        with patch("auctionradar.web.views.subprocess.Popen") as child:
            child.return_value.pid = os.getpid()
            launched = self.api(self.client, "POST", "/api/runs", {})
        self.assertEqual(launched.status_code, 202, launched.get_json())
        env = child.call_args.kwargs["env"]
        own = workspaces.workspace_data_dir(self.alice, self.app.config["WEBAPP_CONFIG"])
        self.assertEqual(Path(env["AUCTIONRADAR_DATA_DIR"]), own)
        self.assertEqual(
            Path(env["AUCTIONRADAR_IDENTITY_DB_PATH"]),
            self.app.config["WEBAPP_CONFIG"].IDENTITY_DB_PATH,
        )
        self.assertTrue(
            Path(env["AUCTIONRADAR_CATALOG_PATH"]).is_relative_to(own / "config" / "generations")
        )
        self.assertEqual(Path(env["AUCTIONRADAR_HISTORY_DIR"]), own / "runs")
        self.assertEqual(
            json.loads(Path(env["AUCTIONRADAR_CATALOG_PATH"]).read_text())["sources"][0]["id"],
            "my_portal",
        )
        self.assertEqual(self.bob_client.get("/api/runs").get_json()["runs"], [])

    def test_source_generations_are_snapshots_for_an_already_launched_worker(self):
        self.source()
        own = workspaces.workspace_data_dir(self.alice, self.app.config["WEBAPP_CONFIG"])
        with self.app.app_context(), db.data_dir_context(own):
            before = workspaces.current_config()
        updated = self.api(
            self.client, "PATCH", "/api/connections/sources/my_portal", {"name": "New source name"}
        )
        self.assertEqual(updated.status_code, 200)
        with self.app.app_context(), db.data_dir_context(own):
            after = workspaces.current_config()
        self.assertNotEqual(before.CATALOG_PATH, after.CATALOG_PATH)
        self.assertEqual(
            json.loads(before.CATALOG_PATH.read_text())["sources"][0]["name"], "My public portal"
        )
        self.assertEqual(
            json.loads(after.CATALOG_PATH.read_text())["sources"][0]["name"], "New source name"
        )
        self.assertEqual(before.CATALOG_PATH.parent, before.SEEDS_PATH.parent)

    def test_empty_or_all_disabled_sources_never_trigger_fallback_or_subprocess(self):
        for configured in (False, True):
            if configured:
                self.source(enabled=False)
            with patch("auctionradar.web.views.subprocess.Popen") as child:
                response = self.api(self.client, "POST", "/api/runs", {})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()["error"], "no_sources_configured")
            child.assert_not_called()

    def seed(self, data):
        own = workspaces.workspace_data_dir(self.alice, self.app.config["WEBAPP_CONFIG"])
        with db.data_dir_context(own):
            models.upsert_item(
                item_id="same-auction",
                asset_type="housing",
                source_id="boe",
                source_ids=["boe"],
                first_seen="2026-10-26T00:00:00Z",
                last_seen="2026-10-26T00:00:00Z",
                data=data,
                listing_url="https://example.org/auction",
                raw_fingerprint="fixture",
            )
        shared = self.api(self.client, "POST", "/api/items/same-auction/share", {}).get_json()
        return "/api/public/" + shared["opaque"]

    def test_all_private_markers_are_combined_and_nested_credentials_never_shared(self):
        path = self.seed(
            {
                "title": "private title",
                "prices": {"asking": 10000},
                "location": "private location",
                "private_fields": ["title"],
                "authenticated_fields": ["location"],
                "certificate_fields": ["prices"],
                "availability": {
                    "status": "available",
                    "notes": "PRIVATE NOTE",
                    "token": "PRIVATE TOKEN",
                },
            }
        )
        payload = self.bob_client.get(path).get_json()["item"]
        for field in ("title", "prices", "location"):
            self.assertNotIn(field, payload)
            self.assertNotIn(field, payload["data"])
        self.assertNotIn("PRIVATE", json.dumps(payload))

    def test_account_deactivation_invalidates_public_shares_too(self):
        path = self.seed({"title": "Public fixture"})
        self.assertEqual(self.bob_client.get(path).status_code, 200)
        self.assertTrue(models.deactivate_user(self.alice))
        self.assertEqual(self.bob_client.get(path).status_code, 404)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_marked_photos_and_profile_based_selection_are_not_reintroduced(self):
        path = self.seed(
            {
                "title": "Public fixture",
                "private_fields": ["photos"],
                "photos": [{"url": "https://example.org/private.jpg", "caption": "PRIVATE"}],
                "selection": {"status": "excluded", "reasons": ["private personal preference"]},
            }
        )
        payload = self.bob_client.get(path).get_json()["item"]
        self.assertNotIn("PRIVATE", json.dumps(payload))
        self.assertNotIn("selection", payload)
        self.assertNotIn("selection", payload["data"])

    def test_new_account_has_no_home_budget_or_fake_schedule(self):
        settings = self.client.get("/api/settings").get_json()
        self.assertIsNone(settings["settings"]["housing_budget"])
        self.assertEqual(settings["settings"]["preferred_regions"], [])
        self.assertFalse(self.client.get("/api/me").get_json()["schedule"]["enabled"])
        preset = self.client.get("/api/searches").get_json()["presets"][0]
        self.assertNotIn("130000", preset["prompt"])
        self.assertNotIn("None EUR", preset["prompt"])


if __name__ == "__main__":
    unittest.main()
