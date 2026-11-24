# Spartan Route daily budget and dashboard redesign (24/11/2026)

Status: **applied** (Route redeployed 24/11/2026), plus one **pending
recommendation** about the host disk.

## Applied

- Each Codex account can now have an optional **daily budget**: the most
  percentage points of the weekly window it may use per 24-hour slice. Slices
  follow the provider's weekly reset clock, not a local timezone.
- No account has a daily budget yet, so routing behaves as before. Once one is
  set, that account pauses for the rest of the slice when the budget is reached
  (reason `daily: daily budget reached`). It also pauses while its weekly
  reading is missing or stale (reason `daily: …`). Other accounts keep
  serving requests.
- A Hermes request that fails because every account is paused is a quota
  decision, not an outage. Check the Accounts page before retrying.
- The Accounts page now shows rings for weekly, today and 5h usage, reset
  credits at the top, and limits inside **Limits & controls**. Activity keeps
  the token under **Dashboard access**.

## Pending recommendation

- The host NVMe drive has one unreadable sector inside the Route database
  (since 31/10/2026). The routing history and the Codex usage history tables
  are damaged. Live routing, limits and slot settings are unaffected. Optional
  telemetry stays disabled. Repair is deferred, and drive health needs an
  operator check.
- Do not rely on Activity's historical totals or usage history until this is
  resolved.
