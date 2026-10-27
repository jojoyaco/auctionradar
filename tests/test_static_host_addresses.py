"""Both installer paths validate usable host/gateway pairs without PVE access."""

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StaticHostAddressTests(unittest.TestCase):
    def test_host_script_checks_ipv4_host_boundaries_in_dry_run(self):
        cases = [
            ("192.0.2.0/24", "192.0.2.1", False),
            ("192.0.2.255/24", "192.0.2.1", False),
            ("192.0.2.2/24", "192.0.2.0", False),
            ("192.0.2.2/24", "192.0.2.255", False),
            ("192.0.2.2/24", "192.0.2.2", False),
            ("192.0.2.2/24", "192.0.2.1", True),
            ("192.0.2.0/31", "192.0.2.1", True),
            ("192.0.2.1/31", "192.0.2.0", True),
        ]
        with tempfile.TemporaryDirectory(prefix="static-ip-test-") as directory:
            root = Path(directory)
            template = root / "debian-13-template.tar.zst"
            archive = root / "product.tar.gz"
            template.write_bytes(b"synthetic dry-run template")
            archive.write_bytes(b"synthetic dry-run release")
            for ip, gateway, valid in cases:
                with self.subTest(ip=ip, gateway=gateway):
                    result = subprocess.run(
                        [
                            "bash",
                            str(ROOT / "deploy/product/create-lxc.sh"),
                            "--ctid",
                            "900001",
                            "--storage",
                            "local-lvm",
                            "--bridge",
                            "vmbr0",
                            "--ip",
                            ip,
                            "--gateway",
                            gateway,
                            "--template",
                            str(template),
                            "--release",
                            str(archive),
                            "--sha256",
                            hashlib.sha256(archive.read_bytes()).hexdigest(),
                            "--dry-run",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode == 0, valid, result.stderr)
