# AuctionRadar

AuctionRadar is a self-hosted workspace for researching public auctions. Search
configured sources, organize findings in private workspaces, and share selected
listings through revocable, read-only links.

## What it does

- Keeps searches, sources, notes, decisions, and history private to each account.
- Supports configurable HTTPS sources, with public-destination checks and
  request limits.
- Shares an auction without exposing private notes or authenticated material.
- Can use an optional BOE P12/PFX certificate to retrieve authenticated
  photographs. It does not currently retrieve every BOE document or support
  authenticated access to other portals.
- Provides an English- and Spanish-language interface, light and dark themes,
  and a mobile-friendly layout. Auction and portal content remains in its
  original language.
- Includes packaging and installation tools for a fresh, unprivileged Debian 13
  LXC. Application code and persistent data are kept separate.

AuctionRadar does not place bids, make payments, submit deposits, or contact
sellers automatically. Auction listings and amounts may be incomplete or out of
date; the application is not investment or legal advice.

## Repository layout

```text
auctionradar/                  Application package
  cli.py                       Public command-line entry point
  assets.py                    Shared asset taxonomy
  network.py                   Outbound network access
  documents.py                 Document handling
  connectors/
    catalog.py                 Public-source discovery and extraction
    boe/
      client.py, detail.py, photo_reader.py
    escogecasa/
      detail.py
  web/                         Flask application, templates, and static files
  defaults/                    Generic, empty configuration
deploy/product/                Release packaging and LXC installation
docs/                          Operator, licensing, and verification guides
tests/                         Automated tests and fixtures
```

The default configuration is generic and empty. It contains no personal
accounts, certificates, databases, saved searches, or installation history.

## Local development

Requirements: Python 3.10 or later and curl 8.4 or later. OpenSSL is needed for
TLS tests; Poppler is optional for extracting text from public PDFs.

```sh
git clone https://github.com/jojoyaco/auctionradar.git
cd auctionradar
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt

# Keep local development isolated and bind the preview to loopback only.
export AUCTIONRADAR_WORKSPACE_MODE=private
export AUCTIONRADAR_DATA_DIR="$PWD/instance"
export AUCTIONRADAR_ENV=development
export AUCTIONRADAR_SESSION_SECURE=false

# Create a local administrator; enter its password at the hidden prompt.
.venv/bin/python -m auctionradar.web.manage create-user --username admin --admin
.venv/bin/gunicorn --bind 127.0.0.1:8000 'auctionradar.web:create_app()'
```

Open <http://127.0.0.1:8000>. The insecure-cookie setting is for this loopback
preview only; do not expose it to a network. For persistent sessions or multiple
workers, set `AUCTIONRADAR_SECRET_FILE` to a private file outside the checkout.
Production deployments must use HTTPS and a protected session-secret file. The
CLI is available with `.venv/bin/python -m auctionradar.cli --help`.

## Documentation

- [Self-hosting and security](docs/SELF_HOSTING.md)
- [Proxmox installation](docs/PROXMOX.md)
- [Verification and test limits](docs/VERIFICATION.md)
- [Architecture and development](docs/ARCHITECTURE.md)
- [Licensing policy](docs/LICENSING.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

Original AuctionRadar material is offered under the
[AuctionRadar Noncommercial License 1.0](LICENSE). The license prohibits
commercial and other profit-oriented use, including using the software to
identify auctions, investments, purchases, or resales intended to earn a return.
It is a custom, source-available license, not an OSI-approved open-source
license. Legal review is pending. See the [licensing guide](docs/LICENSING.md)
for scope and limitations. Third-party components and data retain their own
terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
