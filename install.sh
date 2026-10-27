#!/usr/bin/env bash
# AuctionRadar's own installer, not a Proxmox VE Community Script.
set -Eeuo pipefail

if [[ "${1:-}" == install && ( "${2:-}" == --help || "${2:-}" == -h ) ]]; then
    set -- --help
fi
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    cat <<'HELP'
Usage: bash install.sh install [options]

Run as root in the Proxmox HOST shell (not inside an existing container).
Without options, a terminal wizard selects a new Debian 13 LXC's settings.

  --ctid ID               Unused cluster-wide guest ID
  --storage ID            Active rootdir storage
  --template-storage ID   Active vztmpl storage
  --bridge NAME           Existing Linux bridge
  --ip dhcp|IPv4/CIDR     Default: dhcp
  --gateway IPv4          Required for a static IP
  --nameserver IP         Optional DNS server
  --hostname NAME         Default: auctionradar
  --cores N              Default: 2
  --memory-mb N           Default: 2048
  --disk-gb N             Default: 12
  --commit SHA            Download a specific full 40-character commit
  --yes                   No prompts; requires CTID, both storages, and bridge
  --dry-run               Read-only PVE discovery; no downloads or guest changes

A standalone launcher resolves main ONCE to a commit, then downloads that
snapshot over HTTPS. Review remote code before running it as root.
From a checkout, the local source is used unless --commit is supplied.
HTTPS and an administrator account must be configured after installation.
HELP
    exit 0
fi
[[ "${1:-}" == install ]] || { printf 'Use: bash install.sh install (or --help).\n' >&2; exit 2; }
shift
commit=
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
    if [[ "${args[i]}" == --commit=* ]]; then
        printf 'Use --commit SHA (two separate arguments).\n' >&2
        exit 2
    fi
    if [[ "${args[i]}" == --commit ]]; then
        ((i+1 < ${#args[@]})) || { printf 'Missing --commit value.\n' >&2; exit 2; }
        commit=${args[i+1]}
        [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { printf '--commit requires a full lowercase SHA.\n' >&2; exit 2; }
    fi
done
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
if [[ -z "$commit" && -f "$script_dir/deploy/proxmox/install.py" ]]; then
    exec python3 "$script_dir/deploy/proxmox/install.py" install "$@"
fi
# An offline dry-run must not fetch even the application snapshot.
for arg in "$@"; do
    if [[ "$arg" == --dry-run ]]; then
        printf 'For a download-free dry-run, run install.sh from a reviewed checkout.\n' >&2
        exit 2
    fi
done
[[ "$EUID" -eq 0 ]] || { printf 'Run as root on the Proxmox host.\n' >&2; exit 2; }
# Bootstrap uses only Python's standard library; no packages are installed on PVE.
python3 - "${commit:-main}" "$@" 3<&0 <<'PY'
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen


def download(url, limit):
    request = Request(url, headers={"User-Agent": "AuctionRadar-installer"})
    with urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("download exceeds size limit")
    return data


try:
    ref = sys.argv[1]
    if ref == "main":
        metadata = download("https://api.github.com/repos/jojoyaco/auctionradar/commits/main", 2 * 1024 * 1024)
        ref = json.loads(metadata)["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("GitHub did not return a full commit SHA")
    print(f"AuctionRadar source commit: {ref}", flush=True)
    with tempfile.TemporaryDirectory(prefix="auctionradar-install-") as directory:
        root = Path(directory)
        archive = root / "source.tar.gz"
        archive.write_bytes(download(f"https://codeload.github.com/jojoyaco/auctionradar/tar.gz/{ref}", 32 * 1024 * 1024))
        expected = f"auctionradar-{ref}"
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if len(members) > 5000 or sum(m.size for m in members) > 128 * 1024 * 1024:
                raise ValueError("source archive exceeds extraction limits")
            seen = set()
            for member in members:
                path = PurePosixPath(member.name)
                if (path.is_absolute() or ".." in path.parts or not path.parts
                        or path.parts[0] != expected or str(path) in seen
                        or not (member.isdir() or member.isfile())):
                    raise ValueError("unsafe source archive member")
                seen.add(str(path))
            for member in members:
                target = root / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, target.open("xb") as dest:
                        dest.write(source.read())
        installer = root / expected / "deploy/proxmox/install.py"
        if not installer.is_file():
            raise ValueError("selected commit has no Proxmox installer")
        # The heredoc occupies stdin; fd 3 retains the caller's actual terminal.
        with os.fdopen(os.dup(3)) as original_stdin:
            raise SystemExit(subprocess.call([sys.executable, str(installer), "install", *sys.argv[2:], "--commit", ref], stdin=original_stdin))
except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
    print(f"Installer bootstrap failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
