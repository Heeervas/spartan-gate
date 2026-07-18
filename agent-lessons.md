# Agent Lessons

Last updated: 2026-06-06

## Security

- Tinyproxy filters are regular expressions. Domain policy must escape dots and
  anchor the complete hostname instead of writing raw domains.
- Expiring allowlist metadata must fail closed. Invalid or overflowing
  timestamps must never become permanent access.
- Reader address checks require positive global classification and explicit
  multicast denial; Python's `ipaddress.is_global` can be true for multicast
  addresses.

## Runtime

- Hermes uses s6 initialization. Configuration migrations required by
  supervised services must run before profile reconciliation.
- Browser backends are separate operational modes. Do not claim that an
  inactive Compose profile remains available.

## Tooling

- Helpers that edit multiple files must complete all preflight validation
  before their first write.
- Generic agent-system CI templates must be adapted to the repository before
  they are committed.
