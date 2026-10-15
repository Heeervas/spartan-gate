# Python diagnostics and memory — 15 October 2026

Status: pending activation and runtime acceptance. Private configuration is
prepared for a 4 GiB Hermes RAM limit, a 6 GiB RAM-plus-swap limit (preserving
up to 2 GiB swap), and `lsp.idle_timeout: 300`. The operator owns Compose
validation and Hermes recreation. Do not restart services yourself or report
these settings as proven live memory savings.

## Python task workflow

- Keep Pyright and native LSP diagnostics enabled. Use native `write_file` and
  `patch` during edits so automatic diagnostics run; terminal writes bypass
  that integration and need an explicit check.
- Before declaring a Python task complete, run one complete project Pyright
  validation with its project configuration and interpreter. Preserve existing
  strictness, imports and library typing. Relevant tests are still required.
- Avoid complete project scans after every edit and concurrent scans for the
  same project. Edit diagnostics do not cover all affected unopened files.
- Correct introduced errors and distinguish pre-existing diagnostics. Repeat
  necessary checks before reporting completion; report unavailable, interrupted,
  timed-out or failed checks with their reason, never as clean.

The inspected installed Pyright 1.1.414 already defaults to
`openFilesOnly: true` and `useLibraryCodeForTypes: true`. No `python.analysis`
override is needed. Hermes shallowly merges the `python` object; replacing it
can lose automatic interpreter selection. Do not disable library typing or
weaken the project's settings to reduce memory.

Existing LSP code reuses multi-root servers per profile/process and caps open
documents at 64. The prepared idle timeout decreases from 600 to 300 seconds;
cleanup can occur around 300–360 idle seconds plus shutdown time. This is an
expected lifecycle window, not a guaranteed resource saving. An existing LSP
service singleton does not reload settings in place, while a fresh service can
read the prepared configuration before container recreation.

## Operator follow-up

Concurrent configuration saves can overwrite the prepared idle timeout. The
operator first reruns the preparation helper in preview mode and requires zero
proposed changes, then runs `sg config --quiet` and `sg up -d --no-deps hermes`,
and inspects startup, health and effective limits/settings while preserving
other containers. Acceptance remains pending for synthetic error/correction,
complete-scan detection in an affected unopened file, server reuse and idle
cleanup, and OOM/restart deltas, pressure and memory margins. Rollback uses the
private backup manifest retained by the preparation helper. Restore only files
whose current hashes match their prepared versions; review later edits rather
than clobbering them. Operator validation and Hermes-only recreation follow.
Do not claim these checks passed until there is runtime evidence.
