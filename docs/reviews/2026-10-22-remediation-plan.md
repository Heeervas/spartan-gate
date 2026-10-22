---
plan_id: spartan-gate-audit-remediation-20261022
status: done
updated_at: 2026-10-22
superseded_by: null
---

# Audit remediation and legacy retirement

## Authority and recovery entry

The operator authorized implementation of the reviewed audit on 2026-10-22,
including retirement of Browserless and the legacy ClawRoute application.
Classification: existing-project implementation, authority `complete-target`
for source remediation/retirement and coherent validated local commits.
Deployment, image builds, production changes, data deletion, push/publication
are excluded. Preserve private browser profiles, credentials and databases.

Canonical current state remains `_project_specs/session/current-state.md`;
commands remain `.agents/validation.md`; decisions remain the existing
`_project_specs/session/decisions.md`. This tracked plan is the durable owner
of scope, dependencies and execution checkpoints. Do not create a second STATE.
Read this plan and those owners after session recovery, then inspect Git and
verify candidate bytes before continuing. Do not assume a worker summary is
integrated acceptance.

Starting source: `3b0262b9083e5fbebbcb523e02a99ea98e976d8f`.
Quality baseline remains `75651cc0c5baa1db5a6523b790564988698f2a43`.
Inherited dirty work: `todo-list.md` and three untracked runtime notes named
`hermes_pid_capacity_20102026.md`, `operator_environment_20102026.md`,
`pi_spartan_route_20102026.md`. Never stage, overwrite or reset those inputs.

## Target and preserved contracts

A supported Camofox/Spartan Route stack without Browserless or the legacy
ClawRoute application, with the audit's demonstrated defects corrected and
reproducible validation. Keep compatible configuration names still consumed
by Spartan Route unless an explicit migration is implemented; deleting legacy
code does not authorize deleting shared `config/clawroute` data blindly.
L0/L1 convenience and gated/full network boundaries remain explicit. Retire
CDP-only capabilities honestly rather than claim a Camofox replacement exists.
Never inspect or delete runtime profiles as part of source retirement.

Preserve fail-closed policy, localhost public bindings, Reader global-only GET
exception, provider realm/reasoning identity, no replay after ambiguous delivery,
Camofox isolation, private persistence and operator rollback. No new framework,
language rewrite or new product capability merely to reduce file sizes.

## Audit findings and required disposition

All findings begin OPEN; a plan or removed test is not a fix.

| ID | Priority | Source / defect | Required outcome |
|---|---|---|---|
| F01 | P1 | `scripts/doctor.sh` interpolated render in predictable 0644 temporary | private exclusive scratch, cleanup, synthetic secret regressions |
| F02 | P2 | Route Codex Chat EOF accepted with truncated tool arguments | successful terminal required before tool publication |
| F03 | P2 | old request 401 quarantines new slot credential | immutable attempt identity and guarded mutation |
| F04 | P2 | Native Responses EOF/first failed misclassified | contractual terminal error and accurate telemetry |
| F05 | P2 | provider prepare failure leaks upstream response | acquisition-owned idempotent cleanup |
| F06 | P2 | synchronous SQLite policy reads block loop / errors escape | explicit fail-closed policy snapshot/read boundary |
| F07 | P2 | Reader slow incomplete headers monopolize workers | bounded inbound lifecycle and recovery |
| F08 | P2 | L3 Caddy points to absent Route | surviving tier upstream exists and shares network |
| F09 | P2 | add-port writes before preflight, restart cannot apply mapping | transactional preparation and explicit application |
| F10 | P2 | installer persists selection before validation | candidate preflight and rollback |
| F11 | P2 | edge cookie lacks server-checked expiry | true server TTL preserving HTTP/WS contracts |
| F12 | P2 | proxy audit original URL/userinfo leaks | structured/minimized redaction before truncation |
| F13 | P2 | marker-only patch accepts missing guards | pristine/valid/incompatible states and postconditions |
| F14 | P2 contract | L2/L3 private whitelist not mounted | surviving tier end-to-end policy wiring |
| F15 | P2 assurance | CI misses current stack / mandatory source tests skip | explicit portable/native lanes and exercised gates |

Additional P3 investigations: executor shutdown lifetime, unbounded SSE frame,
Camofox unref test cancellation, PATH fixture isolation, shared monkey-patch test
state, active patch inventory and representative file-backed benchmark.
Shutdown subprocess evidence is not a proven deployed shutdown incident.
Do not claim runtime or private configuration acceptance from these fixtures.

## Dependency waves and ownership

1. **A — durable checkpoint and applicable assurance prerequisites.** Preserve
   audit evidence locally; exercise accepted policy/adapters without moving the
   baseline or weakening thresholds. Install only isolated test/quality
   dependencies needed for the authorized task; no global or image installs.
   Extend policy coverage to owned changed responsibilities with independent
   review. Existing debt remains explicit, not silently excluded.
2. **B — confidentiality and bounded retrieval.** F01, F12, F13 and F07 with
   regression-first tests; disjoint writers. Verify denial and failure cleanup.
3. **C — Route correctness.** F02/F04 then F03 then F05/F06, in one owner thread
   to avoid overlapping state-machine edits. Preserve transport/replay/realm
   contracts. Full Route/Pi suites after focused regressions.
