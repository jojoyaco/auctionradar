"""Private workers never fall back to the bundled household configurations."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auctionradar.web import db, runner


class PrivateRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.catalog = self.root / "source-catalog.json"
        self.catalog.write_text(json.dumps({"sources": []}))
        self.profile = self.root / "search-profile.json"
        self.profile.write_text(json.dumps({"housing": {}, "vehicles": {}}))
        self.feedback = self.root / "selection-feedback.json"
        self.feedback.write_text('{"records": []}')
        self.seeds = self.root / "seeds.json"
        self.seeds.write_text("{}")
        self.environ = patch.dict(
            os.environ,
            {
                "AUCTIONRADAR_WORKSPACE_MODE": "private",
                "AUCTIONRADAR_DATA_DIR": str(self.root),
                "AUCTIONRADAR_CATALOG_PATH": str(self.catalog),
                "AUCTIONRADAR_PROFILE_PATH": str(self.profile),
                "AUCTIONRADAR_FEEDBACK_PATH": str(self.feedback),
                "AUCTIONRADAR_SEEDS_PATH": str(self.seeds),
                "AUCTIONRADAR_RUNNER_SOURCES": "boe,all-home-sources",
            },
        )
        self.environ.start()

    def tearDown(self):
        db.close_connection()
        self.environ.stop()
        self.directory.cleanup()

    def test_empty_catalog_ignores_inherited_default_sources(self):
        self.assertEqual(runner.read_enabled_sources({}), [])

    def test_disabled_source_stays_disabled(self):
        self.catalog.write_text(json.dumps({"sources": [{"id": "fixture", "enabled": True}]}))
        self.assertEqual(
            runner.read_enabled_sources({"sources": {"fixture": {"enabled": False}}}), []
        )

    def test_profile_feedback_seeds_use_effective_paths(self):
        self.assertEqual(
            runner._resolve_canonical_paths(), (self.profile, self.feedback, self.seeds)
        )

    def test_empty_timer_finishes_without_starting_cli(self):
        with patch.object(runner.subprocess, "Popen") as process:
            code = runner.main(["--timer"])
        self.assertEqual(code, 0)
        process.assert_not_called()
        row = db.fetchone("SELECT summary_json,status FROM runs")
        self.assertEqual(row["status"], "success")
        self.assertEqual(json.loads(row["summary_json"])["reason"], "no_sources_configured")

    def test_detached_cli_receives_only_workspace_configuration(self):
        self.catalog.write_text(json.dumps({"sources": [{"id": "fixture", "enabled": True}]}))
        with patch.dict(os.environ, {"AUCTIONRADAR_RUNNER_DRY_ITEMS": "[]"}):
            code = runner.main(["--timer", "--dry-run"])
        self.assertEqual(code, 0)
        row = db.fetchone("SELECT summary_json FROM runs")
        command = json.loads(row["summary_json"])["cli_command"]
        for flag, path in (("--seeds", self.seeds), ("--feedback", self.feedback)):
            self.assertEqual(command[command.index(flag) + 1], str(path))
        overlay = Path(command[command.index("--profile") + 1])
        self.assertEqual(overlay.parent, self.root)
        self.assertEqual(json.loads(overlay.read_text()), {"housing": {}, "vehicles": {}})
        catalog = Path(command[command.index("--catalog") + 1])
        self.assertEqual(catalog.parent, self.root)
        self.assertEqual(json.loads(catalog.read_text()), json.loads(self.catalog.read_text()))

    def test_settings_can_enable_a_disabled_configured_source_without_mutating_it(self):
        self.catalog.write_text(json.dumps({"sources": [{"id": "fixture", "enabled": False}]}))
        overlay = {"sources": {"fixture": {"enabled": True}}}
        path = runner.write_catalog_overlay(overlay, self.root)
        self.assertTrue(json.loads(path.read_text())["sources"][0]["enabled"])
        self.assertFalse(json.loads(self.catalog.read_text())["sources"][0]["enabled"])


if __name__ == "__main__":
    unittest.main()
