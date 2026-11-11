# RAM-only live Route telemetry — 11/11/2026

Status: independently reviewed source activated in Route only. The optional-storage setting remains disabled; Hermes and neighboring services were not restarted.

The Activity live summary uses finalized logical Chat/Responses observations in fixed RAM buckets, not database history. The authenticated live endpoint remains available with optional persisted telemetry disabled. Request/error counts, reported token totals, confirmed token-weighted cache reuse and measured request duration expose coverage, unknown values, process reset and freshness explicitly. No prompts, response content or per-session identifiers are retained. Cache coverage can be incomplete, especially for converted/legacy Chat observations; missing values are not cache misses.

Keep optional persistent telemetry disabled. Live metrics do not repair storage, restore omitted accounting or certify disk health. Existing quota controls and live account checks retain their own authority.

Verification: 561 isolated Python tests, 94 JavaScript tests and TOP quality with existing exceptions passed. Independent candidate review accepted the fixed authorization-recovery behavior. A constrained non-root, network-disabled image canary passed module identities, authentication/no-store, RAM updates with SQL denied and served HTML/asset checks before the operator-approved Route-only recreation. Installed and served identities matched, unauthorized access returned 401, and neighboring container identities were unchanged. The first bounded production window recorded successful traffic and no database, retention, snapshot, traceback or kernel-storage errors.

Rendered desktop/mobile acceptance remains unverified: the approved browser path to the internal dashboard was filtered by existing Tinyproxy policy. No whitelist, alternate browser or credential workaround was introduced. Refresh the Activity page to load the new live summary; history remains intentionally unavailable.
