# Architecture and development

AuctionRadar is one Python application package. Portal-specific code belongs in
`auctionradar/connectors/`, not in the repository root or the Flask view layer.
The project can be run from a checkout or installed as a Python wheel.

This is the canonical architecture document. For contribution workflow and naming,
see [CONTRIBUTING.md](../CONTRIBUTING.md); for agent instructions, see
[AGENTS.md](../AGENTS.md). Deployment procedures belong in
[Self-hosting](SELF_HOSTING.md) and [Proxmox](PROXMOX.md).

## System at a glance

AuctionRadar is a modular monolith, not a collection of microservices. A Flask
application serves HTML templates and a vanilla JavaScript/CSS interface;
detached Python processes perform bounded search and evaluation work. SQLite
stores identity and workspace state. Public connectors use the shared HTTPS
transport; optional AI inference calls an explicitly configured provider.

```text
Browser → Flask app factory + blueprints → authentication + workspace routing
                                             │
                         ┌───────────────────┼───────────────────────┐
                         ↓                   ↓                       ↓
                  search worker       human review API      saved-search worker
                         ↓                   ↓                       ↓
                    public CLI          workspace DB        criteria → optional AI
                         ↓                   ↑                       ↓
                  portal connectors → run artifact → ingestion → matches/assessments
                         ↓
                  public HTTPS transport

Anonymous share → opaque-token lookup → owner's filtered public projection
Optional BOE certificate → separate private-media path (never public shares)
```

