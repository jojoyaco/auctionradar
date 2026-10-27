"""Public repository layout, package boundaries and shipped defaults."""

import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackageLayoutTests(unittest.TestCase):
    def test_root_has_no_loose_python_modules_or_duplicate_defaults(self):
        self.assertEqual(list(ROOT.glob("*.py")), [])
        self.assertFalse((ROOT / "webapp").exists())
        self.assertFalse((ROOT / "config").exists())
        self.assertFalse((ROOT / "deploy/product/config").exists())
        self.assertTrue((ROOT / "pyproject.toml").is_file())

    def test_connectors_have_explicit_package_boundaries(self):
        for name in (
            "auctionradar.cli",
            "auctionradar.network",
            "auctionradar.documents",
            "auctionradar.connectors.catalog",
            "auctionradar.connectors.boe.client",
            "auctionradar.connectors.boe.detail",
            "auctionradar.connectors.boe.photo_reader",
            "auctionradar.connectors.escogecasa.detail",
            "auctionradar.web",
        ):
            self.assertIsNotNone(importlib.import_module(name))

    def test_cli_and_web_share_canonical_empty_defaults(self):
        from auctionradar.cli import parser
        from auctionradar.web.config import Config

        args = parser().parse_args([])
        self.assertEqual(Path(args.catalog), Config.CATALOG_PATH)
        self.assertEqual(Path(args.profile), Config.PROFILE_PATH)
        self.assertEqual(json.loads(Path(args.catalog).read_text())["sources"], [])

    def test_public_entry_points_are_modules(self):
        for name in (
            "auctionradar.cli",
            "auctionradar.web.manage",
            "auctionradar.connectors.boe.photo_reader",
        ):
            result = subprocess.run(
                [sys.executable, "-m", name, "--help"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", result.stdout.lower())

    def test_default_data_is_outside_installed_package(self):
        with tempfile.TemporaryDirectory() as directory:
            script = (
                "from auctionradar.web.config import DEFAULT_DATA_DIR, CONFIG_DIR; "
                "print(DEFAULT_DATA_DIR); print(CONFIG_DIR)"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=directory,
                env={**__import__("os").environ, "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                check=True,
                timeout=15,
            )
            data, defaults = result.stdout.splitlines()
            self.assertEqual(Path(data), Path(directory) / "instance")
            self.assertEqual(Path(defaults), ROOT / "auctionradar/defaults")
