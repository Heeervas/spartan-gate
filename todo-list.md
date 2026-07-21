# Task List

Last updated: 2026-07-21

## In Progress

- [ ] Execute the Hermes recurring-log remediation plan in
  `plans/2026-07-21-hermes-log-remediation.md`:
  - restore and harden the `measure_plan` and `gtm` MCP runtimes;
  - repair Tirith auto-download with only the required GitHub release-asset
    egress;
  - make Spartan Route `codex_429` exhaustion fail immediately;
  - back up, verify, conditionally repair, prune to 90 days, and optimize every
    active Hermes session database offline;
  - make transcript persistence failures observable and recoverable;
  - validate gateway/cron recovery without duplicate delivery and retain
    fail-closed delegation behavior;
  - reduce expected log noise without suppressing security blocks;
  - perform one Hermes rebuild only after reconciling the missing Camofox
    tab-isolation patch/test and reaching the disk-space gate;
  - complete runtime acceptance, a 24-hour log review, and the seven-day
    rollback window before proposing backup/image cleanup.

## Backlog

- [ ] Review Browserless hardening follow-ups and convert publishable items into public issues or tracked docs.
- [ ] Discovery: evaluate a fixed Spanish-from-Spain Pocket TTS voice from a
  consented, suitably licensed recording, exported to a private versioned
  `.safetensors` voice state. Preserve the offline, read-only runtime with no
  dynamic voice uploads, fallback provider, or Hermes rebuild.

## Done
- [x] Review and commit uncommitted Hermes/Camofox and Reader cleanup.

- [x] Implement Spartan Gate layers/addons/Hermes mode install model.

- [x] Complete final validation and independent code review.

- [x] Resolve uncommitted review findings outside Browserless.
- [x] Optimize active Codex account rotation by weekly reset urgency.
- [x] Install the Coordinator agent system from project-agent-installer.
