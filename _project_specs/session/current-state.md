# Current State

Date: 2026-06-27

## Latest Update

Spartan Route v1 now classifies Codex stream read stalls as explicit transport
failures instead of successful empty turns. Streaming Chat Completions emit an
SSE error plus `[DONE]`; streaming Responses terminate with `response.failed`;
non-streaming Codex read failures return retryable `502` errors. Telemetry
stores stable stream error codes, preserves split first-response versus total
duration, keeps usage-missing rows out of cache-token totals, and exposes
diagnostic-only low-cache stream failure state without active blocking. The
dashboard/telemetry enrichment remains in place, including expandable request
rows, account/slot cards, reset countdowns, active cache leases, authenticated
slot paths, and persistent slot enable/disable controls. Quota-unit and
session-cost estimates remain deferred to Spartan Route v2.

## Active Task

None recorded.

## Blockers

None recorded.
