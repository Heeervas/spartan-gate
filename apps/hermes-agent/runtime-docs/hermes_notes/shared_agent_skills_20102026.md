# Shared agent skills

Date: 2026-10-20 (Europe/Madrid).
Status: applied default-context setup; live acceptance passed.

The applied setup mounts a writable host-owned skill library at
`/opt/shared-skills` and appends that directory to the default Hermes context's
`skills.external_dirs`. Named-profile configurations and existing skill trees
remain unchanged. Named profiles support external directories, but their
enrollment is deferred until the shared library is populated and reviewed.
Existing profile-local skills retain their normal discovery and precedence.

The shared library remains intentionally empty pending population by Hermes,
including a frontend skill and its reference structure. Populate complete skill
directories with their supporting files, explicitly targeting
`/opt/shared-skills`. Do not assume
external discovery changes the creation destination: `skills.create_dir`
remains unchanged. Agent-specific reference customization is deferred.

Edits and deletions in this writable directory affect the host library and
every consumer that uses it. Preserve local skill divergences and review the
destination before changing existing content. No skills are copied as part of
this setup, and no shared policy skill is introduced.

The single recreation kept the exact same image and Hermes became healthy, but
non-root writes failed: Compose did not apply the requested shared SELinux
label with `create_host_path: false`. A dedicated-root-only `container_file_t:s0`
label correction restored access. The empty directory retains mode 0755 and
runtime ownership, and its parent label is unchanged. No recursive relabel or
persistent host policy was applied; `restorecon` or a filesystem relabel can
reset the correction. Keep the missing-source protection and verify actual
labels and access after future deployment changes.

Live acceptance ran as Hermes' actual non-root UID/GID. A temporary shared
skill passed native discovery and viewing, including reading an edited body;
create, read, edit and delete passed, and its label inherited
`container_file_t:s0`. The probe was removed, leaving the library empty.
The default native inventory retained the same 216 skill names and hash, with
`skills.external_dirs` exactly `[/opt/shared-skills]`. Native resolution excludes
the shared directory from all 12 named profiles. All 869 existing local
`SKILL.md` records across 13 scopes retained their paths, hashes and metadata.

All 14 configuration files matched their expected hashes and metadata; only
the private Compose mount and default context configuration changed. Hermes is
healthy with no restarts after recreation. All eight neighboring containers
retained their identities, images, start times and restart counts, and all nine
containers are healthy. Shared library population remains deferred to Hermes.
See `docs/operations.md` for backup, activation and rollback instructions.
