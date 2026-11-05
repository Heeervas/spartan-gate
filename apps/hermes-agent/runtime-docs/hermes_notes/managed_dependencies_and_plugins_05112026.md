# Managed dependencies and writable plugins — 05/11/2026

Status: applied operator-authorized local change; public defaults unchanged.

Hermes-owned `node-search`, `profile-delegate` and `hermes-lcm` now live directly
in native persistent plugin storage. Their individual read-only mounts were
removed after exact source inventory/byte verification. Native runtime-user
create, edit, rename and delete probes passed; all three plugins are enabled.
Gate-owned integration mounts remain read-only.

A cold restart exposed an existing conflict between selected native dependencies
and Gate's image-only admission. Recovery retained the selected environment and
used a passive temporary startup hook. The final reviewed adaptation now uses
explicit managed validate-only admission, with protected image-pinned stage2,
runtime and validator mounts. It runs before native automatic repair/publication;
normal startup performs no dependency install, fallback or generation collection.
Native explicit package-management routes remain available under their policy.

Managed validation checks the real recorded selection/lock, interpreter ABI,
startup imports and core/extra/plugin dependency closure. It respects security
overrides declared by the protected core, including the native Discord/PyNaCl
exception; writable plugin/global settings do not authorize overrides. A stale
PM fingerprint alone is not fatal. Invalid or incompatible declarations still
refuse startup; this is not unrestricted self-modification or full lock replay.

Verification: independent plan/candidate review; 345 Hermes tests with seven
known portable skips; native coverage satisfied across two environments (91
source-consuming cases on host, five assembly cases with exact existing image
dependencies). The literal combined host native command did not pass due missing
host test dependencies. Actual generated launcher, selected module origin,
compiled import, management/direct gateway entry and s6 admission success/fatal
refusal were exercised in isolated no-network synthetic fixtures. Existing live
selection passed a separate read-only pre-adoption check.

Final Hermes-only recreation used the unchanged image, without build or pull.
Health and plugin discovery passed. Mounted code bytes matched the candidate;
core remained non-writable and networks internal-only. Facts, locks and installed
generation inventories were unchanged. Neighbor container identities/start times
matched the fresh adoption baseline. No dependencies were updated or user data
removed. Keep the guarded overlay's image/build checks and explicit migration
procedure when changing images; rollback retains the earlier adaptation.
