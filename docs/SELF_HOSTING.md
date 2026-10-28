# Self-hosting and security

## Start here: Proxmox VE

For a new self-hosted instance, use the **guided Proxmox VE installer** from the
**node's Shell as root**, not from inside an existing guest:

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/jojoyaco/auctionradar/main/install.sh -o auctionradar-install.sh && bash auctionradar-install.sh install
```

Select the CTID, storage, template storage, and bridge. The script creates a new,
unprivileged Debian 13 LXC and installs the application; no manual archive build
or transfer is needed. It does not install packages on the Proxmox host, modify
existing guests, or expose the web service. Defaults are 2 cores, 2048 MiB RAM,
12 GiB disk, and DHCP. A local or remote AI provider is configured separately;
these resources are not a sizing recommendation for running an LLM in the CT.

The installer is a **project-maintained preview**, not a Community Helper
Scripts entry. A real installation has been verified on amd64 PVE 9.2.21 with
Debian 13, including loopback-only HTTPS and synthetic-account isolation. PVE 8,
the standalone launcher's remote-download path, public/LAN access, and live AI
inference remain unverified. Read the [Proxmox guide](PROXMOX.md) for
requirements, source trust, unattended inputs, download-free dry-runs, and the
manual package alternative.

**Installation is not the same as enabling browser access.** After it finishes:
from the Proxmox node's root Shell, run `pct enter <CTID>` (using the chosen ID).
This enters a guest root shell without a guest login/password. Configure
[production HTTPS](#production-https), create an administrator inside the CT with
`auctionradar-create-admin admin`, sign in at your HTTPS
hostname, and add your sources. There are no default credentials.

## Isolation and data layout

Install AuctionRadar in a dedicated instance. Do not copy accounts, certificates,
or catalogs from another installation. Packaged installations use private
workspaces by default and disable registration until an administrator enables
it. For source development, explicitly set
`AUCTIONRADAR_WORKSPACE_MODE=private` as shown in the [README](../README.md).

The packaged layout separates application code, configuration, and persistent
data:

- Read-only application: `/opt/auctionradar/app`
- Python environment: `/opt/auctionradar/venv`
- Persistent data: `/var/lib/auctionradar`
- Account identity database: `identity/identity.sqlite3` under the data directory
- Per-account workspaces: `workspaces/<id>/`, containing that account's database,
  sources, history, and files
- Production environment file: `/etc/auctionradar/auctionradar.env`
- Session secret: the private file named by `AUCTIONRADAR_SECRET_FILE`
- Credential-encryption key: `AUCTIONRADAR_CREDENTIALS_KEY_FILE`, containing
  32 random, unencoded bytes; keep it outside the code, owned by the service
  account, with mode `0600`

New source catalogs start empty. Saving a source does not query it; add sources
under **Sources and access**, enable them, and run a search manually. The generic
HTML connector does not provide authentication or arbitrary JavaScript browsing.

## Production HTTPS

The installer binds Gunicorn to `127.0.0.1:8000` and configures a diagnostic
virtual host on `127.0.0.1:8080`. It does not open LAN or Internet access or
configure DNS, NAT, or certificates. In private production mode, the application
rejects HTTP except for `/healthz` and static assets. Use HTTPS for login,
registration, and certificate uploads.

1. Provide a domain and TLS certificate. Review and adapt
   `/etc/auctionradar/nginx-https.conf.example` (also available in the checkout
   at `deploy/product/nginx-https.conf.example`).
2. Set `AUCTIONRADAR_TRUSTED_HOSTS` to the exact domain and keep
   `AUCTIONRADAR_SESSION_SECURE=true`.
3. Keep Gunicorn bound to loopback. Set
   `AUCTIONRADAR_TRUST_LOCAL_PROXY=true` only when the local reverse proxy
   overwrites the scheme header. This does not trust arbitrary host or client-IP
   headers.
4. Validate the nginx configuration with `nginx -t`, enable the HTTPS virtual
   host, and disable the diagnostic virtual host.
5. Create an administrator from the console with
   `auctionradar-create-admin USERNAME`.
6. Only after verifying HTTPS, set `AUCTIONRADAR_REGISTRATION_ENABLED=true` if
   external sign-ups are required, then restart `auctionradar-web.service`.

There are no default accounts or passwords. Do not log URLs containing share
tokens. The nginx example and Gunicorn logger are configured to avoid logging
them. Keep the operating system and dependencies up to date.

## Optional BOE certificate

Uploading a P12/PFX requires explicit consent, a matching and currently valid
certificate and key, and HTTPS in production. The credential is encrypted with
AES-GCM and associated with the account workspace. The API never returns the
credential or its password.

The current reader retrieves authenticated BOE photographs, not every BOE
document or content from other portals. It does not place bids, make payments,
or change an account. A post-search refresh is limited to ten properties and
120 seconds.

Uploading a certificate gives the server access to a signing identity. Encryption
does not protect it from root, the host administrator, or a compromised
application that can use the decryption key. Use only an instance you trust.
Disabling or deleting a credential prevents later use by the application; it
does not delete photographs already retrieved or revoke the certificate with its
issuer. Moving workspace paths requires a planned decrypt-and-re-encrypt
operation, not a simple rename.

## Sharing and operational limits

Anonymous auction links are opaque, revocable, and read-only. They exclude
private notes, decisions, accounts, sources, and authenticated material.
Disabling the link creator invalidates their links. An authenticated recipient
keeps their own workspace after opening a shared link.

Anonymous pages load images only from the application's own origin and the
reviewed HTTPS origins `ngb-repositorio.urbaniza.com` and `subastas.boe.es`.
Other custom-source image origins remain available to authenticated owners but
are omitted from shared payloads. The share-page Content Security Policy also
blocks redirects to unapproved image origins, including recipient-local
services. New anonymous image origins require a deliberate allowlist review;
do not replace this policy with `http:`/`https:` wildcards. Direct portal links
are still subject to their source's access conditions and rights.

Source HTTPS requests require curl 8.4 or later. DNS answers and redirects are
checked and the connection IP is pinned; outbound network filtering is
recommended as an additional defense. The default global limits are two
concurrent search jobs and one concurrent evaluation job. Set
`AUCTIONRADAR_MAX_SEARCH_JOBS` and `AUCTIONRADAR_MAX_EVALUATION_JOBS` to values
from 1 to 8 to change them. The installer does not enable scheduled jobs.

## Backups and updates

The installer is for new installations and rejects existing target paths. It
does not provide automatic upgrades, database deletion, or rollback. Back up
`/var/lib/auctionradar` and `/etc/auctionradar` together, consistently and with
encryption. For SQLite, use `sqlite3.Connection.backup()` or a consistent
container backup; copying only the main database file while its WAL is active
is not sufficient.

Preserve the session and credential-encryption keys: replacing the session
secret invalidates sessions, and losing the credential key may make stored
certificates unrecoverable. Never add backups to the repository or restore them
from an application release package.
