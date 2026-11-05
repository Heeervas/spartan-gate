# Pi package mount recovery — applied

Date: 2026-11-05. Status: applied operational repair; no Hermes image rebuild.

## Failure and correction

A host npm update replaced the globally installed Pi package directory. The
running container retained the old bind inode. Recreation renewed the bind,
but the replacement tree had SELinux `user_home_t` labeling; even container
root received `Permission denied`, while Node reported `MODULE_NOT_FOUND`.
Ordinary file permissions were already correct.

The exact package tree was labeled `container_file_t`, restoring startup.
The container package source was then moved to a complete dedicated versioned
Pi 1.5.0 program snapshot in the deployment data area, outside global npm
ownership. Source/copy content and mode manifests matched for 15,361 entries;
all symbolic links resolved inside the package. The snapshot is mounted
read-only at `/opt/pi-package` and has the shared container label. Only Hermes
was recreated; all neighboring running container identities were preserved.
No network, authentication, session or profile settings were changed.

## Verified boundary and prevention

As the existing workload UID 1000 with its actual `HERMES_HOME`, `pi --version`
returned `1.5.0` and `pi --help` exited zero after recreation. This establishes
native CLI startup, not an inference, extension or coding-job success claim.
The unrelated `node_search` repair remains outside this change.

Host npm updates no longer replace the container program mount. Container Pi
upgrades are deliberate: prepare a new complete versioned snapshot, verify its
contents and SELinux access, change the private package source, reconcile jobs,
recreate only Hermes, then run workload-user startup and service health checks.
Never mutate an active snapshot in place. Preserve the old source for rollback.

The shared SELinux label is not persistent host policy: filesystem relabeling or
`restorecon` can reset it. Verify/reapply the exact snapshot label after such an
operation; do not disable SELinux, broaden Unix permissions, or relabel parent
home/npm trees. See `docs/hermes-pi-jobs.md` for the public operator procedure.

This file is an operator archive, not a published runtime guide or mounted input.
