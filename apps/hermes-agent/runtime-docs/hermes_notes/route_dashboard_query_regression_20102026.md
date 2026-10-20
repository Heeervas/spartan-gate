# Route dashboard query regression — 20 October 2026

Status: source fix applied; runtime activation remains pending.

The dashboard now publishes sessions and summary independently. A timed-out
read waits for the next scheduled poll instead of immediately replaying work
that may still be running on the server. Returning to a visible tab reuses an
active background read. The five-second deadline and stale-response guards
remain in place.

The live-session detail join repeated a session index scan for each selected
turn. The default 30-session, 20-turn view amplified work that the earlier
10-session, 5-turn synthetic benchmark understated. The corrected reader first
selects turn metadata, then scans each selected session once and materializes
only requests from those complete turns. Both phases share one read snapshot.
A covering index avoids loading large retained context payloads during turn
selection and filtering. Existing previews are indexed without truncation;
legacy preview size can increase the first-startup index space and time.
Index creation happens during Route schema initialization; runtime activation requires an authorized restart and disk budget.
Filters, exact matching counts, request details and routing behavior are
preserved. No inference, account or network policy changes are required.

A deterministic SQLite instruction-count regression covers long sessions;
focused tests preserve complete-turn metrics and incremental summary behavior.
Source-only validation uses existing dependencies without a build or restart.

The live authenticated sessions endpoint took 18.084 seconds before this fix.
The exact browser query (30 sessions, 12 turns) took 13.540 seconds. Health
remained responsive during the slow query, and active logs showed no errors.
The reported inference slowdown has not been independently attributed to a
specific inference-path defect; this correction removes measured dashboard
database work and duplicate timeout requests.

A bounded comparison of 200 recent Codex requests on each side of deployment
found median routing preparation at 36 ms before and 32 ms after (95th percentile
65 ms and 62 ms). Median upstream response headers moved from 1.030 to 1.135
seconds. For requests carrying first-output timing, median upstream first output
moved from 5.320 to 9.510 seconds. The latter samples differ in workload: median
input tokens increased and output tokens more than doubled. These observations
locate the recorded increase after upstream dispatch; they do not establish a
provider defect or a controlled before/after regression. They do not show an
increase in the measured router-preparation phase.
A read-only same-snapshot comparison of the detail-scan correction alone took
11.738 seconds and preserved the baseline payload hash; retained metadata
selection remained the main cost. A read-only experiment copied real metadata
into RAM and indexed only that projection: the complete candidate query took
0.449 seconds, including 0.056 seconds for selection. Projection preparation
and index creation took 11.123 seconds. These projected timings demonstrate the
query mechanism; they are not deployed latency or first-startup guarantees.

Validation: the final integrated 441 Route Python tests and 41 dashboard
JavaScript tests passed using existing dependencies. Independent review checked
covering-index plans, legacy migration, snapshot consistency and bounded work.
