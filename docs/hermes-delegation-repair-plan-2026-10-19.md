# Hermes delegation repair: reviewed implementation plan

Status: pending recommendation; no Hermes code, database, configuration, or
service changes have been applied. Prepared 2026-10-19 after an independent
GPT-6.1 Sol adversarial review. Route remediation is a separate change.

## Evidence and success criteria

The gateway repeatedly rejects a raw completion without a unique served session
owner, then requeues it every two seconds. The observed CLI cohort contains 21
terminal pending records with zero delivery attempts and a CLI batch label that
is not a session. A separate malformed messaging cohort contains 39 pending
records; its route label also matches a session in a different profile. Counts
are investigation-time observations, not migration selectors.

The native classifier treats otherwise unstructured session keys as raw API
identities. The Spartan ownership patch scans session stores and selects a
profile before durable claims. Native claim logic accepts a missing row for
legacy compatibility. Consequently, a malformed event can find a session in a
different profile, pass the absent-row claim fallback, and persist there while
its original ledger remains pending. Delivery was not proven to have occurred
across profiles; this is a confirmed reachable code path, not a measured leak.

Success means that every durable event stays bound to its originating ledger,
valid results are delivered once, CLI work remains queryable by its legitimate
consumer, and invalid or ambiguous events cannot cause a tight retry loop or
cross-profile delivery. Preserve fail-closed ownership checks and native API
delivery without an unsolicited model wake turn.

## Adversarial findings incorporated

- P1: global raw-session lookup plus the native absent-row claim fallback can
  select the wrong profile and settle no row in the originating ledger.
- P1: route-only grouping scopes all siblings using the first event; identical
  client-chosen IDs across profiles can coalesce.
- P2: ownership errors precede claims, so unconditional requeue never consumes
  the existing retry budget.
- P2: live producers, interim failure notices, replay, and orphan recovery lack
  independently trusted originating-ledger provenance.
- P2: replay age, attempt handling, and retention can drop or delete pending
  evidence; adding quarantine without changing those paths is insufficient.
- Current fixture tests omit the native classifier, watcher, replay, producer,
  batching, and missing-row claim behavior. Seven existing scope tests pass but
  do not establish these lifecycle guarantees.

Relevant implementation surfaces are the Spartan asynchronous completion scope
patch and native `gateway/run_notifications.py`, `gateway/wake.py`, and
`tools/async_delegation.py`. Re-read the pinned deployed source before editing;
do not assume line numbers or upstream anchors remain unchanged.

## Implementation sequence

1. **Reproduce against native-shaped source.** Add fail-closed regressions for
   CLI misclassification, a malformed messaging event matching another profile,
   and a durable event whose target ledger lacks its delegation row. Exercise
   the actual watcher and ledger helpers, not only the reduced scope fixture.

2. **Capture trusted provenance at dispatch.** Introduce an internal completion
   envelope containing a served-profile/ledger descriptor and route kind
   (`api`, `messaging`, or `cli`). Create these fields from native dispatch
   context, outside user-supplied or serialized event metadata. Cover terminal
   results, interim task-failure notices, batch production, and in-memory
   fallback after a persistence failure. Reconstruct provenance during replay
   and orphan offering from the ledger actually opened, replacing any event
   claims. Resolve descriptors through the served-profile registry; never open
   an arbitrary path supplied by an event.

3. **Make that ledger authoritative.** Perform durable claim, target proof,
   persistence, and settlement in the proven origin scope. Match delegation and
   immutable dispatch/session identity there. Missing rows fail closed for
   provenance-bearing durable events. Keep an explicit, narrowly admitted
   non-durable compatibility path; do not globally remove native compatibility
   without identifying its consumers. Interim notices never claim or settle a
   final batch row.

4. **Classify routes positively.** API admission requires trusted API origin and
   its session in the origin ledger. Preserve arbitrary legitimate API session
   strings, including colons; prefix rejection alone is not an adequate fix.
   Canonical messaging routes retain strict native parsing and profile checks.
   CLI completions remain CLI and cannot enter API persistence or messaging
   wake paths. Do not reconstruct malformed legacy routes by guessing.

