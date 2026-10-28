# Task List

Last reconciled: 2026-10-28. Priority and outstanding outcomes only.
Execution status belongs in the linked task record once a package exists.
A newly confirmed P0/P1 interrupts this order. No completed-work log here.

## Active

- **WS-001** — Complete workspace lifecycle standardization and actual historical
  reconciliation. [Task](../tasks/workspace-lifecycle-standardization/task.md).
- **HERMES-001** — Refresh Hermes main and finish runtime remediation.
  [Task](../tasks/hermes-log-remediation/task.md). Use the native refresh entry;
  rebaseline October accepted source, pin upstream/image, reconcile patches,
  verify disk/review/recovery before separately approved build/activation.
  Preserve the 24-hour log review and seven-day rollback obligations.
- **DEP-001** — Recheck dependency alerts against current source AND deployed
  versions. Do not rebuild retired applications to close obsolete alerts.

## Blocked / monitoring

- **HOST-001** — Tailscale hostname/certificate cutover: interactive sudo required.
  Preserve current hostname/certificate until validated replacement and rollback.
- **CACHE-001** — Establish a fresh deployed cache-baseline epoch using ordinary
  native traffic before evaluation. Restarts reset it; no inferred probe/cohort grant.
  See `docs/spartan-route-cache-experiments.md`.
- **ACT-001** — Complete separately authorized runtime acceptance of prepared
  source/private installation candidates. Preserve exact images, keys, data and
  scoped rollback; no remove-orphans or volume deletion. Historical preparation
  and activation entries do not prove every latest candidate is deployed.
- **ROUTE-001** — Reconcile dashboard-speed parent recreation and finish
  deployed-asset/browser readback. [Task](../tasks/route-dashboard-speed/task.md).
  Preserve candidate/rollback and the recorded `quiet_window_observed:false`;
  no fresh service action or interruption is implied.

## Backlog

- **RECOVERY-001** — Review a better Hermes safecopy-to-Windows connection:
  reliability, startup/recovery and access boundaries.
- **AUDIT-001** — Audit effective private configuration, services and recovery
  after separately authorized activation; public-source audit is not runtime proof.
- **READER-001** — Investigate residual outbound DNS/fetch and executor shutdown
  global deadlines; do not claim cancellation is solved by thread relocation.
- **TTS-001** — Evaluate a fixed Spanish-from-Spain Pocket TTS voice from a
  consented, suitably licensed recording and private versioned safetensors state.
  Preserve offline read-only runtime, no dynamic uploads/provider fallback/rebuild.
