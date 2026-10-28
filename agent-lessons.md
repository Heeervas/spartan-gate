# Agent Lessons

Last updated: 2026-07-19

## Security

- **SG-L001** — Tinyproxy filters are regular expressions. Domain policy must escape dots and
  anchor the complete hostname instead of writing raw domains.
- **SG-L002** — Expiring allowlist metadata must fail closed. Invalid or overflowing
  timestamps must never become permanent access.
- **SG-L003** — Reader address checks require positive global classification and explicit
  multicast denial; Python's `ipaddress.is_global` can be true for multicast
  addresses.
- **SG-L004** — Private permission repair must preflight every target before the first chmod,
  never traverse credential stores, and preserve Browserless's required
  UID-999/configured-GID access with mode 0770 rather than applying blanket
  owner-only directories.

## Runtime

- **SG-L005** — Codex usage windows are identified by `limit_window_seconds`, not by primary/
  secondary position: 18,000 seconds is five-hour and 604,800 seconds is
  weekly. Use positional fallback only when duration is absent; a weekly-only
  payload is valid.
- **SG-L006** — Polling endpoints must read incremental rollups, never scan and parse retained
  `routing_log` rows on every request.
- **SG-L007** — Shared single-flight tasks require shielded waiters, controller-owned child
  `cancel()` plus `gather()`, and generation-guarded publication so an old
  controller or `finally` cannot overwrite newer state.
- **SG-L008** — Hermes uses s6 initialization. Configuration migrations required by
  supervised services must run before profile reconciliation.
- **SG-L009** — The native Hermes launcher replaces `PYTHONPATH` with its project and site
  directories. Shared policy helpers must be importable from that project path;
  boot probes must not inject extra paths that hide missing runtime imports.
- **SG-L010** — Browser backends are separate operational modes. Do not claim that an
  inactive Compose profile remains available.
- **SG-L011** — A log-retention boundary must cover overlay-only service definitions as well
  as the base stack. Sending audit output to bounded container stderr avoids a
  second unrotated file lifecycle.
- **SG-L012** — Telemetry must remain subordinate to request handling: bound admission, keep
  workers alive across write failures, bound shutdown drain time, and prune
  only rebuildable routing history in one transaction with its rollups.
- **SG-L013** — A successful live upstream response must not become HTTP 500 because its
  telemetry snapshot or derived-cache read failed. Return sanitized partial
  metadata, keep inference health ready-but-degraded, and reserve structured
  503 responses for endpoints that have no non-telemetry result to serve.
- **SG-L014** — SQLite backup reads can materialize missing WAL/shared-memory sidecars. An
  exact-file ownership migration must re-inventory the managed family after
  those reads before applying metadata repair.

## Tooling

- **SG-L015** — Docker export with `unpack=false` defers runtime extraction, not its disk
  cost. Budget builder/runtime snapshot coexistence before the first container;
  cancelling its client can leave daemon-side extraction running. Retire only
  authorized, precisely identified build cache before that capacity gate.
- **SG-L016** — Helpers that edit multiple files must complete all preflight validation
  before their first write.
- **SG-L017** — Generic agent-system CI templates must be adapted to the repository before
  they are committed.
