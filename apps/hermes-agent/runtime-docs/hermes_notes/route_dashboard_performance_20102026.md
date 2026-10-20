# Route dashboard performance and refresh

Date: 2026-10-20 (Europe/Madrid).
Status: source changes applied; runtime activation remains pending. No service
was rebuilt or restarted during this work.

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

Activation requires the operator's normal deployment procedure after adequate
disk space is available. Validate the live dashboard and freshness after
deployment; source tests and synthetic browser fixtures do not establish that
the currently running image contains these changes.
