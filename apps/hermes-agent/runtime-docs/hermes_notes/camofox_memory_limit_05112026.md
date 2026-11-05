# Camofox memory headroom — applied 2026-11-05

## Authorized local operation

The operator requested a higher Camofox memory limit and immediate recreation
following a memory-cgroup OOM that killed a browser content process on November 4.
Only the local `camofox` override changed: RAM from 2560 MiB to 4 GiB, and combined
RAM-plus-swap from 4 GiB to 6 GiB (up to 2 GiB swap). These are ceilings, not
reservations. The public 2-GiB default is unchanged. The private override remains
ignored and must not be published; this note does not capture private configuration.

Camofox alone was recreated at 12:56 CET using the same existing image, without
pulling or building. The host had approximately 8 GiB available before activation.
Existing admission/eviction/recovery thresholds, 1-GiB shared memory, persistence,
watchdog volume, credentials, proxy/DNS policy and all other controls were retained.
No browser profile inspection, reset, cleanup, stress test or fault injection ran.

## Verification

- Private Compose validation passed. Effective before/after rendering changed
  exactly `camofox.mem_limit` and `camofox.memswap_limit`; all other services and
  topology were identical. The pre-change Compose hash matched the old container.
- New container is healthy; passive browser health returned HTTP 200. Effective
  cgroup `memory.max=4294967296`, `memory.swap.max=2147483648`; initial OOM and
  OOM-kill counters are zero. This is startup acceptance, not a load guarantee.
- Same image and mounts; HostConfig is unchanged except the two memory fields.
  Actual environment, inherited command/entrypoint/user, working directory and
  Compose hash match the rendered configuration and image. An ordered raw Config
  fingerprint was not identical and is not used as semantic equality evidence.
- All 12 neighboring container IDs, start times, restart counts and images stayed
  unchanged. Route remained ready and intentionally telemetry-degraded.
- Btrfs read errors remained 4039, with other error counters zero. Storage is still
  unreliable; this operation does not repair it or alter its contingency policy.
- The bounded pre-recreation container-log window did not retain matching incident
  records. The earlier kernel OOM and old container OOM flag establish the incident;
  no complete historical browser-log preservation is claimed.

The repository quality preflight passed with existing exceptions; private ignored
Compose is outside its source metrics. Effective Compose comparison and live cgroup
readback provide the relevant configuration checks. No application source changed.
Unrelated repository work was preserved; no global cleanup or service restart ran.

## Remaining limits and recovery

The extra 1.5 GiB reduces exposure to the former cap; an unbounded page can still
exhaust 4 GiB. Do not disable OOM/admission protection or promise zero future kills.
Rollback, only if separately requested, is the same two local values restored to
`mem_limit: 2560m` and `memswap_limit: 4g`, followed by Camofox-only recreation.
Keep existing browser data and watchdog state. No new task workspace/package or
reusable policy was needed for this two-value operation. This operator archive is
not mounted into Hermes and its source commit does not publish the private override.