The diagram describes intended responsibility boundaries, not a guarantee that
every queue/deployment path has been exercised successfully. See the
[verification limits](VERIFICATION.md) and [demo limitations](DEMO.md#scope-and-known-limitations).

## Package boundaries

| Module | Responsibility |
| --- | --- |
| `auctionradar.cli` | Bounded public searches and JSON run artifacts |
| `auctionradar.assets` | Shared categories and evidence-based classification |
| `auctionradar.network` | Public-destination validation and HTTPS transport |
| `auctionradar.documents` | Optional text extraction from public documents |
| `auctionradar.connectors.catalog` | Discovery, source routing and extraction |
| `auctionradar.connectors.boe` | BOE parsing and optional authenticated photos |
| `auctionradar.connectors.escogecasa` | Escogecasa public-listing enrichment |
| `auctionradar.web` | Accounts, workspaces, review, sharing and background jobs |
| `auctionradar.defaults` | Empty configuration templates shipped as package data |

Within `auctionradar.web`, use these ownership boundaries:

| Area | Modules | Responsibility |
| --- | --- | --- |
| Application setup | `__init__.py`, `config.py`, `security.py`, `proxy.py` | Factory, configuration, session/CSRF controls, trusted proxy scheme handling. |
| Identity and persistence | `db.py`, `models.py`, `workspaces.py` | SQLite access, records, request-scoped owner routing, and central identity/share index. |
| Public-source setup | `connections.py` | Owner-scoped source records and atomic source/seed generations. Saving is not fetching. |
| Search execution | `views.py`, `runner.py`, `worker_environment.py`, `job_limits.py` | Authorized job requests, detached workers, explicit paths, and bounded capacity. |
| Ingestion | `ingest.py` | Stable item identity, fingerprints, run imports, and review-preserving updates. |
| Saved searches and AI | `search_views.py`, `search_runner.py`, `searches.py`, `catalog.py`, `ai_filter.py` | Owner-specific criteria/prompts, matching, evidence-checked assessments, caching, and notifications. |
| Private BOE access | `credential_store.py`, `workspace_boe.py`, `private_media.py` | Encrypted credentials, optional authenticated photographs, and owner-only delivery. |
| Presentation | `templates/`, `static/` | Browser rendering, filters, gallery, review interaction, localization, and themes. |

`defaults/` is the single source of distributed configuration. CLI and web code
read the same templates; runtime workspaces hold their own private configuration
and state. Never write user data into the installed package.

## Persistence and isolation

In private mode, the persistent data root contains:

```text
identity/identity.sqlite3       Central accounts and opaque public-share index
workspaces/<owner-id>/
  app.sqlite3                  That owner's items, reviews, runs, and saved searches
  config/                      Profile, feedback, and private source configuration
    current → generations/...  Atomically selected source/seed generation
  runs/                        Search JSON artifacts and local raw evidence
  private-media/               Optional authenticated material
```

See `workspaces.py` and `db.py` for request-scoped database selection and
`worker_environment.py` for detached-process path snapshots. Local identity
references in workspace databases are not a second shared authentication store.
Requests must not switch a process-global default to route another user's data.

The optional `legacy` mode exists for explicit adoption/compatibility; new
packaged installations use private mode. Do not copy an existing operator's
configuration into new accounts or distributed defaults.

App code, configuration, and persistent data are separate in packaged installs.
Session and credential-encryption keys are private external files, not release
assets. Workspace boundaries protect users at the application level; they do
not protect stored credentials from root or a compromised host. For paths,
HTTPS, backup consistency, and key-loss implications, use the operator guide.

## Search and review flow

1. A request is authenticated and routed to its owner's workspace.
2. The web application starts a detached, capacity-limited search worker.
3. The worker invokes `python -m auctionradar.cli` with explicit workspace paths.
4. Public connectors fetch bounded source data and write a run artifact.
5. Ingestion updates that workspace without replacing private review decisions.
6. Active saved searches match ingested items and can evaluate them using their
   own prompts, within configured capacity/budget limits.
7. Optional certificate access runs separately and stores private media; public
   share responses exclude authenticated material and private notes.

Importing the package does not query source portals. Saving a source does not
fetch it. Background workers must receive effective workspace paths explicitly,
not inherit another installation's configuration.

The API keeps detached-runner stdout/stderr in an owner-private
`runs/<run-id>.runner.log` (mode `0600`), not in a public artifact. Run-status
polling and new-run requests reconcile workers that exited before finalizing
their row. This is recovery on API activity, not a continuously running process
supervisor. Review diagnostic logs before sharing them and include them only in
private, appropriately protected backups.

## Saved-search assessment versus human review

`searches.py` first applies structured criteria to the workspace catalog.
For AI-enabled searches, `ai_filter.py` sends the saved-search prompt and a
bounded, allowlisted listing snapshot to Ollama or an OpenAI-compatible
provider. Private review notes, account identities, cookies, and credentials
are not part of the listing payload. The configured provider still receives
the prompt and selected published data; remote inference is an external data
transfer, not automatically local processing.

Assessments use `keep`, `reject`, or `needs_review`, with reasons, missing
fields, and evidence references. Supported factual claims must match evidence
IDs and exact field/value pairs from the supplied snapshot. Missing data,
provider failure, or unsupported claims must not turn into invented certainty.
Photos are metadata, not visual model input. There is no browsing or tool use.

Evaluation fingerprints include factual input, prompt, criteria, provider/model,
and schema settings so unchanged inputs can reuse assessments. Changed listing
content invalidates the old assessment; edited prompts/criteria need a new
evaluation. Budget/capacity exhaustion can leave a visible backlog.

AI results belong to a **search/item match**. Manual pending/OK/KO decisions and
notes are separate records: inference must not overwrite a human review.
Notifications track qualifying new/changed matches and remain owner-scoped.

## Trust boundaries and invariants

- **Public portals:** HTML, documents, URLs, and extracted text are untrusted.
  Keep public-destination checks, connection IP pinning, redirect checks,
  timeouts, and request/body limits in the shared transport.
- **Facts:** a missing photo or unknown possession is not proof of absence.
  Asking price, auction value, deposit, and total cost are different concepts.
  Keep provenance, extraction/read status, and source limitations visible.
- **Accounts:** authorize owner access before selecting paths or launching work.
  Never accept a caller-supplied path as authority to another workspace.
- **Public sharing:** resolve the opaque token through the central share index
  and return a filtered public projection. Revocation and owner deactivation
  must prevent later access. Never include private reviews or authenticated media.
- **BOE credentials:** require explicit consent and encryption; keep access
  separate from public crawling. Removing a stored credential does not revoke
  its certificate with the issuer or erase already retrieved media.
- **Side effects:** importing modules and saving sources must not crawl portals.
  No search/evaluation path may place bids, deposits, payments, or seller contacts.

## Packaging and Proxmox deployment

The root `install.sh` is a host-side entry point. A standalone copy resolves a
source revision and downloads a commit-pinned snapshot; a checkout can use local
source. `deploy/proxmox/install.py` gathers and validates host choices, prepares
the Debian template/product archive, and delegates to
`deploy/product/create-lxc.sh`. That script creates a new unprivileged LXC and
runs `install-lxc.sh` inside it. Existing guests are not upgrade targets.

`deploy/product/package.py` builds a deterministic, allowlisted artifact and
verifies its digest, manifest, paths, and member types. It does not archive the
whole repository. Python wheels include package code, templates, static assets,
empty defaults, and license notices, not workspace databases or private media.

The bootstrap listener remains loopback-only until HTTPS is deliberately
configured. Publishing code is not deployment; simulated installer tests are
not evidence of a full installation on a real Proxmox node.

## Development conventions

- Write documentation, comments and developer-facing help in English. The UI
  starts in English and supports Spanish. Preserve original-language portal
  evidence, parser vocabulary and localization strings.
- Keep portal integrations behind package boundaries. Do not add compatibility
  scripts or import shims to the repository root.
- Use the module entry points and `AUCTIONRADAR_*` environment variables shown
  in the operator guides. Keep credentials out of arguments and source control.
- Python style is defined in `pyproject.toml`. Install the optional development
  tools as described in the verification guide, then run `ruff check` and
  `ruff format --check` against `auctionradar`, `tests`,
  `deploy/product/package.py`, and `deploy/proxmox/install.py`.
- Run the [verification suites](VERIFICATION.md) before changing packaging or
  worker paths. Layout regression tests reject loose root modules and duplicate
  default configuration.

When changing a boundary, document the reason, alternatives, compatibility
impact, and acceptance tests in the issue/PR. Update this document when the
decision changes module ownership, persistence, security, or deployment behavior;
do not record hypothetical features as current architecture.
