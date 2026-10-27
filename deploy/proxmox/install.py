#!/usr/bin/env python3
"""Guided PVE host installation; reuses the verified product packager/installer."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(*command):
    return subprocess.check_output(command, text=True).strip()


def storages(content):
    output = run("pvesm", "status", "--content", content, "--enabled", "1")
    result = [
        line.split()[0]
        for line in output.splitlines()[1:]
        if len(line.split()) >= 3 and line.split()[2] == "active"
    ]
    if not result:
        raise ValueError(f"No active {content} storage on this node.")
    return result


def choose(label, value, default, choices, unattended):
    if value is None and unattended:
        raise ValueError(f"{label} must be specified with --yes.")
    if value is None:
        if choices:
            print(f"{label} options: {', '.join(choices)}")
        value = input(f"{label} [{default}]: ").strip() or default
    if choices and value not in choices:
        raise ValueError(f"{label} is not one of the available options.")
    return value


def validate(args):
    if not 100 <= args.ctid <= 999999999:
        raise ValueError("CTID must be between 100 and 999999999.")
    for value in (args.storage, args.template_storage):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
            raise ValueError("Invalid storage identifier.")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", args.bridge):
        raise ValueError("Invalid bridge name.")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", args.hostname):
        raise ValueError("Hostname must be a lowercase DNS label.")
    if (
        not 1 <= args.cores <= 128
        or not 512 <= args.memory_mb <= 1048576
        or not 8 <= args.disk_gb <= 2048
    ):
        raise ValueError("Resource values are outside the supported range.")
    if args.ip == "dhcp":
        if args.gateway:
            raise ValueError("Do not set --gateway with DHCP.")
    else:
        interface = ipaddress.IPv4Interface(args.ip)
        if not args.gateway or ipaddress.IPv4Address(args.gateway) not in interface.network:
            raise ValueError("Static IPv4 requires a gateway in the same subnet.")
        gateway = ipaddress.IPv4Address(args.gateway)
        unusable = {interface.network.network_address, interface.network.broadcast_address}
        if gateway == interface.ip or (
            interface.network.prefixlen < 31 and (interface.ip in unusable or gateway in unusable)
        ):
            raise ValueError("Static IPv4 and gateway must be distinct usable host addresses.")
    if args.nameserver:
        ipaddress.ip_address(args.nameserver)


def ensure_free(ctid):
    guests = json.loads(
        run("pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json")
    )
    if not isinstance(guests, list) or any(
        not isinstance(g, dict) or "vmid" not in g for g in guests
    ):
        raise ValueError("Invalid cluster guest inventory; refusing to assume CTID is free.")
    if any(int(guest["vmid"]) == ctid for guest in guests):
        raise ValueError(f"Guest ID {ctid} already exists in this cluster (VM or CT).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install"])
    parser.add_argument("--ctid", type=int)
    parser.add_argument("--storage")
    parser.add_argument("--template-storage")
    parser.add_argument("--bridge")
    parser.add_argument("--ip", default="dhcp")
    parser.add_argument("--gateway")
    parser.add_argument("--nameserver")
    parser.add_argument("--hostname", default="auctionradar")
    parser.add_argument("--cores", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--disk-gb", type=int, default=12)
    parser.add_argument("--commit", help="source commit selected by the standalone launcher")
    parser.add_argument("--yes", action="store_true", help="use explicit inputs without prompting")
    parser.add_argument(
        "--dry-run", action="store_true", help="read-only discovery; no download or write"
    )
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise ValueError("Run as root on the Proxmox host, not inside a container.")
    for command in (
        "pveversion",
        "pvesh",
        "pvesm",
        "pveam",
        "pct",
        "ip",
        "bash",
        "sha256sum",
        "dpkg",
    ):
        if not shutil.which(command):
            raise ValueError(f"Missing Proxmox host prerequisite: {command}")
    version = run("pveversion")
    if not re.match(r"pve-manager/(?:8|9)\.", version):
        raise ValueError("This installer targets Proxmox VE 8 or 9.")
    if run("dpkg", "--print-architecture") != "amd64":
        raise ValueError("This installer currently targets amd64 Proxmox hosts.")
    unattended = args.yes or not sys.stdin.isatty()
    if unattended and any(
        v is None for v in (args.ctid, args.storage, args.template_storage, args.bridge)
    ):
        raise ValueError(
            "Without a terminal, supply --ctid, --storage, --template-storage and --bridge (see --help)."
        )
    disks = storages("rootdir")
    templates = storages("vztmpl")
    interfaces = json.loads(run("ip", "-json", "-details", "link", "show", "type", "bridge"))
    bridges = [entry["ifname"] for entry in interfaces]
    if not bridges:
        raise ValueError("No existing Linux bridge was found.")
    print("\nAuctionRadar · Proxmox VE LXC installer (project-maintained, not Community Scripts)")
    if args.ctid is None:
        suggested = run("pvesh", "get", "/cluster/nextid")
        args.ctid = int(choose("CTID", None, suggested, [], unattended))
    args.storage = choose("Container storage", args.storage, disks[0], disks, unattended)
    args.template_storage = choose(
        "Template storage", args.template_storage, templates[0], templates, unattended
    )
    args.bridge = choose(
        "Bridge", args.bridge, "vmbr0" if "vmbr0" in bridges else bridges[0], bridges, unattended
    )
    validate(args)
    ensure_free(args.ctid)
    print(f"\nCreate NEW unprivileged Debian 13 CT {args.ctid} ({args.hostname})")
    print(f"  {args.cores} cores · {args.memory_mb} MiB RAM · {args.disk_gb} GiB on {args.storage}")
    print(f"  Network: {args.ip} on {args.bridge}; template storage: {args.template_storage}")
    print(f"  Application source: {args.commit or ROOT}")
    print(
        "  Download Debian template + application dependencies; enable services only INSIDE the new CT."
    )
    print(
        "  No host package installs, firewall/NAT changes, existing guest updates or automatic CT deletion."
    )
    print("  Web access remains loopback-only until you configure HTTPS. No default accounts.")
    if args.dry_run:
        print("DRY RUN: read-only discovery complete; no downloads, files, or guests changed.")
        return 0
    # An interactive user can cancel the actual resource allocation. Unattended
    # callers must explicitly opt in; EOF can never be interpreted as approval.
    if not args.yes:
        if unattended:
            raise ValueError("Use --yes to install without a terminal, or --dry-run to inspect.")
        if (
            input("\nType install to create this new container (anything else cancels): ").strip()
            != "install"
        ):
            print("Cancelled; nothing changed.")
            return 0
    subprocess.run(["pveam", "update"], check=True)
    available = run("pveam", "available", "--section", "system")
    images = sorted(
        {
            word
            for line in available.splitlines()
            for word in line.split()
            if re.fullmatch(r"debian-13-standard_[A-Za-z0-9_.+-]+_amd64\.tar\.(?:zst|gz|xz)", word)
        }
    )
    if not images:
        raise ValueError(
            "No Debian 13 amd64 standard template available; no container was created."
        )
    image = images[-1]
    with tempfile.TemporaryDirectory(prefix="auctionradar-proxmox-") as directory:
        archive = Path(directory) / "auctionradar.tar.gz"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "deploy/product/package.py"),
                "build",
                "--output",
                str(archive),
            ],
            check=True,
        )
        import hashlib

        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        subprocess.run(["pveam", "download", args.template_storage, image], check=True)
        template = run("pvesm", "path", f"{args.template_storage}:vztmpl/{image}")
        ensure_free(args.ctid)  # Recheck after downloads; pct create also refuses existing IDs.
        command = [
            "bash",
            str(ROOT / "deploy/product/create-lxc.sh"),
            "--ctid",
            str(args.ctid),
            "--storage",
            args.storage,
            "--bridge",
            args.bridge,
            "--ip",
            args.ip,
            "--template",
            template,
            "--release",
            str(archive),
            "--sha256",
            digest,
            "--hostname",
            args.hostname,
            "--cores",
            str(args.cores),
            "--memory-mb",
            str(args.memory_mb),
            "--disk-gb",
            str(args.disk_gb),
        ]
        for option in ("gateway", "nameserver"):
            if getattr(args, option):
                command += ["--" + option, getattr(args, option)]
        subprocess.run(command, check=True)
    print(f"\nInstalled CT {args.ctid}. From the Proxmox node's root Shell, run:")
    print(f"  pct enter {args.ctid}")
    print("Then, inside the container (no guest console password is required):")
    print("  1. Configure HTTPS using /etc/auctionradar/nginx-https.conf.example")
    print("     and /etc/auctionradar/auctionradar.env (see the self-hosting guide).")
    print("  2. Run: auctionradar-create-admin admin (password entered privately).")
    print("  3. Open your HTTPS hostname; add sources and optionally configure an AI provider.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, EOFError) as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        print(
            "Any newly created CT is deliberately left intact for inspection; no rollback/deletion was attempted.",
            file=sys.stderr,
        )
        raise SystemExit(1)
