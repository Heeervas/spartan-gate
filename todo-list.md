# Task List

Last updated: 2026-10-08

Work from the top down. A newly confirmed P0 or P1 interrupts this order.

## Completed

- [x] Restore proxy inheritance through native managed scope and activate
  reviewed image-only dependency admission without rebuilding Hermes.
  - Same image; only Hermes recreated. Configuration, credentials and PM facts
    unchanged; all adapters connected and health passed. Discord's reported
    nonresponse was a mobile-app issue; no Discord migration was applied.
  - Upstream patch comparison and rollback contract recorded in `docs/patches.md`
    and `docs/operations.md`; remaining refresh work below stays separate.

- [x] Activate Camofox 1.15 with reviewed add-on and VNC readiness gates.
  - Cold start, exact analytics mode, malformed-mode protection, open-WebSocket
    recovery, immediate generation-2 protection and production health pass.
  - Superseded local Camofox images are pruned after acceptance; immutable 1.14
    rebuild coordinates remain in `docs/camofox-refresh.md`.

## Blocked

- [ ] Complete the privileged Tailscale hostname and certificate cutover.
  - Blocked on interactive `sudo`. Keep the current hostname and certificate
  active until the replacement is issued, validated, and has a rollback path.

## Monitoring — Astra Cache Baseline

- [ ] Evaluate the deployed v2 baseline after September 13 at 08:40 UTC;
  use September 14 at 08:40 UTC for the 48-hour check.
  - Route-only activation and live health/authentication checks passed on
    September 12. Other services are unchanged. Probes and cohorts remain off.
  - Require real native traffic and the documented recurring-prefix pattern;
    do not bypass the timing gate. A Route restart resets the baseline epoch.
    See `docs/spartan-route-cache-experiments.md`.

## Priority 1 — Refresh Hermes Main And Complete Runtime Remediation

- [ ] Run `.github/prompts/hermes-main-refresh.prompt.md`, using
  `plans/2026-07-21-hermes-log-remediation.md` as the remediation baseline.
  - Rebaseline the plan before edits: the Camofox tab-isolation patch/test now
    exist, but only about 6.8 GB is free; require more than 10 GB for a Hermes
    build and about 16 GB before the planned database backup/VACUUM work.
  - Resolve and pin the exact current upstream Hermes `main` commit and image
    digest. Pre-analyze upstream source, release changes, runtime contracts,
    and plugin surfaces before downloading or rebuilding.
  - Inventory every active Hermes patch, plugin, boot hook, and broker. For
    each, prove whether to remove, simplify, update, archive, upstream, or
    replace it with a plugin; preserve security-boundary behavior with focused
    regression tests.
  - Search current upstream Hermes and Spartan Gate for evidence-backed
    simplifications and optimizations. Review security and operational impact
    before applying any candidate in a small verified batch.
  - Complete the remaining MCP, Tirith, Codex fail-fast, session database,
    transcript durability, gateway recovery, delivery, and log-signal work in
    the remediation plan.
  - Only after patch tests, independent review, and disk gates pass, download
    the selected immutable base, build Hermes once, retain the rollback image,
    deploy, and complete runtime acceptance, the 24-hour log review, and the
    seven-day rollback window. Do not build Browserless or ClawRoute images.

## Priority 2 — Close Dependency Vulnerabilities

- [ ] Remediate the dependency-vulnerability review (no P0/P1 findings).
  - P2: bump Spartan Route from `aiohttp==3.12.14` to `3.14.1`, add focused
    compressed-body and multipart boundary regressions, run the full Spartan
    Route suite, rebuild/recreate only `spartan-route`, and verify the active
    version, health, and closure of all 29 Dependabot alerts.
  - P3: refresh the ClawRoute lockfile from `@hono/node-server` 2.0.6 to a
    non-vulnerable release at or above 2.0.10, then run the Node 22 build and
    tests without building the ClawRoute image.

## Priority 3 — Retire Browserless And Its CDP Integration

- [ ] Remove Browserless as a supported Spartan Gate browser mode after the
  Hermes-main refresh; Camofox remains the supported persistent browser.
  - Supersede `plans/2026-06-06-browserless-review-follow-up.md`. Before the
    first deletion, inventory Google Meet, Chrome DevTools MCP, debugger/profile
    seeding, shared CDP broker, and other CDP-only workflows; assign each a
    Camofox replacement or an explicit removal decision.
  - Remove Browserless services/profiles/build context, edge routes and ports,
    secrets, mounts, permission exceptions, brokers, helpers, tests, examples,
    and documentation. Remove or reclassify Hermes CDP patches only after
    proving no surviving non-Browserless contract needs them.
  - Never inspect or delete Browserless runtime profiles. Document optional
    local backup/migration, then leave profile cleanup as a separate explicit
    operator action.
  - Update layer/install/doctor/alias contracts, validate every Compose shape,
    run affected Hermes and script suites, and complete Camofox runtime
    acceptance. Do not build Browserless.

## Backlog

- [ ] Discovery: evaluate a fixed Spanish-from-Spain Pocket TTS voice from a
  consented, suitably licensed recording, exported to a private versioned
  `.safetensors` voice state. Preserve the offline, read-only runtime with no
  dynamic voice uploads, fallback provider, or Hermes rebuild.

## Recently Completed

- [x] Review and commit uncommitted Hermes/Camofox and Reader cleanup.
- [x] Implement Spartan Gate layers/addons/Hermes mode install model.
- [x] Complete final validation and independent code review.
- [x] Resolve uncommitted review findings outside Browserless.
- [x] Optimize active Codex account rotation by weekly reset urgency.
- [x] Install the Coordinator agent system from project-agent-installer.
