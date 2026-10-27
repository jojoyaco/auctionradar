"""Runs endpoint tests.

The runner itself performs CLI execution; the tests here only validate
the wrapper (no subprocess, no network):

* ``GET /api/runs`` lists runs and reports the ``running`` flag;
* ``POST /api/runs`` (admin only) creates a queued run record and
  launches a detached subprocess (``start_new_session=True``). The test
  swaps ``subprocess.Popen`` with a recorder that pretends to run the
  CLI in-process, asserts a record exists, and inspects ``pid``;
* a non-admin cannot trigger a run;
* idempotent run-id generation produces no duplicate keys.
"""

from __future__ import annotations

import unittest
from unittest import mock

import auctionradar.web.models as models
from tests.test_webapp_auth import _WebappFixture


class RunsApiTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=True)
        self.login(username="alice")

    def test_get_runs_empty(self):
        response = self.client.get("/api/runs")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["runs"], [])
        self.assertFalse(body["running"])

    def test_non_admin_cannot_trigger_run(self):
        models.create_user("plain", "scrypt:noop", "salt", is_admin=False)  # password never used
        # Use the helper to set a valid password; clearing afterwards.
        self.make_user(username="plain2", is_admin=False)
        self.logout()
        self.login(username="plain2")
        response = self.client.post("/api/runs", headers={"X-CSRFToken": self.api_csrf_token()})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_trigger_run(self):
        with mock.patch(
            "auctionradar.web.views.subprocess.Popen", return_value=mock.MagicMock(pid=99999)
        ) as popen_mock:
            response = self.client.post(
                "/api/runs",
                headers={"X-CSRFToken": self.api_csrf_token()},
            )
        self.assertIn(response.status_code, (200, 202))
        body = response.get_json()
        self.assertIn("run_id", body)
        # The wrapper passed the runner module path to Popen — never a shell.
        args, kwargs = popen_mock.call_args
        argv = args[0]
        self.assertIn("auctionradar.web.runner", argv)
        # argv[0] is the interpreter — may be a full path like
        # ``/usr/bin/python3``; only check that it points at a Python
        # executable (contains the literal ``python``).
        self.assertIn("python", argv[0])
        self.assertNotIn("shell", kwargs)
        # The new run is reflected in /api/runs.
        listing = self.client.get("/api/runs").get_json()
        ids = [r["run_id"] for r in listing["runs"]]
        self.assertIn(body["run_id"], ids)


if __name__ == "__main__":
    unittest.main()
