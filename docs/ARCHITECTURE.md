# Architecture and development

AuctionRadar is one Python application package. Portal-specific code belongs in
`auctionradar/connectors/`, not in the repository root or the Flask view layer.
The project can be run from a checkout or installed as a Python wheel.

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

`defaults/` is the single source of distributed configuration. CLI and web code
read the same templates; runtime workspaces hold their own private configuration
and state. Never write user data into the installed package.

## Search and review flow

1. A request is authenticated and routed to its owner's workspace.
2. The web application starts a detached, capacity-limited search worker.
3. The worker invokes `python -m auctionradar.cli` with explicit workspace paths.
4. Public connectors fetch bounded source data and write a run artifact.
5. Ingestion updates that workspace without replacing private review decisions.
6. Optional certificate access runs separately and stores private media; public
   share responses exclude authenticated material and private notes.

Importing the package does not query source portals. Saving a source does not
fetch it. Background workers must receive effective workspace paths explicitly,
not inherit another installation's configuration.

## Development conventions

- Write documentation, comments and developer-facing help in English. The UI
  starts in English and supports Spanish. Preserve original-language portal
  evidence, parser vocabulary and localization strings.
- Keep portal integrations behind package boundaries. Do not add compatibility
  scripts or import shims to the repository root.
- Use the module entry points and `AUCTIONRADAR_*` environment variables shown
  in the operator guides. Keep credentials out of arguments and source control.
- Python style is defined in `pyproject.toml`. Install the optional development
  tools with `python -m pip install '.[dev]'`, then run `ruff check` and
  `ruff format --check` against `auctionradar`, `tests`, and
  `deploy/product/package.py`.
- Run the [verification suites](VERIFICATION.md) before changing packaging or
  worker paths. Layout regression tests reject loose root modules and duplicate
  default configuration.

The release builder uses an explicit allowlist; it does not archive the whole
checkout. Python wheels include templates, static assets, empty defaults and
license notices, but not accounts, credentials or test-generated state.
