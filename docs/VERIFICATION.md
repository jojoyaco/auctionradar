# Verification and test limits

The current repository history starts from a clean product snapshot. It does not
include commits, reflogs, backups, personal catalogs, or operational documents
from another installation. Tests use synthetic identities and data.

## Verified package layout — 2026-10-27

The reorganized `auctionradar/` package was checked with the following results:

- 530 Python tests passed with `ResourceWarning` treated as an error.
- `pip check`, three Node test suites, JavaScript and shell syntax checks passed.
- Ruff lint and formatting checks passed for application code, tests and the
  release builder.
- Real-browser checks covered two-account registration and isolation, empty
  sources, sharing and revocation, English as the initial language, optional
  Spanish, and mobile/desktop sizes.
- Two consecutive builds produced an identical package containing the license,
  third-party notices, and empty configuration, without personal data or
  installation history.
- A Python wheel was built and installed outside the checkout. Its CLI, empty
  defaults, application factory, templates, static files and license metadata
  were verified independently of source-directory imports.
- A Gitleaks 8.30.1 scan of the sanitized tree found no credentials. This does not
  prove the absence of all sensitive data or replace an independent audit.

Legal review of the license and a full installation on a real Proxmox host remain
pending.

## Run the checks

Install the pinned runtime dependencies, then run the Python and Node suites:

```sh
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
.venv/bin/pip check
node tests/ui_preferences.test.cjs
node tests/review_detail_handler.test.cjs
node tests/detail_next_handler.test.cjs
bash -n deploy/product/create-lxc.sh deploy/product/install-lxc.sh deploy/product/auctionradar-create-admin
```

Install the optional development tools and check Python style:

```sh
.venv/bin/pip install '.[dev]'
.venv/bin/ruff check auctionradar tests deploy/product/package.py
.venv/bin/ruff format --check auctionradar tests deploy/product/package.py
```

Layout tests enforce the package entry points, a root without loose Python
modules, and a single set of empty defaults shared by the CLI and web app.
The extracted-release runtime test starts two Gunicorn workers over real TLS,
uploads a synthetic P12, and checks persistent sessions after restart.

Browser checks require `playwright-core` to be available through `NODE_PATH` and
a Chrome or Chromium executable:

```sh
AUCTIONRADAR_TEST_PYTHON="$PWD/.venv/bin/python" \
AUCTIONRADAR_TEST_CHROME=/path/to/chrome \
  node tests/run_browser_checks.cjs
```

The optional legacy browser suite can be run with:

```sh
AUCTIONRADAR_TEST_PYTHON="$PWD/.venv/bin/python" \
AUCTIONRADAR_TEST_CHROME=/path/to/chrome \
  node tests/run_browser_checks.cjs --legacy
```

Browser previews bind to loopback and are removed when the tests finish. They use
no real data or certificates and do not install system services. Passing tests
do not establish exhaustive portal coverage, successful authentication with a
third-party BOE account, or successful installation on Proxmox.
