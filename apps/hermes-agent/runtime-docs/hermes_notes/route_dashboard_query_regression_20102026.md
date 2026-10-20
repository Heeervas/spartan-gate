# Route dashboard query regression — 20 October 2026

Status: source fix applied; runtime activation remains pending.

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
A read-only same-snapshot comparison of the detail-scan correction alone took
11.738 seconds and preserved the baseline payload hash; retained metadata
selection remained the main cost. A read-only experiment copied real metadata
into RAM and indexed only that projection: the complete candidate query took
0.449 seconds, including 0.056 seconds for selection. Projection preparation
and index creation took 11.123 seconds. These projected timings demonstrate the
query mechanism; they are not deployed latency or first-startup guarantees.

Validation: all 441 Route Python tests passed using the existing offline image;
48 telemetry/query tests also passed after adding prompt-preview index coverage.
