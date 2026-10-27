# Installing on Proxmox VE

The scripts in `deploy/product/` create a **new, unprivileged Debian 13 LXC**
from a local template and release package. They are not an official
Helper-Scripts installer. They do not download code from a moving branch or
modify other containers, routers, shared proxies, or network exposure rules.

## Build a release package

Run the package builder from the repository:

```sh
python3 deploy/product/package.py build --output /path/outside/the/repository/auctionradar.tar.gz
```

Record and verify the generated SHA-256 digest. Transfer the package and the
required files from `deploy/product/` to the Proxmox host over a channel you
control. The installer validates the package manifest, hashes, paths, and file
types, and rejects links and special files. The package includes the project
license, third-party notices, and generic empty configuration; it excludes
accounts, personal settings, data, and installation history. Its top-level
directory is named `auctionradar-product/`.

## Review and create the container

Provide an unused CTID, storage, bridge, local Debian 13 template, release
package, and its digest. The script does not guess these values:

```sh
bash deploy/product/create-lxc.sh \
  --ctid <FREE_ID> --storage <STORAGE> --bridge <BRIDGE> \
  --ip dhcp --template <LOCAL_DEBIAN_13_TEMPLATE> \
  --release <PACKAGE_PATH> --sha256 <SHA256_DIGEST> --dry-run
```

`--dry-run` validates local inputs without invoking `pct` or creating resources.
Review the plan, then repeat the command without `--dry-run` to install. For a
static address, provide `--ip <IPv4/CIDR> --gateway <IPv4>` on the same subnet.

If installation fails after the container is created, the container is left in
place for inspection; it is not automatically destroyed, and an existing CTID
is never reused. No initial account or password is created. The web service
listens on loopback until you configure HTTPS as described in
[the self-hosting guide](SELF_HOSTING.md). The installer does not configure
public DNS, NAT, or firewall exposure.

## Validation status

Packaging, extraction safety, shell syntax, dry-run behavior, and the extracted
runtime have automated checks. **A complete installation on a real Proxmox host
with Debian and nginx has not been validated.**

The project's noncommercial license is not open source under the OSI definition
and may prevent acceptance into Proxmox Helper-Scripts. No proposal has been
submitted; do not claim compatibility with its acceptance policy without review
by its maintainers. Consult the current contribution process before proposing
an installer.

References: [Proxmox `pct` manual](https://pve.proxmox.com/pve-docs/pct.1.html)
and [Proxmox VE Community Scripts contribution guide](https://github.com/community-scripts/ProxmoxVE/blob/main/CONTRIBUTING.md).
