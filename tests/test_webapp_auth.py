"""Helpers shared by every webapp test file.

The fixture generates a throwaway data directory, calls
:func:`auctionradar.web.create_app` with a known secret and an empty catalogue,
then hands out a ``Flask.test_client``. Per-test reset wipes the SQLite
file (and its WAL/SHM companions) before opening a fresh connection.

No network is permitted during the suite. All catalogue / profile /
feedback paths are redirected to small inline JSON literals to make the
tests deterministic and independent of runtime installation state.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# ``tests/test_webapp_*.py`` need to import the package. Make sure the
# project root is on ``sys.path`` even when the user runs
# ``python -m unittest discover`` from elsewhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask
from flask.testing import FlaskClient

import auctionradar.web.db as db
import auctionradar.web.models as models
import auctionradar.web.security as security
from auctionradar.web import create_app

# A small catalog with two sources covering vehicles and housing so
# that the "sources" section of the settings UI can be exercised
# without depending on the real 65-entry catalogue.
_TINY_CATALOG = {
    "sources": [
        {
            "id": "boe",
            "name": "BOE",
            "enabled": True,
            "asset_types": ["housing", "vehicles"],
        },
        {
            "id": "ayvens",
            "name": "Ayvens",
            "enabled": True,
            "asset_types": ["vehicles"],
        },
        {
            "id": "disabled",
            "name": "Disabled Source",
            "enabled": False,
            "asset_types": ["housing"],
        },
    ]
}


_TINY_PROFILE = {
    "housing": {
        "preferred_regions": [
            {"code": "ES-AS", "name": "Asturias"},
            {"code": "ES-GA", "name": "Galicia"},
        ],
    },
    "vehicles": {},
}


class _WebappFixture(unittest.TestCase):
    """Re-usable base for the integration tests."""

    app: Flask
    client: FlaskClient
    data_dir: Path
    config_path: Path
    profile_path: Path
    feedback_path: Path
    seeds_path: Path

    def setUp(self):  # noqa: D401 — unittest API
        self.data_dir = Path(tempfile.mkdtemp(prefix="webapp-tests-"))
        self.config_path = self.data_dir / "source-catalog.json"
        self.profile_path = self.data_dir / "search-profile.json"
        self.feedback_path = self.data_dir / "selection-feedback.json"
        self.seeds_path = self.data_dir / "seeds.json"
        self.config_path.write_text(json.dumps(_TINY_CATALOG), encoding="utf-8")
        self.profile_path.write_text(json.dumps(_TINY_PROFILE), encoding="utf-8")
        self.feedback_path.write_text(json.dumps({"records": []}), encoding="utf-8")
        self.seeds_path.write_text(json.dumps({}), encoding="utf-8")
        os.environ["AUCTIONRADAR_CATALOG_PATH"] = str(self.config_path)
        os.environ["AUCTIONRADAR_PROFILE_PATH"] = str(self.profile_path)
        os.environ["AUCTIONRADAR_FEEDBACK_PATH"] = str(self.feedback_path)
        os.environ["AUCTIONRADAR_SEEDS_PATH"] = str(self.seeds_path)
        os.environ["AUCTIONRADAR_SECRET_KEY"] = "test-secret"
        os.environ.pop("AUCTIONRADAR_TRUSTED_HOSTS", None)
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": str(self.data_dir),
                "SECRET_KEY": "test-secret",
                "LOGIN_RATELIMIT_MAX_ATTEMPTS": 3,
                "LOGIN_RATELIMIT_WINDOW_SECONDS": 60,
                "TRUSTED_HOSTS": [],
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):  # noqa: D401 — unittest API
        db.close_connection()
        shutil.rmtree(self.data_dir, ignore_errors=True)
        for env in (
            "AUCTIONRADAR_CATALOG_PATH",
            "AUCTIONRADAR_PROFILE_PATH",
            "AUCTIONRADAR_FEEDBACK_PATH",
            "AUCTIONRADAR_SEEDS_PATH",
            "AUCTIONRADAR_SECRET_KEY",
        ):
            os.environ.pop(env, None)

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def make_user(self, *, username: str, password: str = "password1234", is_admin: bool = False):
        digest, salt = security.hash_password(password)
        return models.create_user(username, digest, salt, is_admin=is_admin)

    def login(self, *, username: str, password: str = "password1234"):
        # If we are already authenticated the GET /login would redirect;
        # use ``/`` instead (which always embeds the CSRF token).
        with self.client.session_transaction() as sess:
            already_logged = bool(sess.get("_user_id"))
        if already_logged:
            # Drop the session, then fetch /login.
            self.client.post("/logout", data={"csrf_token": self.api_csrf_token()})
        csrf = self._csrf_from("/login")
        response = self.client.post(
            "/login",
            data={"username": username, "password": password, "csrf_token": csrf},
            follow_redirects=False,
        )
        return response

    def logout(self):
        csrf = self._csrf_from("/")
        return self.client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)

    def _csrf_from(self, path: str) -> str:
        response = self.client.get(path)
        body = response.data.decode("utf-8")
        import re

        match = re.search(r'name="csrf_token" value="([^"]+)"', body)
        if not match:
            match = re.search(r'name="csrf-token" content="([^"]+)"', body)
        if not match:
            self.fail(f"login page did not embed a CSRF token: {body!r}")
        return match.group(1)

    def api_csrf_token(self) -> str:
        """Fetch a CSRF token that belongs to the current session.

        Tries ``/`` (which works regardless of auth state — it redirects
        to ``/login`` when no user is logged in) and falls back to
        ``/login`` when the user is unauthenticated.
        """
        response = self.client.get("/")
        body = response.data.decode("utf-8")
        import re

        match = re.search(r'name="csrf-token" content="([^"]+)"', body)
        if match:
            return match.group(1)
        return self._csrf_from("/login")

    def api_call(self, method, path, *, json=None, status=200, csrf_header=True):
        headers = {}
        if csrf_header and method.upper() != "GET":
            headers["X-CSRFToken"] = self.api_csrf_token()
        return self.client.open(
            path,
            method=method.upper(),
            json=json,
            headers=headers,
        )
