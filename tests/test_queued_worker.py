"""Real API-to-detached-runner coverage with a local, no-network CLI fixture."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import sys
import time
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from auctionradar.web import create_app, db, models, workspaces
from tests.test_webapp_auth import _WebappFixture

_FIXTURE_CLI = r'''"""Synthetic CLI used only by test_queued_worker."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--source", action="append", default=[])
    args, _unknown = parser.parse_known_args()
    args.history_dir.mkdir(parents=True, exist_ok=True)
    (args.history_dir / "queued-worker-receipt.json").write_text(json.dumps({
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "cwd": os.getcwd(),
        "module": __file__,
        "output": str(args.output),
        "catalog": str(args.catalog),
        "timeout": args.timeout,
        "sources": args.source,
        "workspace_mode": os.environ.get("AUCTIONRADAR_WORKSPACE_MODE"),
    }))
    time.sleep(0.2)
    args.output.write_text(json.dumps({
        "schema_version": "1.0.0",
        "run_id": "synthetic-queued-worker-run",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "items": [],
        "sources": [
            {"source_id": source, "status": "success", "items_count": 0}
            for source in args.source
        ],
        "summary": {
            "source_status_counts": {"success": len(args.source)}, "selection_counts": {}
        },
    }))

if __name__ == "__main__":
    main()
