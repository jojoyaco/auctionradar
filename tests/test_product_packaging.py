"""Safety and reproducibility tests for the private-user product archive."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "deploy" / "product" / "package.py"
HOST_INSTALLER = ROOT / "deploy" / "product" / "create-lxc.sh"
LXC_INSTALLER = ROOT / "deploy" / "product" / "install-lxc.sh"


class ProductPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auctionradar-package-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.archive = self.work / "auctionradar-product.tar.gz"
        self.digest = self._build(self.archive)

    def _build(self, archive: Path) -> str:
        result = subprocess.run(
            [sys.executable, str(PACKAGE), "build", "--output", str(archive)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.split("sha256=", 1)[1].splitlines()[0]

    def _assert_schema_value(self, schema, value, root_schema, path="$"):
        if "$ref" in schema:
            target = root_schema
            for part in schema["$ref"].removeprefix("#/").split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            self._assert_schema_value(target, value, root_schema, path)
            return
        expected_type = schema.get("type")
        type_checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if expected_type:
            self.assertTrue(type_checks[expected_type](value), f"{path}: expected {expected_type}")
        if "const" in schema:
            self.assertEqual(value, schema["const"], f"{path}: const")
        if "enum" in schema:
            self.assertIn(value, schema["enum"], f"{path}: enum")
        if "anyOf" in schema:
            failures = []
            for option in schema["anyOf"]:
                try:
                    self._assert_schema_value(option, value, root_schema, path)
                    break
                except AssertionError as exc:
                    failures.append(str(exc))
            else:
                self.fail(f"{path}: no anyOf branch matched: {failures}")
        if isinstance(value, dict):
            missing = set(schema.get("required", [])) - set(value)
            self.assertFalse(missing, f"{path}: missing {sorted(missing)}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                self.assertFalse(set(value) - set(properties), f"{path}: unexpected keys")
            for key, item in value.items():
                if key in properties:
                    self._assert_schema_value(properties[key], item, root_schema, f"{path}.{key}")
        if isinstance(value, list):
            self.assertGreaterEqual(len(value), schema.get("minItems", 0), f"{path}: minItems")
            if schema.get("uniqueItems"):
                encoded = [json.dumps(item, sort_keys=True) for item in value]
                self.assertEqual(len(encoded), len(set(encoded)), f"{path}: duplicate items")
            if "items" in schema:
                for index, item in enumerate(value):
                    self._assert_schema_value(
                        schema["items"], item, root_schema, f"{path}[{index}]"
                    )
        if isinstance(value, str):
            self.assertGreaterEqual(len(value), schema.get("minLength", 0), f"{path}: minLength")
            if "pattern" in schema:
                self.assertRegex(value, re.compile(schema["pattern"]), f"{path}: pattern")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
            self.assertGreaterEqual(value, schema["minimum"], f"{path}: minimum")

    def test_archive_is_reproducible_and_allowlisted(self):
        second_dir = self.work / "second"
        second_dir.mkdir()
        second = second_dir / self.archive.name
        self._build(second)
        self.assertEqual(self.archive.read_bytes(), second.read_bytes())
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(), self.digest)
        self.assertEqual(
            self.archive.with_name(self.archive.name + ".sha256").read_text(),
            f"{self.digest}  {self.archive.name}\n",
        )

        with tarfile.open(self.archive, "r:gz") as tar:
            members = tar.getmembers()
            names = {member.name for member in members}
            self.assertIn("auctionradar-product/MANIFEST.sha256", names)
            self.assertIn("auctionradar-product/requirements.txt", names)
            self.assertIn("auctionradar-product/LICENSE", names)
            self.assertIn("auctionradar-product/THIRD_PARTY_NOTICES.md", names)
            self.assertIn("auctionradar-product/auctionradar/web/__init__.py", names)
            self.assertIn("auctionradar-product/auctionradar/network.py", names)
            self.assertIn("auctionradar-product/auctionradar/defaults/source-catalog.json", names)
            self.assertIn("auctionradar-product/auctionradar/defaults/search-profile.json", names)
            for forbidden in (
                ".git",
                "HISTORY.md",
                "CONTINUE_PROMPT.md",
                "docs/",
                "tests/",
                "instance/",
                "runs/",
                "raw/",
                "config/access-profile.json",
                "config/registration-status.json",
                "config/seeds-provenance.json",
            ):
                self.assertFalse(any(forbidden in name for name in names), forbidden)
            self.assertFalse(any(member.issym() or member.islnk() for member in members))
            for member in members:
                self.assertEqual(member.uid, 0)
                self.assertEqual(member.gid, 0)
                self.assertEqual(member.mtime, 0)
                if member.isfile():
                    self.assertEqual(member.mode, 0o644)
                elif member.isdir():
                    self.assertEqual(member.mode, 0o755)

            catalog = json.load(
                tar.extractfile("auctionradar-product/auctionradar/defaults/source-catalog.json")
            )
            profile = json.load(
                tar.extractfile("auctionradar-product/auctionradar/defaults/search-profile.json")
            )
            feedback = json.load(
                tar.extractfile(
                    "auctionradar-product/auctionradar/defaults/selection-feedback.json"
                )
            )
            seeds = json.load(
                tar.extractfile("auctionradar-product/auctionradar/defaults/seeds.json")
            )
            schema = json.load(
                tar.extractfile(
                    "auctionradar-product/auctionradar/defaults/source-catalog.schema.json"
                )
            )

        self.assertEqual(catalog["sources"], [])
        self.assertEqual(profile, {})
        self.assertEqual(feedback["records"], [])
        self.assertEqual(seeds, {})
        schema_text = json.dumps(schema, ensure_ascii=False)
        self.assertNotIn("searchProfile", schema["$defs"])
        self.assertNotIn("housingSearch", schema["$defs"])
        self.assertNotIn("camper_conversion", schema_text)
        self.assertNotIn("130000", schema_text)
        catalog_schema = schema["$defs"]["catalog"]
        self.assertEqual(catalog_schema["properties"]["sources"]["minItems"], 0)
        self.assertTrue(set(catalog_schema["required"]).issubset(catalog))
        self.assertTrue(set(catalog_schema["required"]).issubset(catalog_schema["properties"]))
        self._assert_schema_value(schema, catalog, schema)
        self._assert_schema_value(
            schema,
            {
                "revision": "synthetic-revision",
                "sources": [
                    {
                        "id": "my_boats",
                        "name": "My boats",
                        "url": "https://example.org/auctions",
                        "asset_types": ["boats"],
                        "enabled": False,
                        "adapter_id": "generic_html",
                    }
                ],
            },
            schema,
        )

    def test_verifier_extracts_and_checks_manifest(self):
        destination = self.work / "extract"
        result = subprocess.run(
            [
                sys.executable,
                str(PACKAGE),
                "verify",
                "--archive",
                str(self.archive),
                "--sha256",
                self.digest,
                "--destination",
                str(destination),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("verified=", result.stdout)
        self.assertEqual(
            (
                destination
                / "auctionradar-product"
                / "auctionradar"
                / "defaults"
                / "source-catalog.json"
            ).is_file(),
            True,
        )
        self.assertFalse((destination / "auctionradar-product" / "MANIFEST.sha256").exists())

    def test_verifier_rejects_path_traversal_before_extracting(self):
        archive = self.work / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            payload = b"do not write outside destination"
            member = tarfile.TarInfo("auctionradar-product/../../outside.txt")
            member.size = len(payload)
            tar.addfile(member, __import__("io").BytesIO(payload))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        destination = self.work / "bad-extract"
        result = subprocess.run(
            [
                sys.executable,
                str(PACKAGE),
                "verify",
                "--archive",
                str(archive),
                "--sha256",
                digest,
                "--destination",
                str(destination),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / "outside.txt").exists())
        self.assertFalse(destination.exists())

    def test_private_runtime_defaults_and_stdin_admin_bootstrap(self):
        product = ROOT / "deploy" / "product"
        env = (product / "auctionradar.env.example").read_text(encoding="utf-8")
        self.assertIn("AUCTIONRADAR_WORKSPACE_MODE=private", env)
        self.assertIn("AUCTIONRADAR_REGISTRATION_ENABLED=false", env)
        self.assertIn("AUCTIONRADAR_SECRET_FILE=/etc/auctionradar/session.key", env)
        self.assertIn("AUCTIONRADAR_CREDENTIALS_KEY_FILE=/etc/auctionradar/credential.key", env)
        self.assertNotIn("AUCTIONRADAR_CREDENTIAL_KEY_FILE=", env)
        self.assertIn("# AUCTIONRADAR_LEGACY_OWNER_USERNAME=", env)
        self.assertNotRegex(env, r"(?m)^AUCTIONRADAR_SECRET_KEY=")
        service = (product / "auctionradar-web.service").read_text()
        self.assertNotIn("--access-logfile", service)
        self.assertIn("--logger-class auctionradar.web.gunicorn_logging.PrivateLogger", service)
        self.assertIn("error_log /dev/null", (product / "nginx-https.conf.example").read_text())

        installer = (product / "install-lxc.sh").read_text(encoding="utf-8")
        self.assertIn("sys.stdout.buffer.write(secrets.token_bytes(32))", installer)
        self.assertIn("chown auctionradar:auctionradar /etc/auctionradar/credential.key", installer)
        self.assertIn("chmod 0600 /etc/auctionradar/credential.key", installer)
        self.assertNotIn("urlsafe_b64encode", installer)

        admin = (product / "auctionradar-create-admin").read_text(encoding="utf-8")
        self.assertIn("--password-stdin", admin)
        self.assertIn("AUCTIONRADAR_CREDENTIALS_KEY_FILE=/etc/auctionradar/credential.key", admin)
        self.assertNotRegex(admin, r"--password(?:\s|$)")
        self.assertIn('--username "$1"', admin)

        service = (product / "auctionradar-web.service").read_text(encoding="utf-8")
        self.assertIn("User=auctionradar", service)
        self.assertIn("--bind 127.0.0.1:8000", service)
        bootstrap = (product / "nginx-bootstrap.conf").read_text(encoding="utf-8")
        self.assertIn("listen 127.0.0.1:8080;", bootstrap)
        self.assertIn("access_log off;", bootstrap)
        https = (product / "nginx-https.conf.example").read_text(encoding="utf-8")
        self.assertIn("listen 443 ssl;", https)
        self.assertIn("ssl_certificate_key /etc/auctionradar/tls/privkey.pem;", https)

    def test_host_dry_run_never_calls_pct(self):
        fake_bin = self.work / "bin"
        fake_bin.mkdir()
        pct_marker = self.work / "pct-was-called"
        fake_pct = fake_bin / "pct"
        fake_pct.write_text(f"#!/bin/sh\nprintf called > {pct_marker}\n", encoding="utf-8")
        fake_pct.chmod(0o755)
        template = self.work / "debian-13-template.tar.zst"
        template.write_bytes(b"test placeholder; dry-run does not inspect template contents")
        env = dict(os.environ)
        env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            [
                "bash",
                str(HOST_INSTALLER),
                "--ctid",
                "900001",
                "--storage",
                "local-lvm",
                "--bridge",
                "vmbr0",
                "--ip",
                "dhcp",
                "--template",
                str(template),
                "--release",
                str(self.archive),
                "--sha256",
                self.digest,
                "--dry-run",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertIn("pct create", result.stdout)
        self.assertFalse(pct_marker.exists())

    def test_help_paths_do_not_mutate(self):
        for script in (HOST_INSTALLER, LXC_INSTALLER):
            result = subprocess.run(
                ["bash", str(script), "--help"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
