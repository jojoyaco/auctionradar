# Contributing to AuctionRadar

Thanks for helping improve AuctionRadar. Keep changes focused, evidence-backed,
and safe for private workspaces. Read the [architecture](docs/ARCHITECTURE.md),
[verification guide](docs/VERIFICATION.md), and [license](LICENSE) first.
AuctionRadar is source-available under a custom noncommercial license, not an
OSI-approved open-source project. Contribute only material you have the right
to submit under the applicable project terms; retain third-party attribution.

## 1. Open an issue before a pull request

**Every pull request must reference an issue that existed before the PR was
opened. This includes documentation-only changes and draft PRs.** Search for
an existing issue first; reuse it and comment with your intended scope rather
than opening a duplicate. Otherwise, [open an issue](https://github.com/jojoyaco/auctionradar/issues/new/choose).

- **Bug:** include expected/actual behavior, minimal reproduction, version or
  commit, environment, and redacted evidence.
- **Feature, refactor, documentation, or maintenance:** explain the problem,
  proposed scope, alternatives, and observable acceptance criteria.
- **Larger changes:** discuss new dependencies, connectors, database layouts,
  authentication, AI behavior, and installer changes with a maintainer before
  investing in a substantial implementation.

An issue is a discussion, not automatic approval of its proposed solution.
Do not use a PR as the first place to introduce an untracked change. Maintainers
may ask you to supply the missing issue and resubmit the PR if the order was
not followed. Security-sensitive reports use [private coordination](#security-and-sensitive-data)
instead of a public issue containing exploit details.

## 2. Work on a focused branch

Branch from the current `main`, or use your fork if you do not have write
access. Keep one independently reviewable concern per branch/PR. Recommended
branch names are:

```text
fix/123-preserve-review-notes
feat/124-add-source-filter
docs/125-explain-local-development
```

The numbers above are examples: use the actual issue number. Do not bundle
unrelated formatting, dependency upgrades, or generated artifacts with a fix.
Avoid rewriting shared history. Apply any host-specific worktree/ownership
rules; never discard another contributor's changes to make your branch clean.

For local setup, follow [README → Local development](README.md#local-development).
Use disposable identities and state, keep the preview on loopback, and do not
copy a production database, certificates, or personal source catalog.

## 3. Use Conventional Commits and PR titles

Authored commits **and pull request titles** follow
[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

```text
<type>[optional scope][!]: <short description>
```

| Type | Use for |
| --- | --- |
| `feat` | A new user-visible capability. |
| `fix` | Correcting faulty behavior. |
| `docs` | Documentation changes. |
| `refactor` | Restructuring without an intended behavior change. |
| `perf` | Performance improvements. |
| `test` | Test coverage or test infrastructure. |
| `build` | Packaging or dependency/build changes. |
| `ci` | Continuous integration configuration. |
| `style` | Formatting only, not behavioral or design changes. |
| `chore` | Other focused maintenance. |
| `revert` | Reverting an earlier change; reference the original commit. |

Use lowercase types and scopes. Scopes are optional but helpful, for example
`web`, `cli`, `ai`, `boe`, `connectors`, `proxmox`, `packaging`, or `security`.
Write an imperative English description, aim for at most 72 characters in the
whole subject/title, and omit a trailing full stop. These style choices are
project conventions in addition to the upstream specification.

Good PR titles:

```text
fix(web): preserve notes when changing a review decision
feat(ai): explain missing evidence in saved-search assessments
docs: clarify Proxmox installation prerequisites
```

Do not use branch names, `Update files`, `WIP`, or `[Draft]` as the title.
Use GitHub's draft state instead. Put issue references in the PR body, not as
a replacement for a meaningful title.

For a breaking change, add `!` before the colon and explain the impact and
migration in the commit footer and PR description:

```text
feat(api)!: rename the review status field

BREAKING CHANGE: clients must send review_status instead of status.
Refs: #123
```

This is an illustrative example, not an announced API change. Do not rewrite
old shared commits to retrofit this convention. Maintainers should preserve a
conventional final subject when squash-merging; generated merge commits are
not a reason to rewrite repository history.

## 4. Validate the change

Add a regression test for a bug fix and exercise the new behavior for a feature.
Prefer deterministic synthetic fixtures; keep real portal checks separate and
record their coverage and limits. Follow the commands in the
[verification guide](docs/VERIFICATION.md), including the full Python suite for
application, dependency, and deployment changes.

| Changed area | Expected evidence |
| --- | --- |
| Documentation only | Check relative links, anchors, commands against the current code, and Markdown structure. Explain why runtime tests are not needed. |
| Python/application | Relevant regression tests, full Python suite, `pip check`, and Ruff checks. |
| Browser UI | Relevant Node suites and real-browser checks; include desktop/mobile screenshots or a short recording when appearance or interaction changes. Check keyboard use and both languages/themes where affected. |
| Connectors/documents | Redacted or synthetic fixtures, provenance, missing-data/error behavior, and bounded network checks if needed. Never imply exhaustive coverage. |
| Auth/workspaces/sharing | Tests for owner isolation, anonymous/private response boundaries, and revocation where relevant. |
| AI | Deterministic provider fixtures, evidence validation, unavailable/missing-data behavior, and no overwrite of human decisions. |
| Installer/packaging | Shell syntax, dry-run/error paths, package safety/reproducibility, and relevant extracted-runtime tests. Label simulated PVE checks as simulated. |

Use the style configuration in `pyproject.toml`. For Python changes:

```sh
.venv/bin/ruff check auctionradar tests deploy/product/package.py deploy/proxmox/install.py
.venv/bin/ruff format --check auctionradar tests deploy/product/package.py deploy/proxmox/install.py
```

For deployment shell changes:

```sh
bash -n install.sh deploy/product/create-lxc.sh deploy/product/install-lxc.sh deploy/product/auctionradar-create-admin
```

Keep `requirements.in` and the hash-pinned `requirements.txt` consistent when
changing runtime dependencies. Review license/security implications and retain
the lockfile's generation recipe; do not remove hashes to bypass an install
failure. New packages require a justification, not just an import.

## 5. Open and review the PR

Target `main` unless a maintainer has agreed otherwise. Use the
[PR template](.github/pull_request_template.md) and include:

- The **pre-existing issue**, a problem/solution summary, and acceptance criteria.
- Exact validation commands and results, with skipped checks and reasons.
- UI evidence where relevant, plus security/privacy implications.
- Documentation changes and any compatibility, configuration, or data migration
  requirements. Explain backup/rollback limitations for operational changes.

Use `Closes #123` only when the PR fully resolves the issue. Use `Refs #123`
for partial progress and explain what remains. GitHub's closing keywords take
effect when a PR targeting the default branch is merged; a reference alone is
not proof that acceptance criteria were met.

Request maintainer review, address feedback, and keep the PR diff focused.
Do not merge your own change without maintainer approval or bypass required
checks. Prefer follow-up commits while review is active rather than rewriting
another person's work. A maintainer may squash the final change.

These rules and templates guide contributions; they **do not themselves enforce
issue creation timestamps, title syntax, or branch protection on GitHub**.
No such enforcement should be claimed unless separate checks/settings exist.
GitHub exposes the new issue/PR templates once they are merged into the default
branch; publishing a feature branch alone does not activate them.

## Security and sensitive data

Do not post passwords, API keys, cookies, share tokens, P12/PFX files, private
keys, real account identifiers, private notes, or production database/log dumps
in issues, PRs, test fixtures, or recordings. Sanitize screenshots and URLs;
public listing material may still carry personal data and third-party rights.

For vulnerabilities, use **Security → Report a vulnerability** if that option
is available. See [SECURITY.md](SECURITY.md) for the private contact fallback and
supported-version policy. Coordinate a private report or
advisory **before** a security-fix PR; do not open a public vulnerability issue
just to satisfy the issue-first rule. No response-time or support guarantee is
implied by this guide.

## Working with AI assistance

AI-assisted contributions follow the same issue-first, naming, testing, and
review rules. You remain responsible for correctness, licensing, and the final
diff. Never send repository secrets or user data to an external model. Summarize
material AI assistance and any limitations in the PR; do not paste private chat
transcripts or claim tests that were not run. Agent-specific instructions are
in [AGENTS.md](AGENTS.md).

Be respectful in issues and reviews. Discuss the behavior and evidence, not the
person. Small, well-explained changes are easier to review than broad rewrites.
