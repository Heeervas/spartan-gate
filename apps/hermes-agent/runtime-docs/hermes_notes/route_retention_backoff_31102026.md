# Route retention failure pacing — 31/10/2026

Status: reviewed correction activated in Route only; Hermes and neighboring services were not restarted. Installed source identity and degraded health were read back after recreation.

A failed retention attempt previously retried after every following successful telemetry write once the batch threshold had been reached. The source candidate now backs off transient failures for 60, 120, 240, then at most 300 seconds and resets on successful maintenance. SQLite corruption, not-a-database, or I/O result codes suspend retention until storage/database repair and process restart. Queue status reports suspension and retry delay; maintenance failure remains degraded rather than appearing successful.

The same storage failures during optional startup retention are now carried into the worker's suspended/degraded state instead of aborting startup. Schema, slot-policy, quota and Pi initialization failures still propagate. Other startup-retention errors still abort startup.

Routing writes still drain. This does not disable other telemetry consumers, repair storage/database damage, or establish a measured token-throughput improvement. Retained history can grow beyond normal maintenance limits while suspended. Do not restart merely to clear suspension without addressing the underlying failure.

Verification: retry-storm and unsafe startup were reproduced before correction; the revised isolated Route Python suite passed 544 tests; TOP quality passed with existing exceptions; independent review passed with notes. A constrained, network-disabled image canary passed startup, storage-failure suspension, continued writes and shutdown before approved Route-only activation.

Bounded live observation: retention remained suspended with one startup failure and no subsequent retries. Remaining snapshot-write and NVMe read failures persisted; this is not full recovery. A short Route sample showed zero physical disk reads, low CPU and substantially lower memory, but host I/O wait remained high. These uncontrolled samples do not prove restored token throughput. Operator storage/database diagnosis remains required; no automatic restart or rollback loop is recommended.