'''


class QueuedWorkerIntegrationTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(
            {
                "TESTING": True,
                "DATA_DIR": self.data_dir / "identity-home",
                "WORKSPACE_MODE": "private",
                "REGISTRATION_ENABLED": True,
                "SECRET_KEY": "queued-worker-synthetic-key",
                "IDENTITY_DB_PATH": self.data_dir / "central" / "identity.sqlite3",
                "WORKSPACES_DIR": self.data_dir / "workspaces",
                "TRUSTED_HOSTS": [],
                "RUNNER_DEFAULT_MAX_RUN_SECONDS": 15,
            }
        )
        self.client = self.app.test_client()
        self.alice = self._signup(self.client, "queued-worker-alice")
        self.bob_client = self.app.test_client()
        self.bob = self._signup(self.bob_client, "queued-worker-bob")
        self._add_source()
        self.fixture_root = self.data_dir / "detached-project"
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "auctionradar",
            self.fixture_root / "auctionradar",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (self.fixture_root / "auctionradar" / "cli.py").write_text(_FIXTURE_CLI, encoding="utf-8")

    def _signup(self, client, username):
        body = client.get("/register").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', body)
        self.assertIsNotNone(csrf)
        response = client.post(
            "/api/register",
            json={"username": username, "password": "synthetic-queued-worker-password"},
            headers={"X-CSRFToken": csrf.group(1)},
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return int(response.get_json()["user"]["id"])

    def _token(self, client):
        body = client.get("/").get_data(as_text=True)
        match = re.search(r'name="csrf-token" content="([^"]+)"', body)
        if not match:
            match = re.search(r'name="csrf_token" value="([^"]+)"', body)
        self.assertIsNotNone(match)
        return match.group(1)

    def _add_source(self):
        payload = {
            "id": "queued_fixture",
            "name": "Synthetic no-network source",
            "url": "https://example.org/auctions",
            "asset_types": ["housing"],
            "adapter_id": "generic_html",
            "enabled": True,
        }
        public_answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=public_answer):
            response = self.client.post(
                "/api/connections/sources",
                json=payload,
                headers={"X-CSRFToken": self._token(self.client)},
            )
        self.assertEqual(response.status_code, 201, response.get_json())

    def _base_environment(self):
        return {
            key: os.environ[key]
            for key in (
                "PATH",
                "HOME",
                "LANG",
                "LC_ALL",
                "LC_CTYPE",
                "TZ",
                "TMPDIR",
                "PYTHONDONTWRITEBYTECODE",
            )
            if key in os.environ
        }

    def _owner_dir(self):
        return workspaces.workspace_data_dir(self.alice, self.app.config["WEBAPP_CONFIG"])

    def _read_owner_run(self, run_id):
        owner = self._owner_dir()
        with self.app.app_context(), db.data_dir_context(owner):
            row = models.get_run(run_id)
            return dict(row) if row is not None else None

    def _run_status(self, run_id):
        for run in self.client.get("/api/runs").get_json()["runs"]:
            if run["run_id"] == run_id:
                return run
        return None

    @staticmethod
    def _proc_state(pid):
        """Return Linux process state, including Z for a completed zombie."""
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            text = stat_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "missing"
        _command, separator, tail = text.rpartition(")")
        if not separator:
            return "unknown"
        fields = tail.strip().split()
        return fields[0] if fields else "unknown"

    def _wait_for_process_exit(self, pid, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._proc_state(pid)
            if state in {"missing", "Z"}:
                return state
            if state == "unknown" and sys.platform != "linux":
                try:
                    os.kill(pid, 0)
                except (ProcessLookupError, PermissionError, OSError):
                    return "missing"
            time.sleep(0.025)
        return self._proc_state(pid)

    def _queue_run(self, *, environment=None):
        token = self._token(self.client)
        clean_environment = self._base_environment()
        if environment:
            clean_environment.update(environment)
        with (
            patch.dict(os.environ, clean_environment, clear=True),
            patch("auctionradar.web.config.PROJECT_ROOT", self.fixture_root),
            warnings.catch_warnings(),
        ):
            # Detached Popen objects are intentionally not waited by the web
            # request; the test observes their lifecycle through /proc instead.
            warnings.filterwarnings(
                "ignore",
                category=ResourceWarning,
                message=r"subprocess \d+ is still running",
            )
            response = self.client.post("/api/runs", json={}, headers={"X-CSRFToken": token})
        self.assertEqual(response.status_code, 202, response.get_json())
        return response.get_json()

    def test_api_launch_runs_detached_worker_with_owner_isolation(self):
        body = self._queue_run()
        run_id = body["run_id"]
        runner_pid = int(body["pid"])
        owner = self._owner_dir()
        receipt_path = owner / "runs" / "queued-worker-receipt.json"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not receipt_path.is_file():
            if self._proc_state(runner_pid) in {"missing", "Z"}:
                break
            time.sleep(0.025)

        self.assertTrue(receipt_path.is_file(), "detached worker did not invoke the local CLI")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 10
        row = None
        while time.monotonic() < deadline:
            row = self._read_owner_run(run_id)
            if row is not None and row["status"] not in {"queued", "running"}:
                break
            time.sleep(0.025)
        self.assertIsNotNone(row, "owner run row disappeared")
        self.assertIn(row["status"], {"success", "partial"}, row)
        self.assertTrue(row["finished_at"])

        artifact_path = owner / "runs" / f"{run_id}.json"
        self.assertTrue(artifact_path.is_file())
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        summary = json.loads(row["summary_json"])
        self.assertEqual(summary["artifact_path"], str(artifact_path))
        self.assertEqual(artifact["items"], [])
        self.assertEqual(receipt["parent_pid"], runner_pid)
        self.assertEqual(receipt["workspace_mode"], "private")
        self.assertEqual(receipt["cwd"], str(self.fixture_root))
        self.assertIn(str(self.fixture_root), receipt["module"])
        self.assertEqual(receipt["timeout"], 12.0)
        self.assertEqual(receipt["sources"], ["queued_fixture"])
        self.assertTrue(Path(receipt["catalog"]).is_relative_to(owner))
        self.assertTrue(Path(receipt["output"]).is_relative_to(owner))
        self.assertTrue(Path(db._db_path(owner)).is_file())
        identity_db = Path(self.app.config["WEBAPP_CONFIG"].IDENTITY_DB_PATH)
        self.assertTrue(identity_db.is_file())
        self.assertFalse(identity_db.is_relative_to(owner))
        self.assertEqual(
            identity_db,
            self.data_dir / "central" / "identity.sqlite3",
        )
        self.assertEqual(self.bob_client.get("/api/runs").get_json()["runs"], [])
        self.assertIn(self._wait_for_process_exit(runner_pid), {"missing", "Z"})

    def test_dead_detached_worker_is_finalized_by_run_status_endpoint(self):
        owner = self._owner_dir()
        run_id = "dead-queued-worker-test"
        with self.app.app_context(), db.data_dir_context(owner):
            models.create_run(run_id=run_id, created_by=self.alice, sources=["queued_fixture"])
            models.mark_run_started(run_id, pid=2**31 - 1)

        status = self._run_status(run_id)
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "error")
        self.assertTrue(status["finished_at"])
        self.assertEqual(status["error"], "runner process exited before finalizing the run")
        self.assertFalse(self.client.get("/api/runs").get_json()["running"])

    def test_worker_startup_failure_is_logged_privately_and_marked_error(self):
        body = self._queue_run(environment={"AUCTIONRADAR_RUNNER_TIMEOUT": "invalid-test-value"})
        run_id = body["run_id"]
        runner_pid = int(body["pid"])
        self.assertIn(self._wait_for_process_exit(runner_pid), {"missing", "Z"})

        status = self._run_status(run_id)
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "error")
        self.assertTrue(status["finished_at"])
        log_path = self._owner_dir() / "runs" / f"{run_id}.runner.log"
        row = self._read_owner_run(run_id)
        self.assertIsNotNone(row)
        summary = json.loads(row["summary_json"])
        self.assertEqual(summary["reason"], "runner_exited_without_finalizing")
        self.assertEqual(summary["runner_log_path"], str(log_path))
        self.assertTrue(log_path.is_file())
        self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)
        self.assertIn("ValueError", log_path.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
