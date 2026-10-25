# Route protocol and durable-policy boundaries

The API/operator overview is in [`docs/spartan-route.md`](../../docs/spartan-route.md).

## Completion and cleanup

Codex Chat compatibility does not publish buffered tool calls until a terminal
response is seen and function arguments are valid JSON objects. EOF with partial
output is an incomplete upstream result, not a successful completion; it is not
replayed after ambiguous delivery. Existing text-only `response.incomplete`
length-limit compatibility is retained. Native Responses streams classify the
first terminal event as well as later events. EOF after material output emits
`response.failed` with `codex_incomplete_response` and marks telemetry incomplete.
HTTP SSE events have a 4 MiB budget including delimiters, matching the WebSocket
frame budget; overflow raises an explicit transport failure rather than silently
truncating a frame.

Provider response ownership starts at acquisition, not the first generator yield.
Downstream prepare failures, cancellation and normal finalization release the
resource through idempotent cleanup. Cache observations finalize once. Codex
outcomes and affinity publication are fenced to the selected credential identity;
a delayed rejection cannot quarantine or cool down its replacement credential.

## Standard Codex replay scheduling

Opaque receipts retain their existing format, key and integrity checks. Every
receipt is validated; the last input receipt supplies advisory issuer affinity.
Selection first enforces operational/model/effort/retry eligibility, then prefers
that issuer before ordinary lease/load ordering. If no matching issuer is eligible,
use the full eligible pool. Mixed-issuer histories are valid; raw ciphertext and
tool pairings remain unchanged. New receipts identify the actual attempt. This
does not authorize replay after committed output or ambiguous delivery, and does
not establish lossless cross-account hidden-reasoning reuse. See the
[standard-client contract](../../docs/pi-native-responses.md). Source-only until
separately activated.

## Durable policy versus observations

SQLite `codex_slot_overrides` is durable policy, not discardable telemetry. Route
is its sole supported writer. Startup loads its validated snapshot; invalid policy
refuses startup. Operator mutations run off the event loop and publish a complete
snapshot only after commit. Rejected writes roll back and retain the prior
committed snapshot, including disables. Hot-path selection reads that snapshot
without the telemetry connection lock. Direct external database edits are not a
supported live update mechanism: restart Route to load them. No override is
silently discarded on a telemetry read failure.

Missing policy produces structured HTTP 503 `slot_policy_unavailable` before
Codex dispatch. Health distinguishes this (`policyReady=false`, `ok=false`, 503)
from unavailable observational telemetry (ready but degraded, HTTP 200). An intact
committed snapshot remains authoritative during a later telemetry/database fault.
Pi registration, correlation reads and observed-attempt writes run off-loop;
observation failures still cannot replace an inference result.

These are source contracts verified with disposable fixtures, not claims about
an installed service. No schema migration or private-data operation is required.
Executor-thread shutdown lifetime remains a separate unresolved investigation;
`to_thread` prevents event-loop blocking but does not terminate a blocked thread.
