# Route dashboard performance and refresh

Date: 2026-10-20 (Europe/Madrid).
Status: redesign deployed, with the sessions-query correction subsequently
activated and verified. See `route_dashboard_query_regression_20102026.md` for
the live results, rollback image and remaining disk-capacity constraint.

The obsolete private Route image pin has been removed after a backed-up,
validated Compose edit. The main-stack command
`sg up -d --build spartan-route` now builds the repository's Route source and
selects the resulting Compose image. All other effective service settings are
unchanged. This configuration fix is applied. Ensure the normal 5 GB free-space
floor before routine builds; the corrective deployment used a separate,
explicitly approved low-disk exception.

Routing, Codex and Config now share a responsive operator layout. Codex quota
rings show raw used percentages, with capacity-weighted units alongside and
locally ticking reset/expiry counters. A moving countdown is not a fresh
provider observation; check the snapshot and refresh status.

Visible Routing and Codex screens check current data every 15 seconds. Hidden
screens pause scheduled reads. Refresh preserves Routing disclosures and loaded
details, avoids replacing unchanged data, and rejects late responses from
superseded requests or credentials. Request details load on demand.
Config retains explicit saves, unsaved drafts and ETag conflict protection.

Refresh reads the current snapshot; Refresh Live starts or joins a provider
usage refresh. Slot usage checks coordinate with full refreshes. A network
timeout may leave a server operation running; read the latest state before
retrying a write. Failed reads preserve last-good data with a stale/error
message. No mutation is automatically replayed.

Telemetry queries materialize only the selected complete turns, batch account
metadata, and incrementally update today's summary. Heavy file-backed reads use
separate read-only SQLite connections to avoid holding the writer/policy-read
lock. Exact historical counts, filters and latency percentiles remain available;
cold reads and retained-history selection still have a dataset-dependent cost.

Dashboard HTML and allowlisted local assets retain authentication and private
HTTP revalidation. Sensitive API responses are not stored in the browser cache.
No external fonts, image downloads or third-party scripts were added.

Future activation requires the operator's normal deployment procedure after
adequate disk space is available. Validate the live dashboard and freshness
after deployment; source tests and synthetic browser fixtures alone do not
establish that a running image contains the current changes.
