# Current State

Date: 2026-06-28

## Latest Update

Spartan Route Codex timeout handling now matches ClawRoute semantics:
`CODEX_REQUEST_TIMEOUT_MS` bounds only the wait for response headers, not the
entire streamed response body. Local stream read timeouts/errors remain explicit
retryable `codex_stream_timeout` / `codex_stream_error` failures but no longer
put the selected slot into generic transient 5xx cooldown. True upstream server
errors still cool the affected slot/account and all-transient pools now return
sanitized `codex_transient_unavailable` retry metadata. The cache-stall guard
remains diagnostic-only with no active request blocking.

## Active Task

None recorded.

## Blockers

None recorded.
