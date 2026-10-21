# Hermes quality CLI acceptance — 2026-10-21

Source candidate: `19e66eb35062ee36bfecffb59c518e6f15f3322f`, complete patch SHA256
`15cc80c826a760fa70697d80fa038000a02ab958c8cbb0d55f29003f0a067563`.

Actual native Pi Builder and Reviewer used GPT-6.1 Sol with medium reasoning.
First review blocked on canonical Qlty command rejection, missing Bandit lookup
and production PYTHONPATH shadowing. Builder corrected these; independent
re-review reproduced the old failures and passed all repaired cases. One
interrupted upstream stream was resumed with its owned edits preserved.

TOP deterministic measurement passed with the five existing exceptions and no
evidence gaps. The baseline, policy, thresholds and exceptions were not weakened.
This scoped result is separate from behavioral review and image acceptance.

The approved clean-worktree image built successfully. Image and live canaries
passed as the actual runtime UID/GID with writable scratch and read-only root:

- Canonical Qlty version flags, minimal no-plugin initialization, metrics and
  valid SARIF; ordinary/login-shell versions.
- Root ownership and non-writability of installed tools.
- Desloppify under hostile inherited imports, with actual security findings
  including Bandit B602, and local review preparation.
- Default/Builder/Reviewer shell execution and unchanged native inventories,
  disables, external roots and selected view/edit resolution.

The new container is healthy and runs image
`sha256:539fcd392147140148dc98834876c2e5cccfea067dbcf8531b9e949cc3c8ee53`.
Only Hermes changed identity during this operation's cutover. Mounts, networks,
resource limits, read-only root and Camofox-only mode were preserved. Concurrent
Route configuration work was retained. The previous image remains available for
scoped recovery; source and evidence remain in local commits/private receipts.

Limits: amd64 execution tested; arm64 artifacts verified but not executed.
Transitive dependency versions are pinned, not every artifact hash. Optional
`jscpd` duplication checks are not available; the bounded canary used
`--skip-slow`, retaining security. A preliminary canary assertion used an old
state key and was corrected to the installed `work_items` schema without changing
the image. Cold startup briefly failed HTTP health probes before initialization
completed. No trusted subjective-score import, model runner inside Hermes,
automatic acceptance gate, profile enrollment or push is claimed.
