# Security Policy

Spartan Gate is defensive infrastructure for private Hermes-class agents. Please
report suspected vulnerabilities privately and do not include secrets,
credentials, exploit details, or private host data in public issues.

## Supported Versions

| Version | Supported |
| --- | --- |
| `master` | Yes |
| Latest public tag | Yes |
| Older tags | Best effort |

## Reporting A Vulnerability

Use GitHub private vulnerability reporting for this repository when available:

<https://github.com/Heeervas/spartan-gate/security/advisories/new>

If private reporting is unavailable, open a public issue with only a minimal
non-sensitive description and ask for a private contact path. Do not attach
tokens, OAuth files, browser profiles, Caddy hashes, private Compose overrides,
logs with bearer headers, or proof-of-concept exploit details to a public issue.

## Sensitive Data

Treat these as sensitive:

- Provider, Telegram, Discord, GitHub, Raindrop, OAuth, and Codex credentials.
- Browserless and Camofox profile directories.
- `private/`, `.env`, `runtime/`, local certificates, and private host paths.
- ClawRoute logs or databases that include request content or account state.
- Real internal domains, Tailscale hostnames, and LAN exposure details.

## Security Scope

Reports are especially useful when they affect:

- Hermes network isolation.
- Proxy, DNS, Reader, Browserless, Camofox, Caddy, or ClawRoute boundaries.
- Fail-closed behavior for malformed policy, expired policy, or missing auth.
- Secret handling in docs, examples, workflows, Docker images, and logs.

Public files should remain generic and shareable. Local operator material belongs
under ignored `private/` paths.
