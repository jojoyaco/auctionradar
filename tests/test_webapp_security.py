"""Security & authentication tests for the auctionradar.web."""

from __future__ import annotations

import unittest
from unittest import mock

import auctionradar.web.models as models
import auctionradar.web.security as security
from tests.test_webapp_auth import _WebappFixture


class AuthFlowTests(_WebappFixture):
    def test_login_get_renders_csrf_token(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn('name="csrf_token"', body)
        self.assertRegex(body, r'name="csrf_token" value="[^"]+"')

    def test_login_without_csrf_is_rejected(self):
        self.make_user(username="alice")
        response = self.client.post(
            "/login",
            data={"username": "alice", "password": "password1234"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 400)

    def test_login_success_creates_session(self):
        self.make_user(username="alice")
        response = self.login(username="alice")
        self.assertEqual(response.status_code, 302)
        me = self.client.get("/api/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.get_json()["user"]["username"], "alice")

    def test_logout_clears_session(self):
        self.make_user(username="alice")
        self.login(username="alice")
        self.logout()
        me = self.client.get("/api/me")
        self.assertEqual(me.status_code, 401)

    def test_login_rate_limit_locks_after_too_many_failures(self):
        self.make_user(username="alice")
        for _ in range(3):
            self.login(username="alice", password="wrong-pw")
        response = self.login(username="alice", password="wrong-pw")
        # The 4th attempt is rate-limited and returns 429 (with the same
        # error page) because we monitor by IP rather than username.
        self.assertEqual(response.status_code, 429)
        # Even the correct password cannot bypass the lockout.
        locked = self.login(username="alice")
        self.assertEqual(locked.status_code, 429)

    def test_login_failure_logs_attempt(self):
        self.make_user(username="alice")
        self.login(username="alice", password="wrong-pw")
        row = models.db.fetchone(
            "SELECT COUNT(*) AS c FROM login_attempts WHERE username = ?",
            ("alice",),
        )
        self.assertGreaterEqual(row["c"], 1)

    def test_password_hashing_uses_scrypt(self):
        digest, _ = security.hash_password("hello-world-pass")
        self.assertIn("$", digest)  # phc-style format produced by Werkzeug
        # The default scrypt prefix in Werkzeug is ``scrypt:``.
        self.assertTrue(digest.startswith("scrypt:"))
        self.assertTrue(security.verify_password("hello-world-pass", digest))
        self.assertFalse(security.verify_password("hello-WORLD-pass", digest))

    def test_session_version_rejects_old_passwords(self):

        digest, salt = security.hash_password("first-password")
        user_id = models.create_user("alice", digest, salt, is_admin=False)
        # First login
        self.login(username="alice", password="first-password")
        self.assertEqual(self.client.get("/api/me").status_code, 200)
        # Rotate the password (bump session_version) directly in the DB
        new_digest, new_salt = security.hash_password("second-password")
        models.change_password(user_id, new_digest, new_salt)
        # Existing session becomes invalid
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_deactivated_user_cannot_login(self):
        user_id = self.make_user(username="alice", is_admin=False)
        models.deactivate_user(user_id)
        response = self.login(username="alice")
        self.assertEqual(response.status_code, 401)


class CSRFTests(_WebappFixture):
    def test_post_to_api_without_csrf_returns_400(self):
        self.make_user(username="alice", is_admin=True)
        self.login(username="alice")
        response = self.client.post(
            "/api/users", json={"username": "bob", "password": "anotherpass"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(response.get_json()["error"], ("csrf_missing", "csrf_invalid"))

    def test_post_to_api_with_csrf_succeeds(self):
        self.make_user(username="alice", is_admin=True)
        self.login(username="alice")
        # Use ``/`` to fetch the CSRF token because ``/login`` redirects
        # for already-authenticated users.
        csrf = self._csrf_from("/")
        response = self.client.post(
            "/api/users",
            json={"username": "bob", "password": "anotherpass1"},
            headers={"X-CSRFToken": csrf},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["username"], "bob")

    def test_post_with_invalid_csrf_returns_400(self):
        self.make_user(username="alice", is_admin=True)
        self.login(username="alice")
        # Rotate the secret to invalidate the token issued earlier.
        with self.client.session_transaction() as sess:
            sess["__force_csrf_invalid"] = True
        response = self.client.post(
            "/api/users",
            json={"username": "bob", "password": "anotherpass2"},
            headers={"X-CSRFToken": "this-token-was-never-issued"},
        )
        self.assertEqual(response.status_code, 400)


class SecurityHeaderTests(_WebappFixture):
    def test_security_headers_present(self):
        response = self.client.get("/login")
        for header in (
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Content-Security-Policy",
        ):
            self.assertIn(header, response.headers)

    def test_external_javascript_uri_blocked_in_template(self):
        from auctionradar.web.urls import is_safe_url

        self.assertTrue(is_safe_url("https://example.com/item"))
        self.assertTrue(is_safe_url("http://example.org/picture.jpg"))
        self.assertFalse(is_safe_url("javascript:alert(1)"))
        self.assertFalse(is_safe_url("data:text/html,<script>x</script>"))
        self.assertFalse(is_safe_url("file:///etc/passwd"))
        self.assertFalse(is_safe_url(""))

    def test_error_responses_do_not_leak_secrets(self):
        self.make_user(username="alice", is_admin=True)
        self.login(username="alice")
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        with (
            mock.patch(
                "auctionradar.web.views.models.list_users",
                side_effect=RuntimeError("TEST_PRIVATE_MARKER"),
            ),
            mock.patch.object(self.app.logger, "error"),
            mock.patch.object(self.app.logger, "exception"),
        ):
            response = self.client.get("/api/users")
        self.assertEqual(response.status_code, 500)
        body = response.get_data(as_text=True)
        self.assertNotIn("TEST_PRIVATE_MARKER", body)
        self.assertEqual(response.get_json(), {"error": "internal_error"})


class AdminGuardTests(_WebappFixture):
    def test_admin_required_blocks_non_admin_users(self):
        self.make_user(username="alice", is_admin=False)
        self.login(username="alice")
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 403)

    def test_admin_required_allows_admin(self):
        self.make_user(username="admin1", is_admin=True)
        self.login(username="admin1")
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
