# Pi usage through Spartan Route

Date: 2026-10-21 (Europe/Madrid).
Status: implemented, installed and live-verified; existing Pi processes need restart.

Pi now has a minimal native Route quota status with pool, enabled-account and
exact session lease views. The footer shows cumulative session tokens and an
approximate calibrated quota-unit estimate. Use `/spartan-usage` for details or
`/spartan-usage view pool|accounts|lease` to change the view. Restart the Pi process
after updates so cached helper imports cannot retain older code.

Route owns a durable immutable session baseline plus observed new attempts,
independent of routing-history retention. Native compaction and resume retain
the cumulative total. Native forks register their own copied recorded history;
subagents remain separate sessions. No Pi core or Hermes change is required.

The Route-only image replacement passed 460 tests in its actual runtime, and
Hermes, Camofox and Caddy were not recreated. A bounded real Pi test confirmed
compaction increased usage, reopening preserved it exactly, and a subsequent
turn added new usage. All four test attempts exposed provider usage, with no
tracking gaps. The live quota estimate and exact session lease were verified.

This is observed usage, not a billing ledger: provider omissions, failed writes
and process crashes can leave gaps. The UI discloses incomplete or stale data;
failed tracking does not block inference. Quota refresh is shared and due-gated;
it never redeems reset credits. Existing auth, cache keys and global Pi settings
remain unchanged. Preserve newer Route data if rolling back the image.

See `docs/pi-route-usage.md`, `infra/pi/README.md` and the dated review evidence
under `docs/reviews/` for the contract, installation, limitations and recovery.
