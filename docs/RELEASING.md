# Publication and release checklist

Making the repository public, publishing a versioned artifact, and deploying a
working installation are different operations. None is implied by a passing CI
run. Do not change visibility or deploy merely to finish a contribution.

## Before making the repository public

- [ ] Review the intended commit, all public refs and PR heads for secrets and
  sensitive data, not just the latest files. Check issue/PR text, attachments,
  Actions logs/artifacts, and Git author/committer identities too. Revoke exposed
  credentials first; deleting a branch or rewriting Git does not revoke a key.
- [ ] Confirm redistribution rights for every screenshot, video, photograph,
  fixture and third-party component. Publicly visible portal content is not
  automatically cleared for redistribution. Record permission/terms evidence
  or replace the material with appropriately licensed or synthetic content.
- [ ] Review previous demo media across Git history, refs, CI artifacts, and
  retained copies. Replacing current files with synthetic content does not erase
  older portal images or establish legal clearance for historical material.
- [ ] Keep the custom noncommercial license and third-party notices prominent.
  Do not call AuctionRadar OSI open source or change licensing without an
  explicit maintainer decision. Qualified legal review is a separate task.
- [ ] Verify a monitored private contact and the instructions in
  [SECURITY.md](../SECURITY.md). When public, enable and test GitHub private
  vulnerability reporting and the available security features; do not assume
  they are enabled merely because visibility changed.
- [ ] Check [CI](CI.md) at the exact proposed SHA, including detached-worker,
  browser, image-origin, dependency and secret-scanning regressions. Document
  skipped checks rather than presenting them as passes.
- [ ] Review Actions billing/limits and access permissions. Standard public
  GitHub-hosted Linux runners are free; private usage consumes the owner's
  shared allowance. Preserve a zero-dollar hard-stop budget when free-only use
  is required. Do not enable paid runners or services as an incidental step.
- [ ] Apply appropriate branch/review protections where the plan supports them.
  Templates and an issue-first policy are not automatic merge enforcement.

Changing back to private does not remove public forks or copies. Review
[GitHub's visibility consequences](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)
before the actual change.

## Before calling an installer release supported

- [ ] Run the documented Python minimum and deployment interpreter checks,
  reproducible product build, wheel installation, and full browser suite.
- [ ] Complete a disposable **real** PVE/Debian installation, including initial
  `pct enter <CTID>` access, admin creation, HTTPS, health, restart persistence,
  two-user isolation and share revocation. Fake `pct` tests are not this check.
- [ ] Review the backup/restore instructions and preserve encryption/session
  keys. The new-LXC installer is not an automatic upgrade or rollback tool.
- [ ] Choose a unique version/tag, write human-readable release notes, identify
  the tested source SHA, and provide artifact verification/provenance. A digest
  delivered beside an artifact is not a publisher signature by itself.

See [verification](VERIFICATION.md), [self-hosting](SELF_HOSTING.md), and
[Proxmox](PROXMOX.md) for commands and the limits of each check. No real portal
authentication, AI inference, bidding, payments or seller contact is necessary
for deterministic CI.
