# Security policy

## Supported versions

AuctionRadar is a pre-release, source-available project. Security fixes target
the current `main`; no older release line has a long-term support commitment.
Neither a passing CI run nor publication of the source certifies a deployment
as production-ready. Review [self-hosting guidance](docs/SELF_HOSTING.md) before
exposing an instance or uploading a certificate.

## Report privately

**Do not open a public issue or PR with exploit details, credentials, share
tokens, personal data, or production state.**

- If GitHub shows **Security → Report a vulnerability**, use the repository's
  [private reporting form](https://github.com/jojoyaco/auctionradar/security/advisories/new).
  That feature must be enabled and verified by a maintainer when the repository
  becomes public; its availability must not be assumed for a private repository.
- Otherwise, use the contact email published on the
  [owning organization's GitHub profile](https://github.com/jojoyaco) to request
  private coordination. Start with a brief, non-sensitive summary and obtain
  agreement on how to exchange detailed evidence. Do not send private keys,
  account passwords, certificates, or a production database.

Include the affected commit, minimal synthetic reproduction, expected and actual
behavior, impact, and any relevant deployment conditions. Provide only the data
needed to reproduce the issue. If a real credential was exposed, revoke/rotate
it at the issuer rather than assuming deleting a file or log makes it safe.

Maintainers will coordinate investigation, a fix, and disclosure privately where
possible. No response-time, bounty, or support SLA is promised. The contribution
issue-first rule permits this private security coordination instead of a public
vulnerability issue.

## Maintainer publication checklist

- Verify that the organization contact address is monitored and private reporting
  is enabled/usable before inviting public vulnerability reports.
- Keep dependency alerts, secret scanning and CI checks enabled as supported by
  the repository's plan. Do not publish raw scanner reports containing secrets.
- Preserve disclosure-sensitive reports and reproduction data outside public Git
  history, issues, Actions logs and downloadable artifacts.
- Acknowledge the limits of synthetic AI/portal tests and simulated PVE checks;
  do not claim a real deployment was tested when it was not.

See [CONTRIBUTING.md](CONTRIBUTING.md#security-and-sensitive-data) for contribution
handling and [CI](docs/CI.md) for automated checks and billing limits.
