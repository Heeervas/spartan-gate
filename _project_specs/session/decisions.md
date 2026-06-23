# Decisions

## 2026-06-06

- Browserless-specific findings are deferred to a local gitignored plan.
- Camofox remains in scope.
- Codex account scheduling is an activation mechanism only; after fresh weekly
  telemetry exists for all relevant accounts, selection is driven by required
  weekly burn rate and safety constraints.
- The copied Coordinator system is adapted to Spartan Gate rather than keeping
  an incompatible generic CI workflow.

## 2026-06-08

- Prompt-cache repair for Codex should build on the existing weekday balancer
  and dashboard controls, not replace them.
- The missing pieces are a distinct durable session-cache lease, explicit
  `prompt_cache_key` propagation, cache-token telemetry, and account-level
  invalidation semantics.
- The current in-memory `claimSelectionLease()` / `releaseSelectionLease()`
  counters are request-scoped contention signals, not a usable long-lived cache
  lease model.

## 2026-06-11

- Codex usage analysis should anchor on two sources together: current quota
  state from `codex_usage_snapshots` and request/token/cache activity from
  `routing_log`. Request totals alone are not sufficient for the operator
  question.
- The new analysis UI should be a sibling dashboard route,
  `/dashboard-codex-analysis`, reusing the existing dashboard header-nav and
  bearer-token pattern.
- `codex_usage_snapshots` should remain the latest-known quota-state table. If
  quota trend lines are needed, they should use a separate append-only history
  table rather than changing the semantics of the current snapshot table.
