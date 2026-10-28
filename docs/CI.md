# GitHub Actions CI

The `CI` workflow runs for pull requests targeting `main`, pushes to `main`,
and manual `workflow_dispatch` runs. Feature-branch pushes do not start duplicate
runs. The Python suite runs on Python 3.10 (the package minimum) and Python 3.13.
The additional Node, style, wheel, and browser checks run once on Python 3.13.
The browser runner executes `tests/run_browser_checks.cjs --all` once and uses
only local loopback previews with synthetic data; it does not log into auction
portals or contact provider, Proxmox, or production services.

## Checks

- Full `unittest` suite with `ResourceWarning` treated as an error, and `pip check`
  on both supported Python versions.
- Poppler is installed on the ephemeral runner to exercise real optional PDF
  conversion. The installer/admin-helper simulations that require UID 0 run separately
  with `sudo`; they use fake PVE tools, not a real Proxmox host. Their skips in
  the ordinary non-root suite are not treated as installation evidence.
- Ruff lint/format, JavaScript and Bash syntax, all Node unit suites, and a wheel
  build/install/CLI smoke test on Python 3.13.
- All real-browser scenarios on Python 3.13 using the Chrome preinstalled on the
  standard Ubuntu 24.04 runner and pinned `playwright-core` 1.64.1.
- A browser check of the actual Flask share CSP: reviewed/same-origin images
  load, but direct and redirected private image requests are blocked even when
  the browser has local-network permission.
- `pip-audit` for both hash-pinned requirements files with hashes required,
  pip-based resolution disabled, and strict collection failures enabled.
- Gitleaks 8.30.1 over full Git history. The official Linux x64 release archive
  is checked against its pinned SHA-256 before execution; output is redacted
  and inline `gitleaks:allow` comments do not suppress findings.

The CI-only Ruff and setuptools pins live in `requirements-ci.txt`; both that
file and `requirements.txt` are hash-checked at install/audit time. There are no
dependency caches or uploaded artifacts. Jobs use standard Ubuntu runners,
explicit timeouts, and concurrency cancellation for superseded runs.

## Run checks locally

Create an environment and install the runtime lock before the Python checks:

```sh
python3 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v
.venv/bin/pip check
```

On a platform with compatible wheels, install CI tools and run the once-per-
matrix checks:

```sh
.venv/bin/pip install --only-binary=:all: --require-hashes -r requirements-ci.txt
.venv/bin/ruff check auctionradar tests deploy/product/package.py deploy/proxmox/install.py
.venv/bin/ruff format --check auctionradar tests deploy/product/package.py deploy/proxmox/install.py
node tests/ui_preferences.test.cjs
node tests/review_detail_handler.test.cjs
node tests/detail_next_handler.test.cjs
bash -n install.sh deploy/product/create-lxc.sh deploy/product/install-lxc.sh deploy/product/auctionradar-create-admin
```

For all browser scenarios, install Node.js 24, `playwright-core` 1.64.1, and
Chrome/Chromium, then point the runner at those binaries:

```sh
npm install --global --ignore-scripts --no-audit --no-fund playwright-core@1.64.1
AUCTIONRADAR_TEST_PYTHON="$PWD/.venv/bin/python" \
AUCTIONRADAR_TEST_CHROME=/path/to/chrome \
NODE_PATH="$(npm root --global)" \
  node tests/run_browser_checks.cjs --all
AUCTIONRADAR_TEST_PYTHON="$PWD/.venv/bin/python" \
AUCTIONRADAR_TEST_CHROME=/path/to/chrome \
NODE_PATH="$(npm root --global)" \
  node tests/public_share_image_policy.test.cjs
```

The Gitleaks invocation used by CI can also be run locally with Gitleaks 8.30.1:

```sh
gitleaks git --redact --no-banner --log-opts='--all' --ignore-gitleaks-allow .
```

## Cost and account limits

Standard GitHub-hosted runners have no per-minute charge for public repositories
(GitHub's execution and abuse-prevention limits still apply).
For private repositories, standard-runner minutes draw from the repository
owner's plan quota, which is shared with its other repositories and users; usage
above included quota may be billed. GitHub Free for organizations currently lists
2,000 included minutes per month, but check the live plan and billing dashboard
before enabling CI because included usage, consumption, payment methods, and
budgets can change.

If the goal is **$0 spend**, configure an account/organization Actions budget at
$0 and enable **Stop usage when budget limit is reached**, where that control is
available. This is an account-level recommendation, not an unlimited-free-run
promise or a cap implemented by this workflow. Budget scope and in-progress
usage matter; this repository does not change billing settings. See GitHub's
[Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
and [budget controls](https://docs.github.com/en/billing/how-tos/set-up-budgets).

The standard Ubuntu 24.04 runner avoids paid larger runners. Workflow dependencies
are pinned to upstream action commits, with weekly Dependabot updates limited to
GitHub Actions so it does not duplicate the repository's runtime lock updates.

## Checks versus enforced merge gates

A green workflow reports the checks that ran; it does not by itself prevent an
unchecked merge. The current private GitHub Free organization repository cannot
enable branch protection or rulesets: GitHub's API returns a plan/visibility
restriction. This is not a restriction that workflow YAML can remove. Keep the
repository private and free-only unless the owner separately chooses otherwise;
review all four CI jobs before merging. Recheck available protection and private
vulnerability-reporting features if the plan or visibility changes. See
[GitHub's branch-protection documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
and the [private security contact](../SECURITY.md).
