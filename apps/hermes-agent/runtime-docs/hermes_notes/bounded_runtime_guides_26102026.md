# Bounded Hermes runtime guidance — 26 October 2026

Status: applied source and deployment change; this file is an operator archive,
not runtime guidance and not mounted into Hermes.

## Publication boundary

The former whole-directory runtime-doc bind exposed accumulated operational
notes, including untracked additions, immediately. A read-only mount did not
restrict reading. The private nested documentation bind exposed another whole
operator subtree. Neither a prompt index nor omitting a link enforced a file
boundary.

Hermes now receives one read-only `runtime-guides` directory containing only:

- `local/camofox.md`
- `local/package-installs.md`
- `local/private/profile-creation.md`

The historical `private` path component is retained for compatibility; the
published recipe is sanitized and public safe. The two existing public guides
were moved unchanged. The profile recipe promotes the existing operational
procedure, replacing stale peer inventories with the migrated eight-plugin
BASIC baseline. The retained private operator original is historical, not a
second active procedure. The existing profile skill is selectively restored
with complete support files and reconciled current read routes.

Adding another file to `runtime-guides` is explicit publication and requires
approval plus a boundary-test update. Continue recording operational findings
in this unserved archive; never mount it or a nested operator-doc directory.
Shared Brain and preserved-home mounts are separate access surfaces: this
change does not establish hostile profile or filesystem isolation.

## Verification and recovery

The existing runtime-doc regression was changed first and failed on the old
broad mount, then passed with the curated layout. Merged Compose and actual
container mounts each showed one read-only runtime-doc bind and no descendant
mount. All three guides were readable and not writable; an archived note and
private operator note were absent through `runtime_docs`. Only Hermes was
recreated, without build or pull; neighboring service identities were unchanged.
Initial readiness probes failed while the API listener initialized, then Hermes
became healthy with its existing default transport connected.

Profile recovery remains separate from fresh creation. Stage outside named
profile discovery; preserve original database families without SQLite opens,
validate copies offline, and retain history/channel mappings while quarantining
pending work. A staged profile, a parked marker, or a readable database is not
activation or proof of live delivery. Dedicated per-profile bots can share the
native multiplex gateway process; bot credential ownership and runtime profile
routing are separate concerns. Credential restoration and activation each
require their own explicit authority.
