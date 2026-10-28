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

Legal review of the license remains pending. Real PVE 9.2.21 installation
coverage is recorded below; PVE 8 remains unverified.

## Guided Proxmox installer — 2026-10-27

The new `install.sh install` entry point and `deploy/proxmox/install.py` were
checked with 19 focused tests, including real shell/bootstrap execution with a
synthetic HTTP transport and the real package builder/host installer with fake
PVE tools. No actual host packages, guests, or network configuration were changed.

- The complete Python suite passed: **549 tests**, with `ResourceWarning`
  treated as an error.
- Three Node suites, Bash syntax checks, `pip check`, and Ruff lint/format
  checks passed. Local README/operator-guide links and anchors were checked.
- Source pinning, caller-stdin preservation, unsafe archive rejection,
  unattended input validation, cancellation, read-only dry-run, cluster-wide
  VM/CT conflicts, and installer handoff were exercised.
- The HTTPS configuration example is retained inside new installations at
  `/etc/auctionradar/nginx-https.conf.example`.

These 2026-10-27 checks were simulated-host and extracted-application checks.
They were not evidence of a real-host install; the subsequent live PVE 9.2.21
validation is recorded below. PVE 8 and the standalone remote-download launcher
path remain unverified. The helper remains a project-maintained preview, not an
accepted Community Helper Script.

## Real Proxmox VE installation and HTTPS acceptance — 2026-10-28

A complete installer run succeeded on amd64 Proxmox VE 9.2.21 using
`debian-13-standard_13.6-1_amd64.tar.zst`. Both the installer dry-run and the
installation completed for a disposable unprivileged Debian 13 CT (2 cores,
2048 MiB RAM, 12 GiB `local-lvm`, DHCP on `vmbr0`). Gunicorn and nginx were
active; the HTTPS health endpoint returned 200. The installed test vhost was
bound to `127.0.0.1:443` with a self-signed localhost certificate. HTTP
`/login` on the loopback bootstrap listener returned 426; all application and
proxy listeners remained loopback-only.

Two synthetic accounts exercised Secure-cookie login, registration-disabled
behavior, same-item owner isolation, private decision/note persistence,
read-only public sharing without private review data, wrong-owner revoke denial,
share revocation, and signout. Saved sessions and workspace data survived an
application-service restart and a reboot of the disposable CT. This was a
stdlib HTTPS acceptance harness, not a real-browser test. The run did not test
PVE 8, the standalone launcher's remote-download path, public/LAN exposure, or
live AI inference. No PVE host package, network, or storage configuration was
changed.

After the final code commit, the full Python suite passed (**571 tests**);
`pip check`, Ruff lint/format, shell syntax checks, and all three Node suites
also passed. See the validation matrix above for the distinction between these
automated checks and the live PVE run.

## Prepublication hardening and CI

The later hardening change adds regressions for:

- The real API → detached worker boundary, final run state, startup failure
  recovery, owner-private diagnostic logs, and workspace isolation. Only the
  public CLI in a disposable source copy is replaced with a no-network fixture.
- Provider redirects, including a TLS-to-HTTP downgrade with synthetic
  credentials, and Secure cookies for private production with registration off
  as well as on.
- Anonymous image-origin filtering and the actual Flask share CSP, including
  direct and redirected private-network requests in a real browser.
- A single notification per language change, preserved drafts/focus, saved-search
  render synchronization, and page errors in additional browser contexts.
- Usable static IPv4 host/gateway pairs and explicit host-side `pct enter`
  instructions for initial administrator setup.

The [GitHub Actions workflow](CI.md) runs Python 3.10/3.13 checks, browser/Node
regressions, packaging/style checks, hash-pinned dependency auditing and redacted
full-history secret scanning. Check the workflow result at the actual proposed
SHA; configuration alone is not evidence of a passing GitHub run. CI does not
establish legal clearance for demo assets or a real PVE installation. Follow the
[publication checklist](RELEASING.md) before making broader release claims.

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
bash -n install.sh deploy/product/create-lxc.sh deploy/product/install-lxc.sh deploy/product/auctionradar-create-admin
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
  node tests/run_browser_checks.cjs --all
```

The optional legacy browser suite can be run with:

```sh
AUCTIONRADAR_TEST_PYTHON="$PWD/.venv/bin/python" \
AUCTIONRADAR_TEST_CHROME=/path/to/chrome \
  node tests/run_browser_checks.cjs --legacy
```

The separate public image-policy browser regression uses the same environment:

```sh
AUCTIONRADAR_TEST_PYTHON="$PWD/.venv/bin/python" \
AUCTIONRADAR_TEST_CHROME=/path/to/chrome \
  node tests/public_share_image_policy.test.cjs
```

Browser previews bind to loopback and are removed when the tests finish. They use
no real data or certificates and do not install system services. Passing tests
do not establish exhaustive portal coverage, successful authentication with a
third-party BOE account, or successful installation on Proxmox.
