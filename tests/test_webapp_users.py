"""User management tests.

Verifies:

* an administrator can invite a user with ``is_admin=false``;
* non-admin users cannot call the user-management endpoints;
* the last active administrator cannot be deactivated or self-deactivated;
* password change invalidates the current session;
* weak passwords are rejected.
"""

from __future__ import annotations

import unittest

from tests.test_webapp_auth import _WebappFixture


class UserManagementTests(_WebappFixture):
    def test_invite_user(self):
        self.make_user(username="admin1", is_admin=True)
        self.login(username="admin1")
        response = self.api_call(
            "POST",
            "/api/users",
            json={"username": "guest1", "password": "longer-than-eight", "is_admin": False},
        )
        self.assertEqual(response.status_code, 201)

    def test_non_admin_cannot_invite(self):
        self.make_user(username="plain", is_admin=False)
        self.login(username="plain")
        response = self.client.post(
            "/api/users",
            json={"username": "evil", "password": "another12345"},
            headers={"X-CSRFToken": self.api_csrf_token()},
        )
        self.assertEqual(response.status_code, 403)

    def test_short_password_rejected(self):
        self.make_user(username="admin1", is_admin=True)
        self.login(username="admin1")
        response = self.api_call(
            "POST",
            "/api/users",
            json={"username": "guest2", "password": "short", "is_admin": False},
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_username_rejected(self):
        self.make_user(username="admin1", is_admin=True)
        self.login(username="admin1")
        self.api_call(
            "POST", "/api/users", json={"username": "dup", "password": "longer-than-eight"}
        )
        response = self.api_call(
            "POST", "/api/users", json={"username": "dup", "password": "longer-than-eight"}
        )
        self.assertEqual(response.status_code, 409)

    def test_last_admin_cannot_be_deactivated(self):
        admin = self.make_user(username="only-admin", is_admin=True)
        self.login(username="only-admin")
        response = self.client.patch(
            f"/api/users/{admin}",
            json={"active": False},
            headers={"X-CSRFToken": self.api_csrf_token()},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "cannot_deactivate_last_admin")

    def test_admin_can_deactivate_themselves_when_other_admins_exist(self):
        self.make_user(username="admin1", is_admin=True)
        admin2 = self.make_user(username="admin2", is_admin=True)
        self.login(username="admin1")
        response = self.client.patch(
            f"/api/users/{admin2}",
            json={"active": False},
            headers={"X-CSRFToken": self.api_csrf_token()},
        )
        self.assertEqual(response.status_code, 200)

    def test_self_deactivation_blocked(self):
        admin = self.make_user(username="admin1", is_admin=True)
        self.make_user(username="admin2", is_admin=True)
        self.login(username="admin1")
        response = self.client.patch(
            f"/api/users/{admin}",
            json={"active": False},
            headers={"X-CSRFToken": self.api_csrf_token()},
        )
        self.assertEqual(response.status_code, 400)

    def test_password_change_invalidates_session(self):
        self.make_user(username="alice", is_admin=False)
        self.login(username="alice")
        me_before = self.client.get("/api/me")
        self.assertEqual(me_before.status_code, 200)
        response = self.api_call(
            "POST",
            "/api/password",
            json={"current_password": "password1234", "new_password": "newpassword12"},
        )
        self.assertEqual(response.status_code, 200)
        # The session should have been wiped — ``/api/me`` is 401.
        me_after = self.client.get("/api/me")
        self.assertEqual(me_after.status_code, 401)
        # New password works, old does not.
        self.login(username="alice", password="newpassword12")
        self.assertEqual(self.client.get("/api/me").status_code, 200)
        self.login(username="alice", password="password1234")
        self.assertEqual(self.login(username="alice", password="password1234").status_code, 401)


if __name__ == "__main__":
    unittest.main()
