# Shared skill mount isolation

Date: 2026-10-21 (Europe/Madrid).
Status: mount isolation applied and verified after operator recreation.

The dedicated host source is now `~/.agents/skills/shared`, with the same
container destination `/opt/shared-skills` and writable access. Only the 18
portable methods and `shared-skills` entry belong in this export. KDE and other
host-only skills remain outside it. Inspection of the recreated container
confirmed the dedicated source and no alternate parent bind. Runtime-user
read/write checks passed; KDE packages are outside the export.

The existing 19 package directories were moved without changing their contents,
ownership or file identities. Relative compatibility links at their previous
host locations preserve current readers. The exported subtree contains the
physical packages and its own filtered manifest, with no links to host-only
skills. The parent manifest and six KDE packages were preserved.

Mount isolation is separate from the canonical content migration, method
renames and profile enrollment. Codex owns the current correction and
integration; canceled Builder runs remain stopped. Publish only inside
`/opt/shared-skills`, preserving the single canonical package and its runtime
references. No KDE changes are required from Hermes.

Compatibility links outside the export are host integration metadata. A later
host cleanup must account for their targets after the canonical method rename;
do not replace a compatibility link with a new independent package. Existing
long-lived sessions may retain old paths and need refreshing after activation.

Any subsequent runtime patch has its own build/recreation acceptance. Recreate
only Hermes through the operator's approved flow, preserving dependencies and
data. Verify the effective source, runtime-user read/write access and the
absence of host-only skills after activation. See `docs/operations.md` for the
shared-library mount contract and rollback procedure.