4. **D — supported-shape retirement and transactions.** Inventory consumers;
   remove Browserless/legacy application and their dedicated build/profile/
   routing/secret/helpers/tests/docs. Preserve shared Route config contracts.
   Resolve F08/F14 in surviving shapes, then F09/F10 and F11 with synthetic
   install-to-helper tests and complete public Compose matrix.
5. **E — reproducible gates and justified extraction.** Add current stack CI;
   fix PATH/Camofox harness and mandatory source availability explicitly. Bounded
   SSE/cleanup investigation; extract responsibility only with differential
   evidence. Measure performance with file-backed representative workloads;
   no unmeasured improvement claims.
6. **F — integration acceptance and closeout.** Freeze candidate; collect quality
   metrics immediately before report; run affected consolidated suites and
   public Compose contracts; independent Luna/high review, fixes and renewed
   evidence. Commit only owned coherent files. Update state, decisions,
   changelog, public docs and sanitized Hermes recommendation note. No push.

Workers: GPT-6.1-Sol medium. Independent reviewers: GPT-6-Luna high. Each writer
receives exclusive paths; coordinator owns policy/state/plan/changelog unless
explicitly delegated. Workers do not stage/commit. Coordinator integrates and
commits only after review. No concurrent edits to shared configuration paths.

## Acceptance and rollback

Every defect needs a focused meaningful pre-fix failure, corrected behavior,
negative/failure tests and broader consumers checks. Missing tools/upstream
bundle are INCOMPLETE, not PASS or permission to weaken tests. TOP quality,
behavior and independent review remain distinct. Source retirement requires
search/caller evidence plus surviving install shapes and no active references
to deleted executables. Historical docs may name retired components honestly.

No-build/no-restart constraints remain. Minimum disk 5GB; Hermes build >10GB
would still require separate authorization. CI may describe future image checks
but this execution does not run them. Never run real doctor while F01 is unsafe;
use public fixtures and stubs. Private configuration is not rendered to logs.

Keep each batch reversible without touching runtime data. Config helpers must
preflight every file, stage candidates and rollback on validation/write failure;
never pretend Docker application is an atomic filesystem transaction. Edge token
rollback must not silently resurrect expired sessions. Route code changes should
avoid unnecessary schema migration. Retired source remains recoverable in Git;
profiles/databases remain untouched. The deployed stack remains unchanged until
separately authorized activation.

## Execution log

- 2026-10-22: implementation authorized; baseline/worktree/capacity rechecked.
  Audit had three Sol auditors and two Luna reviewers; both final verdicts
  PASS_WITH_NOTES for the audit/plan, not product acceptance. Durable plan created.
  Initial audit gates: Route460, dashboard41, PiJS23/gateway4, HermesJS18,
  cache29/workstation9 passed. HermesPython305/34skips/1dotenv error, scriptsPATH
  and CamofoxJS cancellation remained non-green; assurance lacked prerequisites.
- Checkpoint after implementation wave: assurance prerequisites executed and
  independently approved (TOP PASS_WITH_EXCEPTION with unchanged baseline).
  Route F02-F06 and bounded SSE implemented: 471 Python/41 JS, Pi23 JS/4 Python;
  independent Luna review PASS_WITH_NOTES, executor shutdown remains a stated
  limitation. Doctor/Reader and Hermes confidentiality/patch guards implemented;
  focused checks passed; review requested real upstream patch-chain evidence.
  Browserless/legacy ClawRoute source retirement and surviving L3 Route topology
  implemented; private data preserved. Signed edge expiry added with stock Caddy
  forward_auth and an isolated bounded verifier; native HTTP/WS canaries passed.
  Existing pinned Hermes source bundle found and full patch-chain canary passed.
  None of these summaries is final integrated acceptance or deployment.
- Integration complete: signed-session migration preflight/rollback, Pi/Camofox
  harness and pinned-source CI provisioning implemented and reviewed. Missing
  quality coverage was expanded additively, then recollected successfully.
  Final reviewer scopes all PASS/PASS_WITH_NOTES; the stale browser guidance and
  ports-helper predicate findings were corrected and independently re-reviewed.
  Coordinator reran consolidated native/portable suites; all commands exited0.
  A final focused benchmark adds three tests (Route474); ports adds one (scripts492).
- Source acceptance: F01–F15 addressed. F08/F14 are satisfied for surviving
  shapes, not by preserving the retired addon. Executor shutdown and Reader
  outbound DNS/fetch global cancellation remain P3 investigation, explicitly
  outside the corrected inbound/dispatch contracts. No claim of all hypothetical
  failure modes eliminated. Architectural extraction was limited to demonstrated
  attempt identity, resource ownership and policy snapshots, not speculative moves.
- Benchmark acceptance: deterministic file-backed fixture,12000rows/1317turns/
  40sessions/~101MB and seven repetitions; base/candidate payload digests match.
  Candidate medians slightly slower; no performance gain or production latency
  guarantee claimed. Live contention/OS-cold timing remains untested.
- Final evidence is under ignored `.agents/remediation-evidence/final/` and
  per-owner directories. It contains local paths and is not publishable verbatim.
  Source/test/docs are tracked; native source archives and dependency caches are
  reproducible from pinned provisioning documented in CI. Keep receipts locally.
- Status `done` refers to the authorized source remediation/retirement, not
  release or activation. Production migration/build/deployment/browser acceptance
  remain unexecuted and separately authorized. Local commit IDs are recorded in
  the final execution receipt; inherited changes remain intentionally uncommitted.
