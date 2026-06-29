# Current State

Date: 2026-06-29

## Latest Update

Spartan Route cache-lineage review fixes are implemented and runtime-smoked on
`spike/hermes-cache-lineage`. Effective cache keys now include plugin lineage
over explicit, content-derived, or session identities; same-session cache
diagnostics are grouped by the effective namespace; malformed lineage values
are ignored; OpenAI passthrough receives the effective key; and both full and
tier Spartan Route overlays mount the Hermes `spartan-cache-lineage` plugin
without masking other user plugins. `spartan-route` was rebuilt and recreated;
Hermes remained on the existing image with the enabled plugin.

## Active Task

Implemented and runtime-smoked; branch cleanup pending.

## Blockers

None recorded.
