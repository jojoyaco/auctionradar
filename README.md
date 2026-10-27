<div align="center">

<img src="auctionradar/web/static/favicon.svg" width="76" height="76" alt="AuctionRadar logo">

# AuctionRadar

**Find the listing. Let AI check the fit. You make the final call.**

A self-hosted workspace to discover public listings and review matches with AI,<br>
using your own plain-language prompt, criteria, and published listing evidence.

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Self-hosted](https://img.shields.io/badge/Deployment-Self--hosted-2F7D4E?style=flat-square)
[![Noncommercial license](https://img.shields.io/badge/License-Noncommercial-14233A?style=flat-square)](LICENSE)

[AI review](#your-search-your-ai-reviewer) · [Demo](#see-it-in-action) · [Installation](#installation) · [Documentation](#documentation)

</div>

---

## Your search, your AI reviewer

**Finding a listing is only the first step. Tell AuctionRadar what you want
checked next—in your own words.**

Each saved search has its own filters and optional AI prompt. Use the filters
to narrow the catalog, then describe the preferences, exclusions, red flags,
and missing information you want the AI to assess when a listing matches.
You are not limited to a fixed checklist.

```text
Your search filters → Matching listings → AI reviews your prompt against the facts
                                                        ↓
                                   Recommended · Discarded · Needs review
                                                        ↓
                                       Your notes. Your final decision.
```

### Write the review you wish you had time to do

For example, save a search for vans and give it this prompt:

> I'm looking for a van for a personal camper conversion. Check whether the
> published asking price is below €18,000 and mileage is under 120,000 km.
> Reject listings explicitly described as accident-damaged or non-running.
> If mileage, condition, or VAT status is missing or unclear, mark it as
> needing review. Explain which published facts support the conclusion.
> Do not treat the asking price as the final total cost or infer roadworthiness
> from photographs.

Or ask a housing search to flag confirmed occupancy, ownership shares, or
missing possession information. Different saved searches can ask different
questions—even about the same listing.

### From a match to an explained assessment

1. **Set the filters** in **My searches**: categories, sources, location,
   published amounts, and other supported criteria.
2. **Write your prompt and enable AI** for that saved search.
3. **Review matching findings.** After a search run ingests listings, enabled
   saved searches can assess the matches within configured processing limits.
   You can also request an evaluation from **My searches**.
4. **See the verdict and why.** Assessments include a short explanation,
   supporting published evidence, missing information, and a score when
   supported. An unknown relevant fact should mean **Needs review**, not a
   made-up answer. Your manual OK/KO decisions remain separate and are not
   overwritten by the AI.

AI is **optional and disabled by default**. Configure an Ollama or supported
OpenAI-compatible provider using `AUCTIONRADAR_AI_PROVIDER`,
`AUCTIONRADAR_AI_BASE_URL`, and `AUCTIONRADAR_AI_MODEL`; OpenAI-compatible
providers also require an API key, which can be supplied through
`AUCTIONRADAR_AI_API_KEY_FILE`. Then enable AI per saved search.

The configured model receives your prompt and selected listing facts—not your
private review notes or account identity. If you choose a remote provider,
that information leaves your server and provider charges may apply; do not put
secrets in a prompt.

> **An evidence-based second pass, not an independent inspection.** The AI
> compares supplied listing data with your instructions. It does not browse
> for additional evidence, visually inspect photos, verify the physical asset,
> or establish its legal status. You stay in control of the final review.

## See it in action

![AuctionRadar demo: filter public listings, browse photos, save a private review, and switch to dark mode](docs/media/auctionradar-demo.gif)

<p align="center">
  <a href="docs/media/auctionradar-demo.mp4">Watch or download the full-resolution video</a> ·
  <a href="docs/media/auctionradar-overview.png">Static screenshot</a> ·
  <a href="docs/DEMO.md">Recording notes</a>
</p>

**A real application, not a mockup.** Recorded locally using public BOE and
Escogecasa results retrieved on October 27, 2026, with a disposable account.
The walkthrough shows filtering, photos, a saved review, and light/dark themes.
It does not demonstrate AI inference, search execution, or authenticated BOE
access. Listings remain in their original language; displayed prices are not
verified total costs.

## Built for a careful review

| | What you can do |
| :--- | :--- |
| **Discover** | Configure public HTTPS sources and run bounded searches with per-source coverage and limitations. |
| **Focus** | Filter by asset category, source, location, published amount, photos, and review status. |
| **Ask AI** | Give each saved search a custom prompt; assess matching listings against your preferences with explained, evidence-linked verdicts. |
| **Inspect** | Browse listing photographs, published facts, document-reading status, and links to original portals. |
| **Decide** | Mark findings as pending, OK, or KO; save private notes and revisit review history. |
| **Keep control** | Separate each account's sources, searches, notes, decisions, and history into private workspaces. |
| **Share selectively** | Create revocable, read-only links without exposing private notes or authenticated material. |

Light and dark themes, a mobile-friendly layout, and English/Spanish UI text
are included. Portal content is never automatically translated.

### A simple workflow

```text
Configure sources → Run a search → Match your saved-search criteria
                                               ↓
                                Optional AI review with your prompt
                                               ↓
                                  Inspect evidence and AI findings
                                               ↓
                                   Save your notes and decisions
                                               ↓
                                    Share a read-only listing
```

Fresh installations start with **an empty source catalog and no default
accounts**. No personal databases, certificates, saved searches, or installation
history are distributed.

## Installation

Choose a guided Proxmox deployment or a local development preview.

### Install on Proxmox VE

![Proxmox VE LXC](https://img.shields.io/badge/Proxmox_VE-Debian_13_LXC-E57000?style=flat-square&logo=proxmox&logoColor=white)

**One entry point. A guided setup. Your own unprivileged LXC.**

Open **your Proxmox node → Shell** as root—not the console of an existing
container—and run:

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://raw.githubusercontent.com/jojoyaco/auctionradar/main/install.sh -o auctionradar-install.sh && bash auctionradar-install.sh install
```

The installer guides you through a free CTID, container storage, template
storage, and bridge. It downloads a Debian 13 template and a commit-pinned
AuctionRadar snapshot, builds the application package, and installs the services
inside the **new** container. Defaults: **2 cores · 2 GB RAM · 12 GB disk · DHCP**.
No manual release building or file transfers needed.

**After installation:** configure HTTPS, then create an administrator from the
container console with `auctionradar-create-admin admin`. The bootstrap web
listener stays private to the container until HTTPS is configured. No default
passwords, open registration, or Internet exposure are enabled for you.

> **Preview installer:** targets amd64 Proxmox VE 8/9; a complete installation
> on a real Proxmox host remains unverified. This is AuctionRadar's own helper,
> **not an official Community Helper Script**. Downloading and running it trusts
> this repository with root access—review the script first if preferred.

[Installation guide, unattended mode, and dry-run](docs/PROXMOX.md) ·
[HTTPS and first login](docs/SELF_HOSTING.md#production-https) ·
[Read the installer](install.sh)

### Local development

For a local, loopback-only development preview. For an LXC deployment, use
the [Proxmox installer](#install-on-proxmox-ve) instead.
Requires **Python 3.10+** and **curl 8.4+**.
Poppler's `pdftotext` is optional for public PDF text extraction; OpenSSL is
needed for TLS tests.

#### 1. Install

```sh
git clone https://github.com/jojoyaco/auctionradar.git
cd auctionradar
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/pip install --no-deps -e .
```

#### 2. Create your workspace

```sh
export AUCTIONRADAR_WORKSPACE_MODE=private
export AUCTIONRADAR_DATA_DIR="$PWD/instance"
export AUCTIONRADAR_ENV=development
export AUCTIONRADAR_SESSION_SECURE=false

# Enter a password of at least 12 characters at the hidden prompt.
.venv/bin/python -m auctionradar.web.manage create-user --username admin --admin
```

#### 3. Start the application

```sh
.venv/bin/gunicorn --bind 127.0.0.1:8000 'auctionradar.web:create_app()'
```

Open **<http://127.0.0.1:8000>**, sign in, and add a public source under
**Sources and access**. Enable the source before a future run. Saving a source
does not contact the portal or start a search.

> **Local preview only.** Do not expose this HTTP development configuration to
> a network. For persistent sessions or multiple workers, configure
> `AUCTIONRADAR_SECRET_FILE` with a private file outside the checkout.
> Production requires HTTPS and protected secrets; follow the
> [self-hosting guide](docs/SELF_HOSTING.md).

Prefer the terminal? Explore the public search CLI with:

```sh
.venv/bin/python -m auctionradar.cli --help
```

### Production setup and security

The [guided Proxmox VE installer](#install-on-proxmox-ve) creates a **fresh,
unprivileged Debian 13 LXC**, with application code separate from persistent
data. An [explicit offline-package path](docs/PROXMOX.md#manual-package-installation)
is also available. The installer does not configure public Internet access or
enable scheduled jobs, and a full real-host Proxmox installation remains unverified.

- [Self-hosting, HTTPS, secrets, and backups](docs/SELF_HOSTING.md)
- [Proxmox installation and packaging](docs/PROXMOX.md)

An optional personal **BOE P12/PFX certificate** can retrieve authenticated asset
photographs. It does not retrieve every BOE document or authenticate to other
portals. Uploading transfers a signing identity to your server: read the
[certificate security notes](docs/SELF_HOSTING.md#optional-boe-certificate) first.

## Know the limits

- **Research, not execution.** No automatic bids, payments, deposits, or seller contacts.
- **Bounded coverage.** Supported connectors are not exhaustive crawlers. Logins,
  arbitrary JavaScript sites, and changing portal markup can limit extraction.
- **Evidence, not assumptions.** Missing photos or unpublished facts stay missing.
  An auction value, asking price, and deposit are different figures—not a total cost.
- **No guarantees.** Listings can be incomplete or outdated. AuctionRadar does not
  verify possession, legal status, fees, or suitability and is not legal or
  investment advice.

## Under the hood

One Python package: a Flask web application, a public search CLI, portal-specific
connectors, and SQLite-backed workspace state. The browser UI uses vanilla
JavaScript and CSS.

```text
auctionradar/
├── cli.py              Public search entry point
├── connectors/         Portal discovery and extraction
├── network.py          Public-destination checks and HTTPS transport
├── documents.py        Public document handling
├── web/                Accounts, workspaces, review, sharing, and UI
└── defaults/           Empty configuration templates
deploy/product/         Release packaging and LXC installation
deploy/proxmox/         Guided Proxmox host installer
install.sh              Downloadable install entry point
docs/                   Operator and development guides
tests/                  Automated tests and fixtures
```

### Development checks

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
.venv/bin/pip check
node tests/ui_preferences.test.cjs
node tests/review_detail_handler.test.cjs
node tests/detail_next_handler.test.cjs
```

See [verification and test limits](docs/VERIFICATION.md) for browser checks,
packaging checks, linting, and what passing tests do **not** establish.

## Documentation

| Guide | Start here when you want to… |
| :--- | :--- |
| [Self-hosting and security](docs/SELF_HOSTING.md) | Configure HTTPS, accounts, certificates, backups, and isolation. |
| [Proxmox](docs/PROXMOX.md) | Package the application and prepare a new LXC. |
| [Architecture](docs/ARCHITECTURE.md) | Understand package boundaries and the search/review flow. |
| [Verification](docs/VERIFICATION.md) | Run checks and understand their coverage. |
| [Demo assets](docs/DEMO.md) | Reuse the video, GIF, and screenshots or regenerate the GIF. |
| [Licensing](docs/LICENSING.md) | Understand permitted use and restrictions. |
| [Third-party notices](THIRD_PARTY_NOTICES.md) | Review dependency and data terms. |

## License

Original AuctionRadar material is offered under the
[AuctionRadar Noncommercial License 1.0](LICENSE). **Commercial and other
profit-oriented use is prohibited**, including identifying auctions, investments,
purchases, or resales intended to earn a return.

This is a custom, **source-available license**, not an OSI-approved open-source
license. Legal review is pending. See the [licensing guide](docs/LICENSING.md)
for scope and limitations. Third-party components, listing content, and images
retain their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
