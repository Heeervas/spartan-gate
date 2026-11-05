# Node Search source workspace — 05/11/2026

Status: historical applied development preparation. Its `/opt/data/code` and
original host-envelope placement were superseded by the approved shared-project
collection; see `shared_project_sources_05112026.md` for current access. No plugin
fix or production plugin update is included.

## Source, not installation

`node-search` belongs to the public `Heeervas/node-search-plugin` repository.
The writable native installation is not a replacement for that repository.
A dedicated full-history source clone now tracks `origin/main` at
`2482119ef48f0ad4ea071e895e141bf8b1d64dff` (latest observed upstream main,
plugin 0.3.1). Repository AGENTS.md, README.md, STATE.md, native validation,
quality policy/baseline and tests are present. All three installed plugin source
files matched that revision before preparation and remained unchanged afterward.

The approved source-only read/write mount is `/opt/data/code/node-search-plugin`.
It exposes no host home, Gate private tree or host Git credentials. Missing-host
source refusal and shared SELinux labeling remain enabled; actual UID-1000
Git/read/write/delete access passed. Only Hermes was recreated, without build,
pull or dependency-service restart. Neighboring container identities matched.
Hermes returned healthy and Pi startup remained functional.

## Ready development tooling

Activate `/opt/data/code/.environments/node-search-plugin/bin/activate` before
launching Pi from the source directory. Keep the invoking owner's HERMES_HOME.
The container-owned environment is separate from Hermes's managed environments
and from host/worktree environments. It contains repository pins pytest 8.4.2,
Ruff 0.14.1, PyYAML 6.0.3 and canonical-assurance validator jsonschema 4.26.0.
Reprepare it if the container's base image/interpreter path changes.
A workload-owned `/opt/data/code/NODE_SEARCH_WORKSPACE.md` records paths,
commands, scope and tested limits; it does not override repository instructions.

## Verification and limits

- UID-1000 Git root/branch/history/instructions, temporary source write/delete,
  and real `git ls-remote` succeeded; remote main matched checkout HEAD.
- Pi 1.5.0 `--version` and `--help` passed from this checkout.
- Python 3.14.7 pinned standalone suite: 37 passed, one expected native-Hermes
  integration skip; compilation and Ruff passed.
- Existing native Hermes interpreter, synthetic HOME/roots: 38 passed, no skips.
- Actual repository immutable baseline and TOP policy: QUALITY_PASS.
- Source checkout remained clean and deployed plugin hashes unchanged.

Tests used only synthetic Markdown and disposable scratch HOME/cache state;
no private vault scan or paid inference occurred. This is not new Python
3.10–3.12 matrix, dependency-admission, bug-fix, deployment or publication
acceptance. Follow the owning repository's instructions before actual repair,
and deploy only the validated revision under separate operator authority.
Do not auto-pull over local changes or edit the installed copy in lieu of source.

This is an operator archive, not a mounted runtime guide.
