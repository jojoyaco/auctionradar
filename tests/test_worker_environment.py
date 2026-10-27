"""Detached workers cannot inherit another workspace's state or certificate."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from auctionradar.web.worker_environment import for_workspace


class WorkerEnvironmentTests(unittest.TestCase):
    def config(self, mode):
        root = Path("/state/workspaces/test-account")
        return SimpleNamespace(
            WORKSPACE_MODE=mode,
            DATA_DIR=root,
            CATALOG_PATH=root / "config/source-catalog.json",
            PROFILE_PATH=root / "config/search-profile.json",
            FEEDBACK_PATH=root / "config/selection-feedback.json",
            SEEDS_PATH=root / "config/seeds.json",
        )

    def test_private_environment_is_explicit_and_has_no_household_overrides(self):
        original = {
            "PATH": "/usr/bin",
            "AUCTIONRADAR_HISTORY_DIR": "/household/history",
            "AUCTIONRADAR_OUTPUT_DIR": "/household/output",
            "AUCTIONRADAR_PROFILE_OVERLAY": '{"private":true}',
            "AUCTIONRADAR_BOE_EXPORT_DIR": "/household/certificate-photos",
            "CREDENTIALS_DIRECTORY": "/run/credentials/household",
            "AUCTIONRADAR_RUNNER_SOURCES": "all-household-sources",
            "AUCTIONRADAR_RUNNER_DRY_ITEMS": "[1]",
            "AUCTIONRADAR_CREDENTIALS_KEY_FILE": "/etc/radar/vault-key",
        }
        env = for_workspace(self.config("private"), environ=original)
        self.assertEqual(env["AUCTIONRADAR_DATA_DIR"], "/state/workspaces/test-account")
        self.assertEqual(env["AUCTIONRADAR_HISTORY_DIR"], "/state/workspaces/test-account/runs")
        self.assertEqual(env["AUCTIONRADAR_OUTPUT_DIR"], env["AUCTIONRADAR_HISTORY_DIR"])
        self.assertEqual(
            env["AUCTIONRADAR_BOE_EXPORT_DIR"],
            "/state/workspaces/test-account/private-media/boe-authenticated",
        )
        self.assertEqual(
            env["AUCTIONRADAR_CATALOG_PATH"],
            "/state/workspaces/test-account/config/source-catalog.json",
        )
        for key in (
            "AUCTIONRADAR_RUNNER_SOURCES",
            "AUCTIONRADAR_PROFILE_OVERLAY",
            "CREDENTIALS_DIRECTORY",
            "AUCTIONRADAR_RUNNER_DRY_ITEMS",
        ):
            self.assertNotIn(key, env)
        # The search orchestrator uses this installation's independent AEAD key
        # for its own optional BOE refresh; public scraper children never do.
        self.assertEqual(env["AUCTIONRADAR_CREDENTIALS_KEY_FILE"], "/etc/radar/vault-key")
        self.assertEqual(original["AUCTIONRADAR_HISTORY_DIR"], "/household/history")
        self.assertEqual(env["AUCTIONRADAR_REGISTRATION_ENABLED"], "false")

    def test_legacy_paths_keep_existing_operator_overrides(self):
        env = for_workspace(
            self.config("legacy"),
            environ={
                "AUCTIONRADAR_OUTPUT_DIR": "/existing/output",
                "CREDENTIALS_DIRECTORY": "/run/existing-reader",
            },
        )
        self.assertEqual(env["AUCTIONRADAR_OUTPUT_DIR"], "/existing/output")
        self.assertEqual(env["CREDENTIALS_DIRECTORY"], "/run/existing-reader")

    def test_evaluator_does_not_receive_certificate_key_reference(self):
        env = for_workspace(
            self.config("private"),
            role="evaluation",
            environ={
                "AUCTIONRADAR_CREDENTIALS_KEY_FILE": "/etc/radar/vault-key",
            },
        )
        self.assertNotIn("AUCTIONRADAR_CREDENTIALS_KEY_FILE", env)


if __name__ == "__main__":
    unittest.main()
