# Pi partial quota display correction

Authority: corrective follow-up to the installed Pi/Route feature and the user's
live `quota n/a` report. Continue the existing isolated-worktree, independent
review, commit/push and affected-component installation scope. Preserve unrelated
edits, unknown capacity, authentication and native session accounting.

## Diagnosis and reviewed design

The live sanitized endpoint had valid quota observations but null full-pool
percentages: one routing-enabled slot had unknown identity, while another account
reported only a weekly window. The original footer treated partial coverage as
entirely unavailable. Session tracking was healthy; `0.00u` was a formatting loss
for small positive estimates.

An independent reviewer approved a client-only correction: derive each missing
pool percentage from unique observed accounts with valid capacity and unexpired
windows, weight only that known subset, and label it partial. Details must state
that the full-pool percentage remains unknown and disclose each window's coverage.
Preserve authoritative finite pool percentages and the existing freshness limits.
Small positive estimates must remain visible. No backend, auth, routing, quota
credit or session-ledger change is required.

## Acceptance

The four failure-first presentation contracts reproduced the original defects.
Integrated native cache/usage checks pass, including 19 usage contracts, as do
all four gateway Python tests. Fresh all-scope TOP measurement returns
QUALITY_PASS_WITH_EXCEPTION with no evidence gaps and only the five previously
approved legacy backend allowances. No new allowance or policy change was needed.

Independent candidate review approved the actual diff and report with no P1/P2.
It verified unique observed-account weighting, null-only fallback, explicit partial
coverage, finite authoritative percentages, receipt/timestamp aging, stale flags,
expiry and small-estimate boundaries. Reviewed and installed core SHA-256:
`a1c55947ebbb0b25b848d65db717527089ce4f8c88d9871914d13fd2640faf9f`.

Actual native Pi terminals at 120 and 80 columns rendered the same sanitized live
snapshot before and after the fix. The corrected footer retains the native
status line, shows the two-window bar and partial label without clipping, and
preserves the stale marker. Details disclose different observed capacities for
each window. Captures were rendered for visual comparison; no provider prompt
was sent for this correction.

Only the installed core helper was replaced, with an old-byte backup, concurrent
edit guard and exact readback. A fresh process using that installed helper and
the current authenticated endpoint displayed partial quota successfully. The
previous verification session retained exactly 2068 tokens and four attempts.
Backend image, account configuration, session storage and authentication were
unchanged. Existing Pi processes need a full restart to clear cached helpers.

Publication and checkout cleanup are the final operational steps.
