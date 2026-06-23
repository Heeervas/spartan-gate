# Current State

Date: 2026-06-14

## Latest Update

Created `plans/2026-06-14-hermes-pangocairo-support.md` for a low-complexity
Hermes image change that adds the Debian runtime package
`libpangocairo-1.0-0` to `apps/hermes-agent/Dockerfile`.

Next step: user confirmation, then implement the single-package APT list edit
and validate with `docker compose -f infra/compose/compose.yml config --quiet`
plus an optional targeted `docker compose -f infra/compose/compose.yml build hermes`.

Blockers: none for planning; implementation is pending confirmation.

## Active Task

Refine the Codex usage analysis plan so it compares current Codex limit pressure
against ClawRoute token and cache activity, and scope a new dashboard analysis
page for ClawRoute operators.

## Status

The refined plan is captured in `plans/2026-06-11-codex-usage-telemetry-analysis.md`.
The current live evidence says the recent request/token totals are down versus
the previous 7-day and 5-day windows, but that alone does not answer the user
question because they mean Codex limits, not raw request volume.

The refined plan now treats `codex_usage_snapshots` as the immediate source of
current five-hour and weekly quota pressure, treats `routing_log` as the
request/token/cache source of truth, and explicitly scopes a new
`/dashboard-codex-analysis` page linked from the existing dashboard header.

The plan also records the current telemetry gaps: `codex_usage_snapshots` is a
latest-only table rather than a historical series, `routing_log` does not yet
persist requested reasoning effort, and request rows are not yet attributable
to a selected Codex account hash or slot.

## Blockers

- Historical quota-trend analysis is blocked until Codex snapshot history is
  stored separately from the latest-only snapshot table.
- Historical reasoning-effort comparison is blocked until `routing_log`
  persists normalized reasoning metadata.
- Per-account quota-vs-token attribution is blocked until request rows persist
  the selected Codex account hash or slot identity.
