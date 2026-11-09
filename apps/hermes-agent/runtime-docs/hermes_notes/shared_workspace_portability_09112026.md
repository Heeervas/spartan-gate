# Shared workspace portability — 09/11/2026

Status: applied and verified for the Hermes/Git slice on 09/11/2026;
whole-workspace migration remains incomplete.

The approved target is one writable project-collection bind at
`/opt/data/workspace/projects/shared`, without a second host-absolute container
view or a navigation symlink. Workspace is user code: the ownership patch no
longer scans or recursively repairs it as privileged internal state. Existing
internal-state checks and shared-skill protections remain enabled.

The image build pins Git 2.56.0, enabling relative linked-worktree metadata across
host/container roots. Converting a repository requires compatible Git in every
consumer and a coordinated cutover that excludes old readers as well as writers.
A false configuration value alone is not a verified format downgrade.

The host and all authorized tools should collaborate on the same source/worktree
files; credentials, trust and conversations remain in private native stores.
Ultimate's existing location setting covers future worktrees and landers, not
relocation of existing records. Existing native worktree relocation is blocked on
a supported transition; never move directories or rewrite a native runtime DB
externally. Do not mistake partial preparation for complete collaboration.

Validation includes the actual candidate ownership loop in isolated containers,
unchanged workspace bytes/modes/owners, no privileged scan, retained fail-closed
internal-state mount checks and a two-direction native Git fixture.

Hermes was recreated alone with the reviewed image and is healthy, running Git
2.56.0 as UID/GID 1000. The old readers were stopped before conversion. Ten
repositories now opt into relative worktrees; supported Git repair preserved the
indexes and HEADs of all 26 registered checkouts without moving their paths.
All 17 checkouts physically inside the shared collection work through both host
and container views. The single RW bind, absence of the old aliases, unchanged
neighboring containers and native create/read/patch behavior were verified;
outside-root writes and credential guards still refuse access. The existing
shared-skill aliases are allowed through the write-safe-root boundary.

Nine external registrations remain outside the mounted collection, including
seven native Ultimate worktrees and two separately owned temporary worktrees.
They were not relocated or exposed. Existing native relocation and supported
future placement still require their own reconciled transition; this activation
does not complete those outcomes. Never prune registrations merely because the
container cannot see their host-only paths. Do not restore an old Git image
against the new repository format without a verified compatible recovery.

Runtime publication is not implied by this archive note; it is not a mounted guide.
