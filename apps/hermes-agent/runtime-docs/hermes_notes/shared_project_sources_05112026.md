# Shared project source access — 05/11/2026

Status: applied source-collection migration. The direct-source extraction of
three general plugins is recorded in `shared_plugins_extraction_05112026.md`;
no Gate snapshots remain for them. The four remaining source moves are now
applied after explicit shutdown authority; native session homes stay in place.

## Current access and safeguards

An approved host shared-project collection is mounted read/write at
`/projects/shared`. The native workspace navigation path
`/opt/data/workspace/projects/shared` is a symlink to the canonical view of the
same source collection. An additional narrow bind at the collection's matching
host absolute path resolves existing Git worktree links without mounting native
session homes. Pi extensions remain grouped under `pi-extensions/`; repositories
are independent, not a shared Git root or an installed plugin directory.
The collection excludes Gate private configuration and host credentials/sessions.
Hermes can modify every shared repository, including Git; this is not per-project
or per-profile isolation. Missing-source refusal and shared SELinux labeling remain.

An initial attempt to mount inside workspace was refused by stage2 with
`nested mount in ownership target`. The restart loop was stopped. The bind was
moved outside ownership targets; a descendant navigation symlink uses the
existing physical/no-follow ownership traversal without weakening its checks.
Final startup and health must be read back, not inferred from Docker start.

## Applied source preparation

- Node Search moved intact into `shared/node-search`, preserving full Git/file/
  mode/link identity. Original empty envelope removed; no Git changes or branch
  rewrite. Its three installed plugin files were not changed.
- Profile Delegate: clean public upstream main at `16c7c42`; installed sources
  differ (7 compared files match, 10 differ, 12 are absent from upstream).
- Hermes LCM: clean public upstream main at `a13e709`; installed sources differ
  (58 compared files match, four differ). No automatic deployment or reconciliation.

Actual source repositories remain the owners of instructions and validation.
A shared README provides navigation, not an overriding agent policy.

Node Search's container-only pinned development environment is now
`/opt/data/.cache/dev-venvs/node-search`, outside shared host source and managed
Hermes environments. The old controller-created `/opt/data/code` tree was removed
only after preserving its navigation/tooling receipt, inventorying exact owned
resources and checking current CWD/executable/open-file consumers. The old venv
was reconstructible and a replacement had already passed its checks.

Post-layout Node Search checks: UID-1000 clean Git/read/write at both navigation
paths; 37 pinned standalone tests plus one expected Hermes integration skip;
38 native Hermes tests without skips; Ruff and TOP QUALITY_PASS; Pi startup.
No private vault, paid inference, product source patch or installed-plugin update.
Profile Delegate/LCM source exposure is not their suite/deployment acceptance.

## Applied final source moves

Explicit subsequent approval allowed stopping necessary consumers while keeping
all worktrees. The original Zenith and Ultimate checkouts moved to `shared/`;
Pi Fast and Pi Rename Thread moved under `shared/pi-extensions/`. No fresh clone
replaced them. Compatibility aliases retain saved old host references; private
installation backups remain outside shared. Native worktrees/session homes did
not move. Supported Git repair reconnected their common directories and moved
manual worktrees; HEADs, branches, indexes and source bytes/modes/links match.
Pi Fast's three pre-existing dirty files are unchanged.

The host Ultimate launcher uses supported `ULTIMATE_HOME` selection for the two
retained project homes, honors explicit overrides and refuses a missing retained
home. Isolated launcher checks do not start a server/model. Direct `pnpm start`
bypasses this launcher and needs the retained home explicitly. Ultimate and its
editor/browser were left stopped; no automatic agent work was resumed. Existing
Pi instances retain loaded code until user reload/restart. Orca CLI runtime
readback was unavailable; its private registry/database was not rewritten.
Host Pi discovery/default child references and the Hermes Fast source bind now
select the grouped source. Only Hermes was recreated; no image build or paid
inference. Runtime health/access readbacks belong to the task's final evidence.

Gate-specific integration plugins remain in Spartan Gate. Source presence does
not establish whether a plugin is enabled in any given Hermes owner.

This note is an operator archive, not a published runtime guide.
