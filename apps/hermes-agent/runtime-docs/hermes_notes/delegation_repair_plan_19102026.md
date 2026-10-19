# Delegation repair plan — 19 October 2026

Status: pending recommendation. No Hermes code, service, or ledger change has
been applied by this investigation.

An independent adversarial review confirmed that malformed CLI and messaging
completion routes can enter raw API ownership resolution. A native legacy
missing-row claim fallback can then select a different profile's ledger.
Ownership failures also requeue before a delivery attempt is counted, causing
an unbounded two-second error loop.

The reviewed plan is saved in the repository at
`docs/hermes-delegation-repair-plan-2026-10-19.md`. It requires trusted originating
ledger and route-kind provenance across dispatch, replay, grouping, claim,
persistence, and settlement; strict durable missing-row denial; bounded retries;
and quarantine that preserves results and survives retention.

Do not fix this by removing the ownership guard, guessing a profile from cached
route hints, rejecting every colon-containing API ID, or automatically dropping
ambiguous completions. Do not perform database recovery from observed cohort
counts alone. Production inventory, backup, migration, and activation remain
separate future work.

The continuation step is to implement and test that plan, then prepare a complete
image-pinned overlay and dry-run recovery for review. Hermes activation and
production ledger writes require the later authorization described in the plan.