5. **Quarantine ambiguity durably.** Add a per-ledger quarantine table keyed by
   delegation identity with reason, safe source metadata, creation/update times,
   and remediation status. Do not duplicate prompts or results. Exclude active
   quarantine from gateway replay, orphan offering, replay-age/attempt dropping,
   and destructive record pruning. Preserve original results and authorized
   CLI/TUI retrieval. Resolve entries only through proven reconciliation or an
   explicit operator action; do not mark ambiguous results delivered or dropped.

6. **Bound temporary failures.** Ownership/route ambiguity quarantines once.
   Temporary database or transport failures use persisted capped exponential
   backoff with jitter, starting at 5 seconds and capped at 5 minutes. Deferred
   admission does not consume delivery attempts; actual admitted delivery
   failures retain the native bounded attempt policy. Rate-limit repeated
   diagnostics to one per ledger/delegation/reason per five minutes. Ensure offer
   tracking agrees with quarantine, deferral, successful delivery, and shutdown.

7. **Separate batches and deduplication by origin.** Include trusted ledger
   identity in grouping, offer, and delivery-idempotency keys. Validate every
   sibling's ledger and destination before any claim. Preserve native partial
   delivery semantics. A conflicting sibling must neither be delivered with nor
   acknowledge a valid sibling.

8. **Prepare recovery separately from deployment.** Provide a metadata-only
   inventory/dry run classifying exact rows by ledger and available route proof.
   Preflight all affected databases and schemas before the first write, then
   take SQLite-aware backups with integrity checks and a recovery manifest.
   Quarantine ambiguous cohorts transactionally in their own ledgers; preserve
   result and task payloads. Never use the observed counts or a broad label as
   the sole selector. Supply explicit inspect, reconcile, and resume operations;
   do not automatically replay the malformed cohorts.

## Verification and activation gates

Cover legitimate colon-containing API IDs, canonical messaging, CLI exclusion,
missing and duplicate sessions, duplicate delegation IDs across profiles,
forged serialized provenance, absent-row claims, mixed-owner groups, interim
notices, producer persistence failure, replay/orphan/offer lifecycle, claim
races, duplicate acceptance, restart-persistent backoff, removed profiles,
temporary unreadable stores, and quarantine surviving normal retention.
Patches must remain deterministic, idempotent, and fail on source drift.

Run the affected native-source integration tests and full Hermes patch groups;
render active public/private Compose shapes for any overlay changes. Do not
weaken network, profile, database ownership, or credential boundaries to make
tests pass. Do not inspect browser profiles or run user-directed messages as
acceptance traffic.

Prefer an existing-image overlay only if all producer, replay, claim, schema,
and watcher changes can be deployed together with a tested rollback. Pin the
image and every original/replacement source fingerprint; generate and validate
all replacements before activation. Check composition with existing overlays,
read-only mounts, and the native launcher import path. Run the drift guard before
supervised reconciliation. A gateway-only overlay is insufficient. If this
cannot be made atomic and reviewable, prepare a future normal image build.

Before activation, restore normal disk gates or obtain a fresh operation-specific
exception. The current exception covers Route source edits and bounded tests;
it does not authorize Hermes migration, image work, or deployment. Preserve
rollback receipts and database backups; never blindly restore an old database
over new accepted work. Rollback must account for the additive quarantine schema
and prevent the old gateway from replaying quarantined records.

Runtime acceptance uses synthetic, operator-local destinations: one API result
per served profile, a canonical messaging delivery, CLI exclusion, and explicit
cross-profile denial. Observe aggregate dispositions and bounded logs for at
least 30 minutes, including a graceful restart and orphan sweep. Stop activation
on duplicate delivery, wrong-profile persistence, growing unbounded pending
queues, or lost quarantine evidence.

## Continuation request

"Implement the reviewed Hermes delegation repair plan in
`docs/hermes-delegation-repair-plan-2026-10-19.md` using `.prompts/rev-impl.md`.
Revalidate the deployed native source and ledger inventory, implement and test
the provenance and quarantine boundary first, then prepare the complete guarded
overlay and dry-run recovery. Do not activate the overlay or mutate production
ledgers until the concrete deployment and recovery artifacts have been reviewed
and I authorize activation. Preserve existing unrelated changes and restore the
normal disk prerequisites before image or migration work."
