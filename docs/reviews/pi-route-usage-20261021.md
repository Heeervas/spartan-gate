# Pi Route usage review and acceptance

Implementation authority: accepted session plan; isolated worktrees; independently
reviewed batches before commits; feature commits may be pushed. Existing unrelated
operator changes are outside this task. No Pi core changes or Hermes recreation.

## Prerequisite batch

An independent GPT-6.1 Sol medium reviewer approved the prerequisite candidate
before commit with no P1/P2 findings. The review inspected the actual policy,
wrapper, package lock and generated report. Immutable baseline
`75651cc0c5baa1db5a6523b790564988698f2a43`, TOP floor, existing Hermes coverage,
thresholds and failure rules are unchanged. The feature expands coverage to Pi
and Route and pins Oxlint 1.80.0.

The exercised preflight returned QUALITY_PASS. This proves prerequisite wiring;
no changed JS/TS existed at that point, so the Oxlint adapter was legitimately
not applicable. Feature acceptance requires fresh collection and reporting on
the integrated production candidate, plus behavioral checks and renewed review.

## Implementation acceptance

Backend and client implementation passed integrated checks and independent batch
reviews. Installation, Route-only deployment and live acceptance are complete.


## Bounded legacy integration allowances

An independent GPT-6.1 Sol medium reviewer inspected the actual backend diff and
fresh measurement output and approved these exact maxima, conditional on the
correctness findings being resolved and renewed review. Created 2026-10-21;
expires 2026-11-20. No allowance applies to new modules or client functions.

| Exact Python selector | Metric | Baseline | Maximum |
| --- | --- | ---: | ---: |
| `app.py` module | logical file lines | 2960 | 2991 |
| `codex.py` module | logical file lines | 3453 | 3468 |
| `telemetry.py` module | logical file lines | 1880 | 1882 |
| `CodexRouter.execute_chat` | cyclomatic complexity | 29 | 35 |
| `CodexRouter.execute_chat` | logical function lines | 105 | 116 |

Rationale: observed-attempt accounting must share the existing result/retry/
cleanup lifecycle to finalize retries and cancellation exactly once while keeping
slot in-flight guards. Policy and storage logic already lives in small separate
modules with no strong findings. Broad extraction of router execution or the
legacy modules would add unrelated routing risk. These allowances preserve the
accepted thresholds, baseline and TOP floor; any increase requires renewed review.


## Backend batch acceptance

Independent re-review approved the revised backend with no remaining P1/P2.
The reviewer checked the immutable storage boundary, attempt finalization and
cleanup, header exclusion, existing auth middleware, exact lease lookup and shared
refresh admission. Review corrections prevent unobserved sessions from displaying
an unrelated global lease and apply backoff to completed refresh cohorts with live
account errors while ignoring stale persisted errors.

The integrated permitted-loopback suite passed 460 Python tests, including 19
focused Pi contracts. The reviewed new controller SHA-256 is
`742d6b097002814f5bbfc7a15cb428dd2f6e927041d0d54ce50aeae1fb8a24cd`;
reviewed test SHA-256 is
`aba902a9386ba20153901cfb8da77130805ceea52aa4b8e20a8a67bdcbcd74aa`.
The partial commit's index quality gate is separate from the pending client
candidate; final feature acceptance still requires all-scope measurement.

## Client and integrated source acceptance

Independent re-review approved the revised client with no remaining P1/P2. The
review reproduced and then verified corrections for generic refresh acknowledgments,
bounded reconnect, polling ownership during provider switches, pending gaps across
reload, and expired/stale quota rendering. The per-session pending-gap marker
contains no origin, baseline or credentials and clears only after the server
acknowledges incomplete coverage. Filesystem failure remains visibly incomplete.

Integrated native Pi loader/cache/usage checks pass, including 15 usage contracts
and the existing cache assertions. All four Python gateway tests and Route's
JavaScript suite pass. The all-scope TOP report returns
`QUALITY_PASS_WITH_EXCEPTION`: all required adapters succeed, no evidence gaps,
and only the five approved legacy backend allowances apply. New client functions
have no strong findings. The reviewed client SHA-256 is
`f73eb48f87d9eb2c48df31a001a06e6c896409eb2f4558040dd78e53c4c5914f`;
core SHA-256 is
`3cb2ec16770144e36d8bf0ab28bb68cef4962e5c14f36a1bba1ba1f4c7ced4e4`.

## Deployed and live acceptance

The final display polish replaces unsupported reset glyphs with plain-text
separators. Its focused native checks, renewed independent review and fresh
all-scope TOP report pass with the same five legacy allowances. Final core
SHA-256: `969af444cb11f980b61d45ef80be06605213dd385ac025a060866627f1de427d`.

The production image uses the existing Route runtime base and copies only the
reviewed backend package. Image identity:
`sha256:39ea71ee2c3a42dc69c9c9753358c0adc5ad1f0296c6df60e06bb1658fc404c7`;
revision label `e9e25285eaaa2674a42fdcc7d5dbb7904ded22c2`. All five changed
production-module hashes match the reviewed source. All 460 Python tests pass
inside its Python 3.12 runtime, using the image's own package and static assets.

Before cutover, the SQLite backup passed quick_check and provider/private Compose
backups were guarded against concurrent edits. The independently reviewed
operation changed only the private Route image pin, validated Compose and used
the normal main-stack helper for a graceful Route-only stop and recreation.
The old process exited cleanly without OOM. The new process is healthy with zero
database failures. Hermes, Camofox and Caddy container identities are unchanged.
The prior image and database remain available for recovery; rollback must not
replace newer live accounting with the older database backup.

The installed Pi files match the reviewed source; the existing provider gained
only its constant marker. Authentication and global Pi settings are unchanged.
Actual native Pi TUI views were exercised in a separate synthetic HTTPS session
at 80 and 100 columns, including pool, accounts, lease and numeric details.
The native footer remains visible, and the plain reset labels remove the missing
glyph seen in the original capture. These are terminal captures rendered for
inspection, not browser screenshots. A fresh Pi process is required after helper
updates because native reload can retain cached `.mjs` imports.

Bounded real Pi RPC acceptance used two short prompts, manual native compaction,
then a fresh process resuming the same persisted session and one final prompt.
Warming and native/provider retries were disabled in the disposable agent only.
Exactly four observed attempts reported usage, with no missing/incomplete attempts
or tracking gaps. Tokens progressed 469 → 969 → 1428 during compaction; resume
preserved 1428 exactly, then the final prompt raised the total to 2068. A separate
read-only SQLite connection confirmed the durable total and attempt counts.
The authenticated live endpoint returned an active session lease and a calibrated
unit estimate; shared refresh admission also succeeded without credit redemption.
These measurements validate observed accounting, not billing-exact consumption.

Operational review also corrected the acceptance script's default retries,
bounded subprocess cleanup and exact target-image guard before execution. Private
logs and sanitized evidence are retained with the operator's local backups.
