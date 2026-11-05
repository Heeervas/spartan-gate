# Gate-owned Hermes integrations

This directory contains adapters tied to Spartan Gate services: cache lineage,
Discord voice relay/Talk, supervised Workstation access and the Reader backend.
They remain owned by Gate, with their native and boundary tests.

General-purpose `discord-deferred-thread-title`, `self-improvement-governor` and
`shared-masterv2` are independent repositories in the operator's approved shared
source collection. Their package code, compatibility tests and development
instructions live there, not as Gate snapshots. The historical governor installer
and its test moved with its plugin as explicitly legacy tooling. `shared-master`
v1 is retired; Git history preserves the old source.

The public optional integration recipe is
`../compose/hermes-shared-plugins.example.yml`; supply SHARED_PROJECTS_DIR only
after provisioning and reviewing those repositories. The base stack has no
source dependency on them. Operator-owned profile enablement remains separate.
Read-only runtime aliases do not prevent mutation through another writable path
to the same source repository. Coordinate edits/reloads and do not claim source
immutability or OS isolation from mount flags alone.

Gate keeps `test_prompt_capacity_patch.py` for its own image patch's atomic
preflight/idempotence. The standalone shared-masterv2 repository owns actual
plugin/native assembly coverage and takes the compatible capacity patch as an
explicit test input. No plugin behavior changed as part of source relocation.
