# Current State

Date: 2026-06-29

## Latest Update

Spartan Route now applies a bounded recovery wait when all usable Codex slots
are temporarily cooling after upstream 5xx or `usage_limit_reached` errors.
Temporary no-slot responses include `Retry-After`, transient 5xx states return
`503 codex_transient_unavailable`, quota exhaustion returns `429 codex_429`,
and forced live usage checks can clear restored slot cooldowns without a
container recreate. `sg-whitelist-domain` now hot-reloads running proxy/DNS
services with `USR1` and recreates only services whose signal reload fails.

## Active Task

Implemented; pending commit.

## Blockers

None recorded.
