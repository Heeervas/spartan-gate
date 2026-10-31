# shared-masterv2 — source preparation, not runtime activation

**Status: pending operator adoption.** Source wiring prepares a new minimal
plugin and finite native capacity patch. No image build, service restart,
recreation, profile configuration or live instruction change is established by
this note. This archive is not a runtime guide and must not be mounted.

## Instruction contract

- V2 registers one native SYSTEM section and reads UTF-8
  `get_default_hermes_root()/MASTER.md`, independent of cwd. The standard
  container source is `/opt/data/MASTER.md`; custom `<root>/profiles/<name>`
  homes share `<root>/MASTER.md`. Do not create profile-local MASTER copies.
- There are no profile-name exclusions: `empty`, `workstation`, `ssr`, `ssr-*`
  and `ssr_*` receive common instructions when enabled, without changing their
  memory/history/tool disables.
- Native project/gateway AGENTS discovery remains cwd-based and unchanged.
  If a cwd change leaves profile AGENTS absent, conditionally read the intended
  profile AGENTS directly. Do not replace that recovery with routine MASTER
  reads or assume `AGENTS_<name>.md` is auto-injected.
- Content measured for sizing: **16,416 stripped characters**, **16,576 framed**
  in the one-section fixture. Native ordinary registrations still default to
  **4,000**; the opted-in native maximum is **20,000**, total **24,000** including
  framing. Inventory other enabled sections before adoption. V2 has no private
  cap, truncation, dual transport, provider middleware or reconciliation.
- Missing/empty/unreadable/invalid-UTF8 files warn without source contents or
  exception payloads. Native length, reserved-marker or aggregate failures omit
  the section with warnings, not truncation or refusal of the user's request.
- Native render/freeze boundaries read once; repeated builds reuse that snapshot.
  Resume retains persisted prompt bytes; invalidation (for example compaction)
  rereads current MASTER once. The patch also restores five-digit section lengths
  and accepts the exact renderer-owned active-profile paragraph before the
  timestamp, retaining canonical frame validation and the legacy timestamp shape.

## Prepared integration and safe adoption boundary

The Dockerfile applies the capacity patch at build time. Public Compose mounts
only `shared-masterv2` read-only at `/opt/data/plugins/shared-masterv2`, failing
if its source is missing. New-profile recipes select `shared-masterv2`; use
individual per-profile implementation links, never a whole plugin directory
link. Replace v1 enablement rather than enabling both.

A plugin reload does not install the core patch. Operator adoption requires the
patched image, reviewed private configuration/mount compatibility, explicitly
authorized user recreation, and **fresh sessions** with complete prompt proof.
Do not enable v2 against an old image. No live adoption is claimed here.

The obsolete v1 transport test is retired in source because it asserts removed
middleware/reconciliation behavior. V1 implementation and manifest remain
untouched while the existing read-only mount still consumes them. Payload
retirement is separately pending after recreation and consumer verification;
source preparation is not permission to remove a live mount's files.
