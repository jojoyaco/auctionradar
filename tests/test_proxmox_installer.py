"""PVE wizard and standalone bootstrap tests; never touch real infrastructure."""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("proxmox_install", ROOT / "deploy/proxmox/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
SHA = "a" * 40


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.guests = []
        self.images = "system debian-13-standard_13.1-2_amd64.tar.zst"
        self.options = [
            "install",
            "--yes",
            "--ctid",
            "900001",
            "--storage",
            "local-lvm",
            "--template-storage",
            "local",
            "--bridge",
            "vmbr0",
        ]
        for target, kwargs in (
            ("os.geteuid", {"return_value": 0}),
            ("shutil.which", {"side_effect": lambda name: "/fake/" + name}),
            ("sys.stdin.isatty", {"return_value": False}),
            ("run", {"side_effect": self.command_output}),
            ("subprocess.run", {"side_effect": self.mutate}),
        ):
            patcher = (
                mock.patch.object(installer, target, **kwargs)
                if "." not in target
                else mock.patch("proxmox_install." + target, **kwargs)
            )
            # importlib-loaded module must be visible to mock's name resolver.
            self.addCleanup(patcher.stop)
            sys.modules["proxmox_install"] = installer
            patcher.start()
        self.addCleanup(sys.modules.pop, "proxmox_install", None)

    def command_output(self, *command):
        self.calls.append(command)
        if command == ("pveversion",):
            return "pve-manager/9.0.11/test"
        if command == ("dpkg", "--print-architecture"):
            return "amd64"
        if command[:2] == ("pvesm", "status"):
            name = "local-lvm" if "rootdir" in command else "local"
            return f"Name Type Status Total Used Available %\n{name} dir active 100 0 100 0%"
        if command[0] == "ip":
            return json.dumps([{"ifname": "vmbr0"}])
        if command[:3] == ("pvesh", "get", "/cluster/resources"):
            return json.dumps(self.guests)
        if command[:3] == ("pvesh", "get", "/cluster/nextid"):
            return "900001"
        if command[:2] == ("pveam", "available"):
            return self.images
        if command[:2] == ("pvesm", "path"):
            return "/fake/debian-13-standard.tar.zst"
        raise AssertionError(command)

    def mutate(self, command, **kwargs):
        self.calls.append(tuple(command))
        if "build" in command:
            Path(command[-1]).write_bytes(b"synthetic product")
        return subprocess.CompletedProcess(command, 0)

    def test_dry_run_only_reads_inventory_no_downloads_files_or_pct(self):
        with mock.patch.object(installer.tempfile, "TemporaryDirectory") as temporary:
            self.assertEqual(installer.main(self.options + ["--dry-run"]), 0)
        temporary.assert_not_called()
        self.assertFalse(
            any(
                c[0] == "pct"
                or c[:2] in [("pveam", "update"), ("pveam", "download")]
                or "build" in c
                for c in self.calls
            )
        )

    def test_noninteractive_missing_target_fails_without_mutation(self):
        with self.assertRaisesRegex(ValueError, "Without a terminal"):
            installer.main(["install", "--yes"])
        self.assertFalse(any(c[0] in ("pct", "pveam") for c in self.calls))

    def test_noninteractive_explicit_inputs_still_require_yes(self):
        with self.assertRaisesRegex(ValueError, "Use --yes"):
            installer.main([arg for arg in self.options if arg != "--yes"])
        self.assertFalse(any(c[0] == "pveam" for c in self.calls))

    def test_existing_vm_or_ct_anywhere_in_cluster_is_rejected(self):
        for kind in ["qemu", "lxc"]:
            self.guests = [{"vmid": 900001, "type": kind, "node": "other-node"}]
            with self.assertRaisesRegex(ValueError, "already exists"):
                installer.main(self.options)
        self.assertFalse(any(c[0] == "pveam" for c in self.calls))

    def test_malformed_inventory_fails_closed(self):
        self.guests = [{"type": "qemu"}]
        with self.assertRaisesRegex(ValueError, "Invalid cluster"):
            installer.main(self.options)

    def test_unknown_storage_and_bridge_rejected(self):
        for option, value in [("--storage", "unknown"), ("--bridge", "eth0")]:
            args = self.options.copy()
            args[args.index(option) + 1] = value
            with self.assertRaisesRegex(ValueError, "available options"):
                installer.main(args)

    def test_network_and_resource_validation_precedes_downloads(self):
        cases = [
            ["--gateway", "10.0.0.1"],
            ["--ip", "10.0.0.2/24"],
            ["--ip", "10.0.0.2/24", "--gateway", "10.1.0.1"],
            ["--hostname", "bad;command"],
            ["--cores", "0"],
            ["--disk-gb", "1"],
            ["--nameserver", "not-an-ip"],
        ]
        for extra in cases:
            with self.assertRaises(ValueError):
                installer.main(self.options + extra)
        self.assertFalse(any(c[0] == "pveam" for c in self.calls))

    def test_install_downloads_template_builds_product_then_reuses_host_installer(self):
        self.assertEqual(
            installer.main(self.options + ["--ip", "10.0.0.2/24", "--gateway", "10.0.0.1"]), 0
        )
        self.assertIn(("pveam", "update"), self.calls)
        self.assertIn(
            ("pveam", "download", "local", "debian-13-standard_13.1-2_amd64.tar.zst"), self.calls
        )
        host = next(c for c in self.calls if c[0] == "bash")
        self.assertEqual(host[1], str(ROOT / "deploy/product/create-lxc.sh"))
        self.assertEqual(host[host.index("--ctid") + 1], "900001")
        self.assertEqual(host[host.index("--gateway") + 1], "10.0.0.1")
        self.assertFalse(Path(host[host.index("--release") + 1]).exists())
        self.assertEqual(
            sum(c[:3] == ("pvesh", "get", "/cluster/resources") for c in self.calls), 2
        )

    def test_no_template_stops_before_container_creation(self):
        self.images = "system debian-12-standard_12.0-1_amd64.tar.zst"
        with self.assertRaisesRegex(ValueError, "No Debian 13"):
            installer.main(self.options)
        self.assertFalse(any(c[0] == "bash" for c in self.calls))

    def test_interactive_defaults_and_cancel_never_mutate(self):
        with (
            mock.patch.object(installer.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=["", "", "", "", "cancel"]),
        ):
            self.assertEqual(installer.main(["install"]), 0)
        self.assertFalse(any(c[0] == "pveam" for c in self.calls))

    def test_pve_failure_propagates_without_destroy_or_retry(self):
        with mock.patch.object(
            installer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pveam")
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.main(self.options)
        self.assertFalse(any("destroy" in c for c in self.calls))


class BootstrapTests(unittest.TestCase):
    def script(self):
        return (ROOT / "install.sh").read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]

    def archive(self, name=None, kind=tarfile.REGTYPE):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as tar:
            entry = tarfile.TarInfo(name or f"auctionradar-{SHA}/deploy/proxmox/install.py")
            entry.type = kind
            entry.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
            payload = b"# synthetic installer"
            entry.size = len(payload) if kind == tarfile.REGTYPE else 0
            tar.addfile(entry, io.BytesIO(payload) if entry.size else None)
        return data.getvalue()

    def bootstrap(self, archive, ref="main", failed_download=False):
        calls = []
        duplicate_fd = os.dup

        def urlopen(request, **kwargs):
            calls.append(request.full_url)
            if failed_download:
                raise OSError("synthetic network failure")
            return io.BytesIO(
                json.dumps({"sha": SHA}).encode() if "/commits/" in request.full_url else archive
            )

        def execute(command, **kwargs):
            self.assertTrue(Path(command[1]).is_file())
            self.assertIn("--yes", command)
            self.assertIsNotNone(kwargs["stdin"])
            return 0

        with (
            mock.patch("urllib.request.urlopen", side_effect=urlopen),
            mock.patch("subprocess.call", side_effect=execute) as child,
            mock.patch.object(sys, "argv", ["-", ref, "--yes"]),
            mock.patch("os.dup", side_effect=lambda _fd: duplicate_fd(0)),
            self.assertRaises(SystemExit) as result,
        ):
            exec(compile(self.script(), "install.sh bootstrap", "exec"), {})
        return result.exception.code, calls, child

    def test_main_resolved_once_then_only_immutable_snapshot_downloaded(self):
        code, urls, child = self.bootstrap(self.archive())
        self.assertEqual(code, 0)
        self.assertEqual(len(urls), 2)
        self.assertTrue(urls[1].endswith("/" + SHA))
        child.assert_called_once()

    def test_explicit_commit_skips_moving_branch_lookup(self):
        code, urls, _ = self.bootstrap(self.archive(), SHA)
        self.assertEqual(code, 0)
        self.assertEqual(urls, [f"https://codeload.github.com/jojoyaco/auctionradar/tar.gz/{SHA}"])

    def test_traversal_symlink_absolute_and_wrong_root_never_execute(self):
        for name, kind in [
            (f"auctionradar-{SHA}/../../bad", tarfile.REGTYPE),
            (f"auctionradar-{SHA}/link", tarfile.SYMTYPE),
            ("/etc/bad", tarfile.REGTYPE),
            ("wrong/root", tarfile.REGTYPE),
        ]:
            code, _, child = self.bootstrap(self.archive(name, kind))
            self.assertEqual(code, 1)
            child.assert_not_called()

    def test_network_failure_never_executes_partial_download(self):
        code, _, child = self.bootstrap(b"", failed_download=True)
        self.assertEqual(code, 1)
        child.assert_not_called()

    def test_help_and_invalid_command_do_not_contact_network(self):
        for arguments, code in [
            (["--help"], 0),
            (["update"], 2),
            (["install", "--commit", "main"], 2),
        ]:
            response = subprocess.run(
                ["bash", str(ROOT / "install.sh"), *arguments],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(response.returncode, code, response.stderr)

    def test_standalone_dry_run_refuses_to_download(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "install.sh"
            script.write_text((ROOT / "install.sh").read_text())
            response = subprocess.run(
                ["bash", str(script), "install", "--dry-run"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(response.returncode, 2)
        self.assertIn("download-free dry-run", response.stderr)

    @unittest.skipUnless(os.geteuid() == 0, "standalone launcher enforces root")
    def test_real_shell_bootstrap_preserves_original_stdin(self):
        # Run the real shell + embedded Python + downloaded child. Only the
        # HTTP transport is replaced, using Python's startup test hook.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "install.sh"
            script.write_text((ROOT / "install.sh").read_text())
            archive = root / "fixture.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                payload = b"print('CHILD_INPUT=' + input(), flush=True)\n"
                entry = tarfile.TarInfo(f"auctionradar-{SHA}/deploy/proxmox/install.py")
                entry.size = len(payload)
                tar.addfile(entry, io.BytesIO(payload))
            (root / "sitecustomize.py").write_text(
                "import io, pathlib, urllib.request\n"
                f"urllib.request.urlopen = lambda *a, **k: io.BytesIO(pathlib.Path({str(archive)!r}).read_bytes())\n"
            )
            response = subprocess.run(
                ["bash", str(script), "install", "--commit", SHA, "--yes"],
                input="original-caller-input\n",
                capture_output=True,
                text=True,
                timeout=15,
                env={**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"},
            )
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertIn("CHILD_INPUT=original-caller-input", response.stdout)


@unittest.skipUnless(os.geteuid() == 0, "host installer requires root")
class HostHandoffTests(unittest.TestCase):
    def test_real_launcher_package_builder_and_host_script_with_fake_pve_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / "bin"
            binaries.mkdir()
            template = root / "debian.tar.zst"
            template.write_bytes(b"synthetic Debian template")
            log = root / "commands.jsonl"
            fake = binaries / "fake-tool"
            fake.write_text(
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                "name=pathlib.Path(sys.argv[0]).name; args=sys.argv[1:]\n"
                f"with open({str(log)!r}, 'a') as f: f.write(json.dumps([name,*args])+'\\n')\n"
                "if name=='pveversion': print('pve-manager/9.0.11/test')\n"
                "elif name=='pvesh': print('[]')\n"
                "elif name=='pvesm' and args[0]=='status':\n"
                " print('Name Type Status Total Used Available %')\n"
                " print(('local-lvm' if 'rootdir' in args else 'local')+' dir active 100 0 100 0%')\n"
                f"elif name=='pvesm' and args[0]=='path': print({str(template)!r})\n"
                "elif name=='ip' and '-json' in args: print('[{\"ifname\":\"vmbr0\"}]')\n"
                "elif name=='pveam' and args[0]=='available': print('system debian-13-standard_13.1-2_amd64.tar.zst')\n"
                "elif name=='pct' and args[0]=='list': print('VMID Status Lock Name')\n"
            )
            fake.chmod(0o755)
            for command in ["pveversion", "pvesh", "pvesm", "pveam", "ip", "pct"]:
                (binaries / command).symlink_to(fake)
            response = subprocess.run(
                [
                    "bash",
                    str(ROOT / "install.sh"),
                    "install",
                    "--yes",
                    "--ctid",
                    "900001",
                    "--storage",
                    "local-lvm",
                    "--template-storage",
                    "local",
                    "--bridge",
                    "vmbr0",
                ],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", ""),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(response.returncode, 0, response.stderr)
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            create = next(c for c in commands if c[:2] == ["pct", "create"])
            self.assertEqual(create[2], "900001")
            self.assertEqual(create[create.index("--unprivileged") + 1], "1")
            self.assertTrue(any(c[:2] == ["pveam", "download"] for c in commands))
            self.assertTrue(
                any(
                    c[:3] == ["pct", "exec", "900001"]
                    and "/root/auctionradar-installer/install-lxc.sh" in c
                    for c in commands
                )
            )
            self.assertFalse(any("destroy" in c or "apt-get" in c for c in commands))
            self.assertIn("auctionradar-create-admin admin", response.stdout)
            self.assertIn("/etc/auctionradar/nginx-https.conf.example", response.stdout)


if __name__ == "__main__":
    unittest.main()
