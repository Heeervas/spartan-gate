# Shared tool environment: recreation confirmation

Applied change to the operator helper on 16 October 2026.

`sg-hermes env set TOOL_API_KEY` and the same command with `--apply` now
both ask before recreating Hermes after saving a changed value. The menu
warns that recreation interrupts active work. Only option `1` recreates
Hermes; Enter or `2` saves for later. Invalid input or an interrupted prompt
keeps the saved value pending without recreation. `--no-apply` saves without
a confirmation menu. An unchanged value does not trigger recreation.

This changes the host helper only; no Hermes recreation or credential change
was performed as part of this update.
