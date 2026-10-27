# Installing on Proxmox VE

[Back to the README](../README.md#install-on-proxmox-ve) ·
[HTTPS and first login](SELF_HOSTING.md#production-https)

## Guided installation

AuctionRadar provides its own **`install` entry point**, inspired by the simple
setup experience of Proxmox helpers. No manual package building or file transfer
is required. This is **not an official Proxmox VE Community Script**.

### Requirements

- Root access to a **Proxmox VE 8 or 9 amd64 node**. Run in **Node → Shell**,
  never inside an existing LXC or VM.
- An active storage supporting `rootdir`, another supporting `vztmpl` (one
  storage may support both), and an existing Linux bridge.
- Outbound access to GitHub, Proxmox's Debian template repository, Debian's
  package repositories, and PyPI. No token is needed for the public repository;
  unauthenticated GitHub API rate limits can interrupt bootstrap.
- Python 3 and the standard PVE tools already installed on the host. The helper
  does **not** install packages or change repositories on the PVE host.

Default container: **unprivileged Debian 13 · 2 cores · 2048 MiB RAM · 12 GiB disk
· DHCP · hostname `auctionradar`**. AI model hosting is separate and may need
substantially more resources.

### Start the wizard

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/jojoyaco/auctionradar/main/install.sh -o auctionradar-install.sh && bash auctionradar-install.sh install
```

The wizard offers a cluster-wide free guest ID and lists eligible storages and
bridges. Choose each destination and review the plan. Type `install` to create
the new container, or cancel without making changes.

It then:

1. Refreshes the template catalog and downloads a Debian 13 amd64 standard image
   to your selected template storage. That cached image remains on the host.
2. Builds a data-free product package from the selected application snapshot.
3. Rechecks the CTID against **both VMs and containers across the cluster**.
4. Calls the existing [host installer](../deploy/product/create-lxc.sh), which
   creates a new unprivileged LXC and runs the Debian installer inside it.
5. Installs Gunicorn/nginx services, creates private keys, and checks `/healthz`.
6. Prints next steps for HTTPS and administrator creation. Registration stays
   disabled; the bootstrap listener stays loopback-only.

The tool never updates an existing guest or automatically destroys a partially
created one. Failures stop the installation and leave a new CT intact for
inspection. A repeat run with that CTID fails rather than overwriting it.

### Finish setup

From the **new container's console**:

1. Follow [production HTTPS](SELF_HOSTING.md#production-https). The example is
   retained at `/etc/auctionradar/nginx-https.conf.example`; configure your domain,
   certificate, trusted hosts, and proxy deliberately. The installer does not
   change public DNS, NAT, host firewall rules, or a shared reverse proxy.
2. Run `auctionradar-create-admin admin` and enter a private password at the
   hidden prompt. No default administrator/password is installed.
3. Sign in at your HTTPS hostname and configure **Sources and access**. Configure
   an optional AI provider separately to enable prompt-based saved-search review.

Do not expose the bootstrap HTTP port or switch to development mode to bypass
HTTPS. Installation alone does not provide a LAN-accessible browser URL.

## Review and pin the source

Executing downloaded code as root trusts this repository and GitHub's HTTPS
delivery. For review, split the command into download and execution:

```sh
curl --proto '=https' --tlsv1.2 -fsSL \
  https://raw.githubusercontent.com/jojoyaco/auctionradar/main/install.sh \
  -o auctionradar-install.sh
# Inspect the launcher and deploy/proxmox/install.py before running.
bash auctionradar-install.sh install
```

The standalone launcher resolves `main` **once** to a full commit SHA, prints
it, and downloads that immutable source snapshot. It validates archive paths,
sizes, and file types before extraction; symlinks, hard links, special files,
and path traversal are rejected. It does not execute an unverified `curl` pipe.
The built product is then SHA-256 and manifest checked by the existing
installer. These integrity checks are **not a publisher signature or an
independent authenticity guarantee**.

For a reproducible installation, download the launcher from a reviewed full
commit and pass the same commit for its application source:

```sh
COMMIT=<REVIEWED_40_CHARACTER_COMMIT_SHA>
curl --proto '=https' --tlsv1.2 -fsSL \
  "https://raw.githubusercontent.com/jojoyaco/auctionradar/$COMMIT/install.sh" \
  -o auctionradar-install.sh
bash auctionradar-install.sh install --commit "$COMMIT"
```

From a complete checkout, `bash install.sh install` uses that checkout's source
instead of downloading a snapshot. Supplying `--commit` explicitly switches
back to a pinned GitHub download. The latest `main` is not a tagged release.

## Unattended install and dry-run

From a reviewed checkout, inspect an explicit plan first:

```sh
bash install.sh install \
  --ctid 200 --storage local-lvm --template-storage local \
  --bridge vmbr0 --ip dhcp --yes --dry-run
```

**Replace these example destinations with your own.** This dry-run uses only
read-only PVE discovery: no downloads, template refreshes, local writes, or guest
changes. A standalone launcher refuses `--dry-run` rather than downloading code
while claiming to be offline; use a complete local checkout for this mode.

Remove `--dry-run` to install. `--yes` requires an explicit CTID, both storage
IDs, and bridge, bypasses prompts, and authorizes creation of the new guest. With
no terminal, missing inputs fail instead of prompting or assuming approval.

Optional arguments:

| Option | Purpose |
| --- | --- |
| `--hostname NAME` | Lowercase container hostname; default `auctionradar`. |
| `--cores N`, `--memory-mb N`, `--disk-gb N` | Resource allocation. |
| `--ip IPv4/CIDR --gateway IPv4` | Static addressing; gateway must be in the subnet. |
| `--nameserver IP` | Explicit DNS server; otherwise inherits PVE defaults. |
| `--commit SHA` | Full lowercase 40-character source commit for a remote installation. |

Run `bash install.sh --help` for the complete option summary.

## Manual package installation

For environments where source downloads on the host are undesirable, the
explicit local-template/local-package path remains available:

```sh
# Build on your development machine; output must be outside the checkout.
python3 deploy/product/package.py build --output /path/outside/the/repository/auctionradar.tar.gz
```

Record the generated SHA-256 digest. Transfer the package and the files from
`deploy/product/` to the Proxmox host over a channel you control, then run:

```sh
bash deploy/product/create-lxc.sh \
  --ctid <FREE_ID> --storage <STORAGE> --bridge <BRIDGE> \
  --ip dhcp --template <LOCAL_DEBIAN_13_TEMPLATE> \
  --release <PACKAGE_PATH> --sha256 <SHA256_DIGEST> --dry-run
```

This lower-level `--dry-run` validates local inputs without any PVE command.
Repeat without `--dry-run` to install. The package includes licenses, notices,
and empty configuration, not accounts, data, or installation history.

## Validation and Community Scripts status

Automated tests cover guided planning, cancellation, unattended inputs,
cluster-wide ID conflicts, source pinning, archive safety, template selection,
installer handoff, and error handling with simulated PVE commands. Existing
packaging and extracted-runtime checks also apply. **A complete installation on
a real Proxmox host with Debian and nginx has not been validated.**

AuctionRadar is not listed in Community Helper Scripts. Their current process
requires new scripts to be tested and reviewed in **DevScripts first**. No
submission was made. The custom noncommercial license also needs review for
eligibility; do not claim upstream acceptance or endorsement.

References: [pct](https://pve.proxmox.com/pve-docs/pct.1.html),
[pveam](https://pve.proxmox.com/pve-docs/pveam.1.html),
[pvesm](https://pve.proxmox.com/pve-docs/pvesm.1.html),
[GitHub commit lookup](https://docs.github.com/en/rest/commits/commits#get-a-commit),
and [Community Scripts contribution process](https://github.com/community-scripts/ProxmoxVE/blob/main/CONTRIBUTING.md).
