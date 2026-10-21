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

Pending: backend/client implementation, integrated quality and behavioral checks,
independent batch reviews, installation, deployment and live acceptance.


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
