# Source remediation acceptance — 2026-10-22

## Outcome and scope

Source implementation of the audit remediation and authorized Browserless/legacy
ClawRoute retirement is accepted locally. This is not a release, deployment or
private-configuration certification. The running stack, credentials, databases
and browser profiles were not migrated or deleted. No image build or push ran.

Plan: [durable execution plan](2026-10-22-remediation-plan.md).
State/authority: `_project_specs/session/current-state.md`; commands and pinned
native inputs: `.agents/validation.md` and `.github/workflows/ci.yml`.

## Local source commits

- `0741d55`: scoped assurance prerequisites and executable quality path.
- `9516b64`: Route attempt identity, terminal/cleanup/policy boundaries, Pi thread
  isolation and bounded SSE; representative benchmark and regression tests.
- `193f83f`: private doctor diagnostics, Reader inbound lifecycle, Hermes audit
  minimization and stage2 patch postconditions.
- `50b0967`: source retirement, surviving installation shapes, signed edge
  verifier, transactional helpers/migration, native CI and operator guidance.

The following documentation-only closeout commit records this receipt, state,
plan, decisions, changelog and sanitized Hermes note. No inherited todo-list or
three pre-existing untracked runtime notes are included in these commits.

## Candidate-bound verification

Coordinator reran the consolidated candidate after workers completed. The last
source deltas (ports regression and benchmark) received focused independent
review and fresh full affected-suite runs. Raw results and candidate manifests
are retained locally under ignored `.agents/remediation-evidence/`; do not copy
those files publicly without sanitizing paths. No stale intermediate failure is
counted as a final pass.

| Gate | Result |
|---|---|
| Route Python / JavaScript | 474 / 41 passed |
| Scripts | 492 passed, zero skips |
| Hermes Python | 310 tests, one root-authority fixture skip |
| Mandatory native Hermes | 75 passed, zero skips |
| Reader | 26 passed |
| Pi Python / JavaScript | 4 / 23 passed |
| Camofox Python / JavaScript | 28 / 53 passed, zero cancellations/skips |
| Mandatory native Camofox | 53 passed; overlaps the preceding JS suite |
| Native Caddy/verifier | 5 passed with real isolated HTTP/WS fixtures |
| Public Compose, syntax and diff | passed |
| Deterministic assurance | TOP QUALITY_PASS_WITH_EXCEPTION |

The five bounded Route exceptions predate this task. Baseline
`75651cc0c5baa1db5a6523b790564988698f2a43`, thresholds and adapters were not weakened.
Policy coverage was expanded. Shell/Compose/Caddy semantics are covered by native
checks, not falsely attributed to Python AST or JavaScript complexity metrics.
Retired Hermes JavaScript tests have no surviving suite: zero tests is not PASS
coverage. Remote GitHub Actions execution is not claimed.

Sol medium implemented independent owned areas. Luna high reviewed assurance,
Route, doctor/Reader/Hermes guards, retirement/edge security and integration.
Initial blockers (native source evidence, quality applicability, stale retirement
guidance) were resolved and re-reviewed. Final verdicts were PASS or
PASS_WITH_NOTES, scoped to source, evidence and documented residuals. The minor
ports predicate defect was corrected, regression-tested and independently passed.

## Performance and residuals

The file-backed synthetic fixture contains 12,000 rows across 1,317 turns and 40
sessions with wide contexts, approximately 101 MB, and seven repetitions.
Baseline/candidate fixture and payload digests match. Candidate median timings
were slightly slower; no speed improvement, production latency guarantee or
contention/OS-cold acceptance is claimed.

Executor-thread shutdown and outbound Reader DNS/fetch cancellation still lack
proven hard global deadlines. The implemented guarantees concern request
admission, response ownership, event-loop responsiveness and policy safety, not
magically cancelling blocking threads. These P3 investigation limits remain.

## Activation boundary and recovery

Do not run a stack-wide activation as an assumed next step. Existing private
Compose/Caddy overrides can still contain retired services or static-cookie
listeners. Activation needs separately authorized migration, verifier wiring,
three independent keys, image preparation and live browser/HTTP/WS acceptance.
Use the documented offline migration and candidate validation; do not delete
profiles, remove orphans or change permissions on retired data automatically.

Source rollback is available through the local commits; rollback of signed edge
policy must not silently restore unlimited cookie replay. Retired source is in
Git history, not a reason to erase runtime stores. Existing Route configuration
names and model aliases are retained deliberately for compatibility.
