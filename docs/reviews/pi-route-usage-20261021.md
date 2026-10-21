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
reviews. Installation, deployment and live acceptance remain pending below.


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
