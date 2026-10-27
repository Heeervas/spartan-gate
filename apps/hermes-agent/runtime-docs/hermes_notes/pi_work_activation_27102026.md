# Pi reuse from WORK — 27 October 2026

Status: **applied; bounded native execution and same-session continuation passed**.

## Applied change

- The selected owner reuses the already activated container Pi state through one
  `$HERMES_HOME/pi` symlink. No package, configuration or role copies were made.
- Its workspace instructions now describe authorized task packets, native
  background handles, explicit session recovery and result verification. Existing
  instructions, collaborator restrictions and the actual Hermes home are retained.
- Configuration, Route credentials and Pi session storage are deliberately shared
  with the existing owner; this is not profile isolation. Do not inspect unrelated
  sessions, reuse another task's identity or treat delegation as new authority.

## Verification

- The actual workload identity loaded four extensions, one Route provider, nine selected
  skills and three roles, with zero loader errors/skill diagnostics. Native Pi
  context discovery selected only the intended workspace AGENTS file.
- Two authorized Sol/minimal, no-tools calls used Hermes's actual native terminal
  background registry under the selected owner's home/terminal scope. Both exited
  zero; exact assistant output was checked independently. Reopening the recorded
  Pi session recalled a fresh nonce not supplied in the continuation: 27.3 seconds
  total, two assistant messages, zero tool calls. No client project was changed.
- Native completion registration was exercised; automatic Discord notification
  delivery and tool-enabled coding were not re-tested by this bounded check.
- Container identity/start time remained unchanged, health was healthy and restart
  count zero. Gateway metadata reported DEFAULT Discord/API and WORK Discord
  connected, with both profiles served and no restart requested.
- Repository TOP quality passed with existing exceptions. No new production code
  was changed; those metrics do not validate the private symlink/instructions.

## Recovery and operations

No gateway reload, container recreation or image rebuild was needed. The operator
retains a private raw execution receipt and the exact pre-edit instructions outside
instruction discovery. Stop/reconcile owned jobs before withdrawal; remove only
this owner's symlink and restore only the added instruction section if unchanged.
Never delete or restore over the shared Pi state, sessions or newer foreign edits.
This note is an operator archive entry, not a published Hermes runtime guide.
