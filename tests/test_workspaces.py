"""Private workspace, registration, and share-isolation contracts."""

from __future__ import annotations

import json
import re
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from auctionradar.web import create_app, db, models, searches, security, workspaces
from tests.test_webapp_auth import _WebappFixture


class PrivateWorkspaceTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.owner_id = self.make_user(
            username="home-owner", password="owner-password-123", is_admin=False
        )
        self.item_id = "shared-item-id"
        self._store_item(
            self.item_id,
            title="Home workspace listing",
            notes="owner-only review note",
            media_id="a" * 32,
            private_data=True,
        )
        self.legacy_share_token = "old-home-share-token-that-remains-opaque"
        models.create_share(self.item_id, self.legacy_share_token, self.owner_id)
        models.set_setting(
            "settings",
            {"housing": {"budget_max_all_in": 54321}, "sources": {}},
            updated_by=self.owner_id,
        )
        models.create_run(run_id="home-run", created_by=self.owner_id, sources=["boe"])
        with db.data_dir_context(self.data_dir):
            searches.save_search(
                self.owner_id,
                {"name": "Owner private search", "criteria": {"categories": ["housing"]}},
                {"boe"},
            )
        db.close_connection()
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "WORKSPACE_MODE": "private",
                "REGISTRATION_ENABLED": True,
                "LEGACY_OWNER_USERNAME": "home-owner",
                "SECRET_KEY": "test-secret",
                "LOGIN_RATELIMIT_MAX_ATTEMPTS": 3,
                "LOGIN_RATELIMIT_WINDOW_SECONDS": 60,
                "TRUSTED_HOSTS": [],
            }
        )
        self.client = self.app.test_client()
        self.login(username="home-owner", password="owner-password-123")
        self.other_client = self.app.test_client()
        self.other_id = self._register(self.other_client, "private-user", "private-password-123")
        self.other_data_dir = workspaces.workspace_data_dir(
            self.other_id, self.app.config["WEBAPP_CONFIG"]
        )

    def _csrf_token(self, client, path="/login"):
        response = client.get(path)
        body = response.data.decode("utf-8")
        match = re.search(r'name="csrf_token" value="([^"]+)"', body)
        if not match:
            match = re.search(r'name="csrf-token" content="([^"]+)"', body)
        self.assertIsNotNone(match, body)
        return match.group(1)

    def _register(self, client, username, password, **extra):
        csrf_token = self._csrf_token(client, "/register")
        response = client.post(
            "/api/register",
            json={"username": username, "password": password, **extra},
            headers={"X-CSRFToken": csrf_token},
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertFalse(payload["user"]["is_admin"])
        return int(payload["user"]["id"])

    def _store_item(self, item_id, *, title, notes="", media_id=None, private_data=False):
        data = {
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/listing/" + item_id,
            "title": title,
            "location": "Asturias",
            "prices": {"asking": 40000},
            "photos": (
                [
                    {
                        "media_id": media_id,
                        "origin": "boe_authenticated_cache",
                        "caption": "private photo",
                    }
                ]
                if media_id
                else []
            ),
        }
        if private_data:
            data.update(
                {
                    "authenticated_content": True,
                    "certificate_derived": True,
                    "description": "Certificate-derived private description",
                    "private_notes": "must not be public",
                    "documents": [
                        {"url": "https://subastas.boe.es/reg/verDocumento.php?id=private"}
                    ],
                }
            )
        models.upsert_item(
            item_id=item_id,
            asset_type="housing",
            source_id="boe",
            source_ids=["boe"],
            first_seen="2026-10-20T10:00:00+00:00",
            last_seen="2026-10-21T10:00:00+00:00",
            data=data,
            listing_url=data["url"],
            raw_fingerprint="fp-" + title,
        )
        if notes:
            models.add_review(
                item_id=item_id,
                user_id=self.owner_id if title.startswith("Home") else self.other_id,
                status="ko",
                notes=notes,
                snapshot={"title": title},
            )
        return data

    def _api(self, client, method, path, *, json_body=None):
        headers = {}
        if method.upper() != "GET":
            headers["X-CSRFToken"] = self._csrf_token(client, "/")
        return client.open(path, method=method.upper(), json=json_body, headers=headers)

    def test_existing_home_data_and_settings_remain_with_configured_owner(self):
        me = self.client.get("/api/me").get_json()["user"]
        self.assertTrue(me["is_workspace_owner"])
        self.assertEqual(me["id"], self.owner_id)

        detail = self.client.get(f"/api/items/{self.item_id}").get_json()
        self.assertEqual(detail["item"]["title"], "Home workspace listing")
        self.assertEqual(detail["history"][0]["notes"], "owner-only review note")
        settings = self.client.get("/api/settings").get_json()
        self.assertEqual(settings["settings"]["housing_budget"], 54321)
        runs = self.client.get("/api/runs").get_json()["runs"]
        self.assertEqual([run["run_id"] for run in runs], ["home-run"])
        self.assertEqual(
            self.client.get("/api/searches").get_json()["searches"][0]["name"],
            "Owner private search",
        )
        self.assertEqual(self.client.get("/api/public/" + self.legacy_share_token).status_code, 200)

    def test_same_item_id_has_independent_review_settings_sources_runs_and_searches(self):
        with db.data_dir_context(self.other_data_dir):
            self._store_item(self.item_id, title="Other account listing", notes="other-only note")
        detail = self.other_client.get(f"/api/items/{self.item_id}").get_json()
        self.assertEqual(detail["item"]["title"], "Other account listing")
        self.assertEqual([review["notes"] for review in detail["history"]], ["other-only note"])

        self.assertEqual(self.other_client.get("/api/items").get_json()["total"], 1)
        self.assertEqual(self.other_client.get("/api/searches").get_json()["searches"], [])
        self.assertEqual(self.other_client.get("/api/runs").get_json()["runs"], [])
        self.assertEqual(self.other_client.get("/api/sources").get_json()["sources"], [])
        settings = self.other_client.get("/api/settings").get_json()
        self.assertIsNone(settings["settings"]["housing_budget"])

        own_dir = self.other_data_dir
        self.assertEqual(
            json.loads((own_dir / "config" / "source-catalog.json").read_text()), {"sources": []}
        )
        self.assertFalse((own_dir / "config" / "search-profile.json").read_text().strip() == "")
        self.assertEqual(
            (self.other_client.get(f"/api/items/{self.item_id}").get_json()["history"])[0]["notes"],
            "other-only note",
        )

    def test_share_is_hash_routed_readonly_and_never_exposes_private_or_auth_data(self):
        response = self._api(self.client, "POST", f"/api/items/{self.item_id}/share", json_body={})
        self.assertEqual(response.status_code, 200)
        share = response.get_json()

        # A logged-in different user still resolves the anonymous token into
        # exactly the creator's workspace, not the same-id local listing.
        public = self.other_client.get("/api/public/" + share["opaque"])
        self.assertEqual(public.status_code, 200)
        payload = public.get_json()["item"]
        self.assertEqual(payload["title"], "Home workspace listing")
        self.assertNotIn("source_id", payload)
        self.assertNotIn("first_seen", payload)
        self.assertNotIn("last_seen", payload)
        self.assertNotIn("description", payload["data"])
        self.assertNotIn("private_notes", payload["data"])
        self.assertEqual(payload["data"]["photos"], [])
        self.assertEqual(payload["data"]["documents"], [])
        self.assertNotIn("review", payload)
        self.assertNotIn("history", payload)

        media = self.other_client.get(f"/api/public/{share['opaque']}/media/{'a' * 32}")
        self.assertEqual(media.status_code, 404)
        self.assertEqual(
            self.other_client.get(f"/api/items/{self.item_id}/media/{'a' * 32}").status_code, 404
        )

        # Share IDs are workspace-local; the other account cannot revoke a
        # creator's link even if it independently has the same numeric ID.
        with db.data_dir_context(self.other_data_dir):
            self._store_item(self.item_id, title="Other account listing", notes="other-only note")
        failed = self._api(
            self.other_client, "POST", f"/api/shares/{share['id']}/revoke", json_body={}
        )
        self.assertEqual(failed.status_code, 404)
        other_share = self._api(
            self.other_client, "POST", f"/api/items/{self.item_id}/share", json_body={}
        ).get_json()
        self.assertIsNotNone(other_share)
        self.assertEqual(self.client.get("/api/public/" + share["opaque"]).status_code, 200)

        revoked = self._api(self.client, "POST", f"/api/shares/{share['id']}/revoke", json_body={})
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.client.get("/api/public/" + share["opaque"]).status_code, 404)

    def test_explicitly_private_fields_are_not_reintroduced_in_public_payload(self):
        item_id = "private-fields-item"
        data = self._store_item(item_id, title="Private title", private_data=True)
        data["private_fields"] = ["title", "url"]
        models.upsert_item(
            item_id=item_id,
            asset_type="housing",
            source_id="boe",
            source_ids=["boe"],
            first_seen="2026-10-20T10:00:00+00:00",
            last_seen="2026-10-21T10:00:00+00:00",
            data=data,
            listing_url=data["url"],
            raw_fingerprint="fp-private-title",
        )
        share = models.create_share(item_id, "private-fields-share-token", self.owner_id)
        self.assertGreater(share, 0)

        payload = self.client.get("/api/public/private-fields-share-token").get_json()["item"]
        self.assertNotIn("title", payload)
        self.assertNotIn("url", payload)
        self.assertNotIn("title", payload["data"])
        self.assertNotIn("url", payload["data"])

    def test_owner_can_manage_private_settings_but_not_other_users_from_workspace(self):
        response = self._api(
            self.other_client,
            "PUT",
            "/api/settings",
            json_body={"settings": {"housing_budget": 88000, "sources": {}}},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(
            self.other_client.get("/api/settings").get_json()["settings"]["housing_budget"], 88000
        )
        self.assertEqual(self.other_client.get("/api/users").status_code, 403)

    def test_global_admin_auth_does_not_select_or_read_another_workspace(self):
        digest, salt = security.hash_password("administrator-password-123")
        admin_id = models.create_user("global-admin", digest, salt, is_admin=True)
        admin_client = self.app.test_client()
        token = self._csrf_token(admin_client, "/login")
        login = admin_client.post(
            "/login",
            data={
                "username": "global-admin",
                "password": "administrator-password-123",
                "csrf_token": token,
            },
        )
        self.assertEqual(login.status_code, 302)
        self.assertEqual(admin_client.get("/api/users").status_code, 200)
        self.assertEqual(admin_client.get(f"/api/items/{self.item_id}").status_code, 404)
        self.assertNotEqual(admin_id, self.owner_id)

    def test_workspace_config_paths_are_effective_and_not_mutated_globally(self):
        original = self.app.config["WEBAPP_CONFIG"]
        with self.app.app_context():
            with db.data_dir_context(self.other_data_dir):
                effective = workspaces.current_config()
                self.assertEqual(effective.DATA_DIR, self.other_data_dir)
                self.assertEqual(
                    effective.CATALOG_PATH, self.other_data_dir / "config" / "source-catalog.json"
                )
                self.assertEqual(
                    effective.PROFILE_PATH, self.other_data_dir / "config" / "search-profile.json"
                )
                self.assertEqual(
                    effective.SEEDS_PATH, self.other_data_dir / "config" / "seeds.json"
                )
        self.assertEqual(original.DATA_DIR, self.data_dir)
        self.assertNotEqual(
            original.CATALOG_PATH, self.other_data_dir / "config" / "source-catalog.json"
        )


class RegistrationTests(_WebappFixture):
    def test_production_registration_requires_secure_cookie_and_https(self):
        with self.assertRaises(RuntimeError):
            self._private_app(ENVIRONMENT="production", SESSION_COOKIE_SECURE=False)
        client = self._private_app(ENVIRONMENT="production", SESSION_COOKIE_SECURE=True)
        self.assertEqual(client.get("/register").status_code, 426)
        self.assertEqual(client.get("/login").status_code, 426)
        self.assertEqual(client.get("/healthz").status_code, 200)
        response = client.get("/register", base_url="https://localhost")
        self.assertEqual(response.status_code, 200)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
        signup = client.post(
            "/api/register",
            base_url="https://localhost",
            json={"username": "secure-fixture", "password": "secure-fixture-password"},
            headers={"X-CSRFToken": token, "Referer": "https://localhost/register"},
        )
        self.assertEqual(signup.status_code, 201, signup.get_json())

    def _private_app(self, **overrides):
        config = {
            "TESTING": True,
            "DATA_DIR": str(self.data_dir),
            "WORKSPACE_MODE": "private",
            "REGISTRATION_ENABLED": True,
            "SECRET_KEY": "registration-secret",
            "TRUSTED_HOSTS": [],
            **overrides,
        }
        self.app = create_app(config)
        self.client = self.app.test_client()
        return self.client

    def _token(self, client, path="/register"):
        body = client.get(path).data.decode("utf-8")
        match = re.search(r'name="csrf_token" value="([^"]+)"', body)
        self.assertIsNotNone(match, body)
        return match.group(1)

    def test_registration_disabled_by_default_and_legacy_mode_cannot_enable_it(self):
        self.assertEqual(self.client.get("/register").status_code, 404)
        self.assertEqual(self.client.post("/register", data={}).status_code, 400)
        with self.assertRaises(RuntimeError):
            from auctionradar.web.config import build_config

            build_config(
                {
                    "AUCTIONRADAR_WORKSPACE_MODE": "legacy",
                    "AUCTIONRADAR_REGISTRATION_ENABLED": "true",
                }
            )

    def test_registration_requires_csrf_and_never_grants_admin(self):
        client = self._private_app()
        self.assertEqual(client.get("/register").status_code, 200)
        missing = client.post(
            "/api/register",
            json={"username": "new-account", "password": "long-registration-password"},
        )
        self.assertEqual(missing.status_code, 400)

        token = self._token(client)
        response = client.post(
            "/api/register",
            json={
                "username": "new-account",
                "password": "long-registration-password",
                "is_admin": True,
            },
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        payload = response.get_json()["user"]
        self.assertFalse(payload["is_admin"])
        stored = models.get_user_by_id(payload["id"])
        self.assertFalse(stored["is_admin"])
        workspace = workspaces.workspace_data_dir(payload["id"], self.app.config["WEBAPP_CONFIG"])
        self.assertEqual(
            json.loads((workspace / "config" / "source-catalog.json").read_text()), {"sources": []}
        )

    def test_duplicate_signup_is_neutral_case_insensitive_and_race_safe(self):
        client = self._private_app()
        csrf_token = self._token(client)
        first = client.post(
            "/api/register",
            json={"username": "MiXeD", "password": "long-registration-password"},
            headers={"X-CSRFToken": csrf_token},
        )
        self.assertEqual(first.status_code, 201)

        duplicate_client = self.app.test_client()
        duplicate_token = self._token(duplicate_client)
        duplicate = duplicate_client.post(
            "/api/register",
            json={"username": "mixed", "password": "another-registration-pass"},
            headers={"X-CSRFToken": duplicate_token},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.get_json(), {"error": "registration_unavailable"})
        self.assertNotIn("username_taken", duplicate.get_data(as_text=True))

        # Two simultaneous inserts rely on SQLite's case-insensitive UNIQUE
        # constraint, not a check-then-insert race.
        clients = [self.app.test_client(), self.app.test_client()]
        tokens = [self._token(c) for c in clients]
        gate = threading.Barrier(2)

        def submit(index):
            gate.wait(timeout=5)
            return (
                clients[index]
                .post(
                    "/api/register",
                    json={"username": "racing-account", "password": "another-registration-pass"},
                    headers={"X-CSRFToken": tokens[index]},
                )
                .status_code
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(submit, (0, 1)))
        self.assertEqual(statuses, [201, 409])

    def test_registration_rate_limit_is_independent_of_login_limiter(self):
        client = self._private_app(
            LOGIN_RATELIMIT_MAX_ATTEMPTS=1,
            REGISTRATION_RATELIMIT_MAX_ATTEMPTS=1,
            REGISTRATION_RATELIMIT_WINDOW_SECONDS=3600,
        )
        login_token = self._token(client, "/login")
        failed = client.post(
            "/login",
            data={"username": "missing", "password": "incorrect", "csrf_token": login_token},
        )
        self.assertEqual(failed.status_code, 401)

        signup_client = self.app.test_client()
        signup_token = self._token(signup_client)
        signup = signup_client.post(
            "/api/register",
            json={"username": "first-signup", "password": "long-registration-password"},
            headers={"X-CSRFToken": signup_token},
        )
        self.assertEqual(signup.status_code, 201)

        second_client = self.app.test_client()
        second_token = self._token(second_client)
        throttled = second_client.post(
            "/api/register",
            json={"username": "second-signup", "password": "long-registration-password"},
            headers={"X-CSRFToken": second_token},
        )
        self.assertEqual(throttled.status_code, 429)
        with db.identity_context() as conn:
            attempts = conn.execute("SELECT COUNT(*) FROM registration_attempts").fetchone()[0]
        self.assertEqual(attempts, 1)


class DatabaseContextTests(_WebappFixture):
    def test_simultaneous_workspace_contexts_do_not_cross_route_and_restore(self):
        first = self.data_dir / "context-a"
        second = self.data_dir / "context-b"
        db.init_db(first)
        db.init_db(second)
        barrier = threading.Barrier(2)

        def write_setting(data_dir, key, value):
            with db.data_dir_context(data_dir) as conn:
                self.assertEqual(db.current_data_dir(), data_dir.resolve())
                conn.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)",
                    (key, json.dumps(value), "2026-10-26T00:00:00+00:00"),
                )
                barrier.wait(timeout=5)
                self.assertEqual(db.current_data_dir(), data_dir.resolve())
                return conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[
                    "value"
                ]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: write_setting(*args),
                    [(first, "only-a", "A"), (second, "only-b", "B")],
                )
            )
        self.assertEqual(results, ['"A"', '"B"'])
        self.assertEqual(db.current_data_dir(), second.resolve())
        with db.data_dir_context(first):
            self.assertIsNotNone(db.fetchone("SELECT 1 FROM settings WHERE key='only-a'"))
            self.assertIsNone(db.fetchone("SELECT 1 FROM settings WHERE key='only-b'"))


if __name__ == "__main__":
    unittest.main()
