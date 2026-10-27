"""Integration regressions for the UI, settings, and security."""

import logging
from unittest import mock

from auctionradar.web import models
from auctionradar.web.config import build_config
from auctionradar.web.security import PrivateLinkLogFilter
from auctionradar.web.urls import is_safe_url
from tests import test_webapp_share as share_cases
from tests.test_webapp_auth import _WebappFixture


class IntegratedWebTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="owner", is_admin=True)
        self.login(username="owner")

    def test_real_templates_and_static_are_mounted(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"/static/app.js", page.data)
        self.assertIn(b"csrf-token", page.data)
        self.assertIn(b"Oportunidades", page.data)
        for path in ("/static/app.js", "/static/app.css"):
            with self.client.get(path) as static:
                self.assertEqual(static.status_code, 200)
                static.get_data()
        self.assertIn("no-store", page.headers["Cache-Control"])
        self.assertNotIn("unsafe-inline", page.headers["Content-Security-Policy"])

    def test_default_settings_and_partial_updates_keep_preferences(self):
        settings = self.client.get("/api/settings").get_json()["settings"]
        self.assertIsNone(settings["housing_budget"])
        self.assertEqual(settings["preferred_regions"], ["Asturias", "Galicia"])
        self.assertGreater(settings["max_details"], 0)
        result = self.api_call("PUT", "/api/settings", json={"settings": {"vehicle_budget": 22000}})
        self.assertEqual(result.status_code, 200, result.data)
        after = result.get_json()["settings"]
        self.assertIsNone(after["housing_budget"])
        self.assertEqual(after["preferred_regions"], settings["preferred_regions"])

    def test_all_asset_filter_and_catalog_for_invited_users(self):
        self.assertEqual(self.client.get("/api/items?asset=all&status=all").status_code, 200)
        self.make_user(username="guest")
        self.login(username="guest")
        catalog = self.client.get("/api/sources")
        self.assertEqual(catalog.status_code, 200)
        self.assertIn("boe", [s["id"] for s in catalog.get_json()["sources"]])
        self.assertEqual(self.client.get("/api/settings").status_code, 403)

    def test_limits_reject_negative_fractional_nonfinite_and_excessive(self):
        for key, value in [
            ("workers", 0),
            ("workers", 1.5),
            ("max_pages", 100000),
            ("max_details", -1),
            ("max_documents", 6),
            ("timeout", "NaN"),
            ("housing_budget", -1),
            ("vehicle_budget", "Infinity"),
            ("max_run_seconds", 86400),
            ("workers", True),
        ]:
            with self.subTest(key=key, value=value):
                result = self.api_call("PUT", "/api/settings", json={"settings": {key: value}})
                self.assertEqual(result.status_code, 400, result.data)

    def test_reactivate_invited_user(self):
        target = self.make_user(username="invited")
        self.assertEqual(
            self.api_call("PATCH", f"/api/users/{target}", json={"active": False}).status_code, 200
        )
        self.assertEqual(
            self.api_call("PATCH", f"/api/users/{target}", json={"active": True}).status_code, 200
        )
        self.assertTrue(models.get_user_by_id(target)["active"])

    def test_malformed_user_requests_are_validation_errors(self):
        for payload in [
            {"username": 42, "password": "valid-password-test"},
            {"username": "new", "password": "short"},
            {"username": "new", "password": "valid-password-test", "is_admin": "yes"},
        ]:
            result = self.api_call("POST", "/api/users", json=payload)
            self.assertEqual(result.status_code, 400, result.data)

    def test_production_without_session_secret_fails_closed(self):
        with self.assertRaises(RuntimeError):
            build_config({"AUCTIONRADAR_ENV": "production"})

    def test_links_reject_credentials_control_characters_and_script_schemes(self):
        for url in (
            "https://user:password@example.org/",
            "https://example.org/a\nb",
            "javascript:alert(1)",
            "//example.org/a",
            "file:///etc/passwd",
        ):
            self.assertFalse(is_safe_url(url))

    def test_invalid_review_payload_never_causes_server_error(self):
        for status in ([], {}, 5, None):
            result = self.api_call("POST", "/api/items/unknown/review", json={"status": status})
            self.assertEqual(result.status_code, 400)

    def test_private_link_tokens_redacted_from_error_messages(self):
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            "",
            1,
            "Exception on %s [GET]",
            ("/api/public/dummy-token",),
            None,
        )
        PrivateLinkLogFilter().filter(record)
        self.assertNotIn("dummy-token", record.getMessage())
        self.assertIn("[redacted]", record.getMessage())

    def test_https_login_preserves_same_origin_referrer_for_csrf(self):
        self.logout()
        response = self.client.get("/login", base_url="https://localhost")
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")
        import re

        csrf = re.search(
            r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)
        ).group(1)
        result = self.client.post(
            "/login",
            base_url="https://localhost",
            data={"username": "owner", "password": "password1234", "csrf_token": csrf},
            headers={"Referer": "https://localhost/login"},
        )
        self.assertEqual(result.status_code, 302)

    def test_forwarded_header_does_not_bypass_login_rate_limit(self):
        self.logout()
        csrf = self._csrf_from("/login")
        for i in range(4):
            response = self.client.post(
                "/login",
                data={"username": "owner", "password": "wrong", "csrf_token": csrf},
                headers={"X-Forwarded-For": f"203.0.113.{i}"},
            )
        self.assertEqual(response.status_code, 429)

    def test_launch_failure_is_recorded_and_correct_data_dir_passed(self):
        with mock.patch(
            "auctionradar.web.views.subprocess.Popen", side_effect=OSError("simulated")
        ) as launch:
            result = self.api_call("POST", "/api/runs", json={})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(
            launch.call_args.kwargs["env"]["AUCTIONRADAR_DATA_DIR"], str(self.data_dir)
        )
        self.assertEqual(models.list_runs()[0]["status"], "error")


class PublicPresentationTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice")
        self.login(username="alice")

    _store_item = share_cases.ShareTests._store_item

    def test_quick_review_preserves_latest_server_notes_not_stale_client_notes(self):
        key = self._store_item()
        latest = "Latest team note — never replace it with a cached value"
        self.api_call(
            "POST", f"/api/items/{key}/review", json={"status": "pending", "notes": latest}
        )
        changed = self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ok", "notes": "stale cached note", "preserve_notes": True},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.get_json()["item"]["review"]["notes"], latest)
        self.assertEqual(changed.get_json()["item"]["review"]["status"], "ok")

    def test_quick_review_rejects_non_boolean_preserve_flag(self):
        key = self._store_item()
        result = self.api_call(
            "POST", f"/api/items/{key}/review", json={"status": "ko", "preserve_notes": "yes"}
        )
        self.assertEqual(result.status_code, 400)

    def test_public_payload_top_level_and_token_not_reconstructed(self):
        key = self._store_item()
        created = self.api_call("POST", f"/api/items/{key}/share", json={}).get_json()
        public = self.client.get(f"/api/public/{created['opaque']}").get_json()["item"]
        self.assertEqual(public["prices"]["asking"], 90000)
        self.assertNotIn("review", public)
        listed = self.client.get(f"/api/items/{key}").get_json()["shares"][0]
        self.assertIsNone(listed["url"])
        self.assertEqual(listed["id"], created["id"])
