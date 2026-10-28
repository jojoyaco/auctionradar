"""Subprocess contract checks for the installed administrator helper."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_HELPER = ROOT / "deploy" / "product" / "auctionradar-create-admin"


class AdminBootstrapTests(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, "the installed helper is root-only")
    def test_installed_helper_creates_admin_with_production_configuration(self):
        with tempfile.TemporaryDirectory(prefix="auctionradar-admin-helper-") as temporary:
            root = Path(temporary)
            data = root / "data"
            secret = root / "session.key"
            credentials = root / "credential.key"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            secret.write_text("synthetic-session-key", encoding="utf-8")
            credentials.write_bytes(b"x" * 32)

            # Keep the production helper's argv/environment contract while
            # mapping its installed absolute paths to isolated test state.
            runuser = fake_bin / "runuser"
            runuser.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "args = sys.argv[1:]\n"
                "if args[:2] != ['--user', 'auctionradar'] or '--' not in args:\n"
                "    raise SystemExit('unexpected runuser invocation')\n"
                "command = args[args.index('--') + 1:]\n"
                "if not command or command[0] != 'env':\n"
                "    raise SystemExit('expected env command')\n"
                "env = os.environ.copy()\n"
                "index = 1\n"
                "while index < len(command) and '=' in command[index]:\n"
                "    key, value = command[index].split('=', 1)\n"
                "    env[key] = value\n"
                "    index += 1\n"
                "if index >= len(command) or command[index] != '/opt/auctionradar/venv/bin/python':\n"
                "    raise SystemExit('unexpected installed Python command')\n"
                "if env.get('PYTHONPATH') != '/opt/auctionradar/app':\n"
                "    raise SystemExit('missing installed application import path')\n"
                f"env['AUCTIONRADAR_DATA_DIR'] = {str(data)!r}\n"
                f"env['AUCTIONRADAR_SECRET_FILE'] = {str(secret)!r}\n"
                f"env['AUCTIONRADAR_CREDENTIALS_KEY_FILE'] = {str(credentials)!r}\n"
                f"env['PYTHONPATH'] = {str(ROOT)!r}\n"
                "os.execve(sys.executable, [sys.executable, *command[index + 1:]], env)\n",
                encoding="utf-8",
            )
            runuser.chmod(0o755)
            environment = {
                name: value
                for name, value in os.environ.items()
                if not name.startswith("AUCTIONRADAR_") and name != "PYTHONPATH"
            }
            environment["PATH"] = (
                str(fake_bin)
                + os.pathsep
                + str(Path(sys.executable).parent)
                + os.pathsep
                + environment.get("PATH", "")
            )
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                ["bash", str(ADMIN_HELPER), "validation-admin"],
                cwd=ROOT,
                env=environment,
                input="synthetic-admin-password-123\n",
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("username='validation-admin' admin=True", result.stdout)
            self.assertNotIn("synthetic-admin-password", result.stdout + result.stderr)
            self.assertTrue((data / "identity" / "identity.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
