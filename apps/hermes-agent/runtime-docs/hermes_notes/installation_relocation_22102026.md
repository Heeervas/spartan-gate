# Host installation relocation

Applied on 2026-10-22: the managed host installation moved to its organized
project location. The repository and durable data moved together on the same
filesystem. An old-location compatibility link remains for existing sessions
and host virtual environments; new shells load the canonical repository path.

All ten application containers were recreated using their existing image IDs.
Bind sources were updated, while Docker volume identities, container mount
destinations, networks and security settings were preserved. No image rebuild
was required. Recovery database snapshots passed integrity checks before the move.

Root Syncthing uses the relocated paths with the same folder identities and
directions. Its restricted namespace, installed checks and persistent SELinux
rules were updated together. The duplicate user Syncthing unit stays disabled.
Shared skills and system-managed runtime state retain their established owners.

The workstation wrapper again discovers the actual internal-network gateway,
honors Compose project selection and rejects failed or invalid discovery.
The existing gateway provenance and explicit repin checks remain in place.

Private configuration mappings, recovery copies and live verification evidence
belong to the host migration receipt outside this public repository. A later
removal of the compatibility link needs separate checks for retained sessions
and environment launchers.
