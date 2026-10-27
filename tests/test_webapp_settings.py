"""Settings endpoint tests — overlay validation, region semantics, source IDs."""

from __future__ import annotations

import unittest

from tests.test_webapp_auth import _WebappFixture


class SettingsTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=True)
        self.login(username="alice")

    def test_get_settings_unauthenticated_401(self):
        # Drop the active session.
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 401)

    def test_put_settings_validates_bounds(self):
        # Bad housing budget: string that cannot be parsed.
        response = self.api_call(
            "PUT",
            "/api/settings",
            json={"settings": {"housing_budget": "abc"}},
        )
        self.assertEqual(response.status_code, 400)

    def test_put_settings_validates_source_id(self):
        response = self.api_call(
            "PUT",
            "/api/settings",
            json={"settings": {"sources": {"does-not-exist": {"enabled": True}}}},
        )
        self.assertEqual(response.status_code, 400)

    def test_put_settings_validates_regions(self):
        response = self.api_call(
            "PUT",
            "/api/settings",
            json={"settings": {"regions": "Asturias"}},
        )
        self.assertEqual(response.status_code, 400)

    def test_settings_persists_overlay(self):
        response = self.api_call(
            "PUT",
            "/api/settings",
            json={
                "settings": {
                    "housing_budget": 120000,
                    "vehicle_budget": 22000,
                    "vehicle_max_mileage": 150000,
                    "preferred_regions": ["Asturias", "Galicia"],
                    "regions": ["Asturias"],
                    "timeout": 10,
                    "max_pages": 1,
                    "max_details": 4,
                    "max_documents": 1,
                    "workers": 2,
                    "max_run_seconds": 600,
                    "sources": {"boe": {"enabled": True}, "ayvens": {"enabled": False}},
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["settings"]["housing_budget"], 120000)
        self.assertEqual(body["settings"]["vehicle_budget"], 22000)
        self.assertEqual(body["settings"]["regions"], ["Asturias"])
        self.assertEqual(body["settings"]["preferred_regions"], ["Asturias", "Galicia"])
        self.assertEqual(body["settings"]["sources"]["ayvens"]["enabled"], False)

    def test_settings_preserves_canonical_artefacts(self):
        # The settings overlay must never overwrite the real
        # ``source-catalog.json`` on disk — it should live in DB only.
        with open(self.config_path, encoding="utf-8") as fh:
            before = fh.read()
        self.api_call(
            "PUT",
            "/api/settings",
            json={"settings": {"sources": {"disabled": {"enabled": True}}}},
        )
        with open(self.config_path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertEqual(before, after)

    def test_get_settings_lists_sources(self):
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        ids = [s["id"] for s in response.get_json()["sources"]]
        # Tiny catalog only — only the IDs we registered.
        self.assertIn("boe", ids)
        self.assertIn("ayvens", ids)
        self.assertIn("disabled", ids)


if __name__ == "__main__":
    unittest.main()
