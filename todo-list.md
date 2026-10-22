# Task List

Last updated: 2026-10-22

Work from the top down. A newly confirmed P0 or P1 interrupts this order.

## Completed

- [x] Complete the public-source audit, reviewed remediation and Browserless/
  legacy ClawRoute source retirement. Preserve shared Route configuration and
  private data. See `docs/reviews/2026-10-22-remediation-result.md`.
  - Source acceptance is not deployment: private override migration, image
    preparation and live acceptance remain separate below.

- [x] Integrate native Hermes vault tools with Camofox and deploy reviewed images.
  - Preserve native prompts/backends and payment consent; enforce session/tab
    ownership, authenticated secret transport and exact-origin OTP checks.
  - Sequential builds, affected suites, independent adversarial review and real
    synthetic login/payment/address/OTP acceptance passed. Text is redacted;
    native screenshot/vision limitations are explicitly accepted and documented.

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

- [ ] Re-establish the current deployed baseline epoch before evaluating cache
  behavior. Historical September timing is not a current acceptance window.
  - Require real native traffic and the documented recurring-prefix pattern;
    a Route restart resets the baseline epoch. Do not enable probes/cohorts by
    inference. See `docs/spartan-route-cache-experiments.md`.

## Priority 1 — Refresh Hermes Main And Complete Runtime Remediation

- [ ] Run `.github/prompts/hermes-main-refresh.prompt.md`, using
  `plans/2026-07-21-hermes-log-remediation.md` as the remediation baseline.
  - Rebaseline against the accepted October source remediation before edits.
    Recheck capacity rather than use historical disk figures; require more than
    10 GB for a Hermes build and budget backups before database maintenance.
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

- [ ] Recheck dependency alerts against current source and deployed versions.
  - Route source now pins aiohttp 3.14.3; this does not establish the deployed
    version or closure of historical alerts. Validate those after activation.
  - The legacy ClawRoute package/lockfile is retired; do not upgrade or rebuild
    a deleted application to close obsolete alerts.

## Activation — Reviewed Source Remediation

- [ ] Review and migrate existing private Compose/Caddy overrides before activation.
  - Remove retired service references without deleting profiles/databases.
    Wire the isolated edge verifier and three independent keys; migrate static
    listeners with native Caddy validation and retained rollback copies.
  - Prepare only required images, preserving image/code-overlay guards. Build
    and recreate only after preflight; do not use `--remove-orphans` or delete
    volumes as a shortcut. Do not build retired images.
  - Verify live health, auth denial, signed-cookie expiry, WebSocket access,
    Camofox workflows and Hermes/Route behavior. Remote CI and dependency-alert
    closure require their own evidence.
  - Track Reader outbound DNS/fetch and executor shutdown global deadlines as
    residual investigation, not a solved cancellation contract.

## Backlog

- [ ] Future: review a better way to connect the Hermes safecopy to Windows.
  - Evaluate connection reliability, startup/recovery and access boundaries.
    Deferred work; not part of the current migration fixes.

- [ ] Audit effective private configuration, deployed services and recovery
  after separately authorized activation. The completed public-source audit
  does not certify those runtime boundaries.

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
