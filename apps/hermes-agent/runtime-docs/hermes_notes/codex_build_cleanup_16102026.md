# Host Codex executable cleanup

Status: applied operator helper, 16 October 2026.

`sg-disk codex-clean` previews old host Codex executable releases and requests
interactive confirmation. `--dry-run` only previews; `--yes` skips the prompt.
Selected and running builds, newer downloaded builds, and one extra older
release per installation are retained by default. `--keep N` adjusts the extra
rollback retention. Cleanup sacrifices the removed versions' rollback capacity.

The helper only manages host app-server-daemon release directories. It does not
clean Hermes, Docker, host npm caches, Codex histories or application databases.
Shared installation paths and hard links are deduplicated in recovery estimates.
Avoid starting or updating host Codex during deletion; process inspection has
documented user and namespace visibility limits. See `docs/operations.md`.
