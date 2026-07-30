# Bounded Hermes Workstation Access

Spartan Gate gives the existing Hermes instance a second, explicitly named
terminal without replacing its normal terminal backend and without creating a
Hermes profile:

```text
Hermes default gateway
  workstation_terminal / workstation_process / workstation_transfer
    -> target-scoped Hermes approval adapter
    -> read-only-mounted Unix socket
    -> laptop host broker (owns SSH key; pins exact gateway PID)
    -> existing execution relay
    -> exact pinned SSH host key
    -> hermes-worker in Hermes-Workstation WSL
```

Blender keeps using the separate app-tunnel key and existing scoped relay. The
terminal plugin does not replace or modify the Blender MCP path.

## Security contract

- Hermes' hardline blocks, user deny rules, Tirith checks, manual approvals,
  cancellation, timeouts, ANSI stripping, and output redaction remain active.
- Approval keys are namespaced to `Hermes Workstation`. Normal-terminal
  approvals and free-form command allowlist entries do not transfer.
- Cron and bare non-interactive calls fail closed. The broker accepts only the
  exact default gateway PID pinned at broker startup, not a terminal child or
  another profile.
- PTY or stdin capability requires a separate one-time approval. Session and
  permanent choices are disabled and YOLO does not bypass it. Later keystrokes
  are an ongoing channel and cannot be inspected as new shell commands; the
  approval UI says this explicitly and shows the exact guarded command and
  bounded working directory before consent.
- The laptop broker owns the execution key. No SSH key is mounted into Hermes.
  The plugin cannot set a host, port, user, key, SSH option, or destination.
- The broker listens on an owner-only Unix socket. It adds no TCP listener,
  Docker port, host networking, generic proxy, or broader Docker network.
- Host OpenSSH uses `StrictHostKeyChecking=yes`, an exact generated
  `known_hosts`, Ed25519-only host-key selection, `ClearAllForwardings=yes`,
  and disabled agent/X11 forwarding.
- Remote cwd is limited to `/home/hermes-worker/workspace` and
  `/home/hermes-worker/windows-exchange`.
- Terminal starts and transfers use owner/generation-bound, operation-hashed,
  single-use broker reservations. Reset revokes pending approvals and active
  processes/streams before generation advances; missing or consumed
  reservations fail closed.
- Every transfer gets a separate one-time approval. The broker and plugin cap
  file data at 512 MiB, 10,000 entries, and fixed structural roots. They reject
  traversal, symlinks, hardlinks, special files, filesystem crossings, private
  keys, credential-store paths, and unapproved sensitive files.
- File bytes use a versioned framed stream over the same owner-only Unix socket;
  they are never embedded in model-visible commands, JSON, Base64, or output.

The unprivileged `hermes-worker` account remains the strong workstation
boundary. Changing Hermes UID/GID is not part of this feature; the current
UID/passwd mismatch remains documented technical debt.

## Configuration

Copy `private.example/workstation/bridges.json` to the ignored private location
and fill the existing remote, host-key, execution-key, and app-tunnel values.
Enable the terminal broker explicitly:

```json
"terminal_broker": {
  "enabled": true,
  "container_name": "spartan_gate_hermes"
}
```

Unknown fields, non-Tailscale remote addresses, non-loopback app targets,
fingerprint mismatches, symlink keys, broad key permissions, and duplicate
ports fail closed.

Expose only the broker directory to the existing Hermes service in the ignored
`private/compose.local.yml` overlay:

```yaml
services:
  hermes:
    environment:
      SPARTAN_WORKSTATION_BROKER_SOCKET: /run/spartan-workstation/workstation-terminal.sock
    volumes:
      - ${SPARTAN_WORKSTATION_STATE_DIR:-/run/user/1000/spartan-gate-workstation}:/run/spartan-workstation:ro
```

The plugin source is mounted read-only by public Compose, but remains inert
until it is explicitly enabled in the current Hermes configuration.

## Activation order

The order is deliberately sequential: every stage narrows authority for the
next one.

1. Validate and start the host broker:

   ```sh
   sg-workstation validate
   sg-workstation up
   sg-workstation status
   sg-workstation test
   ```

   `up` is idempotent when the complete bridge set is already running. If a
   process has stopped and the saved configuration and gateway still match,
   `up` preflights the required binaries and pinned remote host key, then
   PID-verifies and restarts the complete managed set. This briefly interrupts
   the surviving execution, application, and terminal bridges. Configuration
   or gateway changes still require an explicit `sg-workstation down` first.

2. Validate Compose and enable the plugin in the current/default Hermes
   instance. The plugin directory is bind-mounted read-only, so plugin-only or
   broker-only changes do not require a Hermes image rebuild:

   ```sh
   sg config --quiet
   sg exec hermes hermes plugins enable spartan-workstation-terminal
   sg-workstation restart-terminal
   sg up -d --force-recreate hermes
   sg exec hermes hermes plugins list --plain --no-bundled
   ```

   Rebuild Hermes only if the image does not already contain the reviewed
   target-scoped approval adapter, and only with more than 10 GB free. Do not
   create another profile or replace the normal Docker terminal backend.

3. Only after end-to-end acceptance, quarantine any old container-visible copy
   of the execution key. The helper moves only the exact supplied file after
   SHA256 equality with the host source and never prints key contents:

   ```sh
   python3 scripts/quarantine-workstation-key.py \
     --source private/workstation/keys/hermes_execution_ed25519 \
     --exposed /path/to/exact/legacy/container-visible/key \
     --quarantine-dir private/workstation/quarantine
   ```

   Prove the quarantined path is absent from rendered Hermes mounts. Keep the
   quarantine until rollback is no longer required.

If the gateway restarts, its PID no longer matches the broker's startup pin.
Run `sg-workstation restart-terminal` to pin the new exact default gateway
without restarting execution or application relays. This fail-closed behavior
is intentional.

## Tool surface

`workstation_terminal` supports foreground/background commands, foreground
timeouts to 900 seconds, background lifetimes to 24 hours, bounded cwd, PTY,
explicit stdin capability, completion notifications, and rate-limited literal
watch patterns.

`workstation_process` supports `list`, `poll`, incremental `log`, `wait`,
`write`, `submit`, `close`, `resize`, `interrupt`, and `kill`. Process IDs are
unguessable and owner/generation scoped. Reset asks the broker to revoke the
old generation before committing the next one; if revocation fails, generation
does not advance.

`workstation_transfer` uploads or downloads one regular file or directory. The
model supplies only relative paths, a structural Hermes workspace label
(`default` maps to `/opt/data/workspace`; a validated profile label maps to
`/opt/data/profiles/<label>/workspace`), and the fixed remote root
`workspace` or `exchange`. Limits are 512 MiB, 10,000 entries, depth 64, one
or two active streams, and four total workstation operations shared with
terminal processes. Foreground and non-durable background execution are
supported. `workstation_transfer_status` lists, polls, waits, or cancels jobs
owned by the current session/generation.

Every transfer requires a fresh one-time owner approval showing direction,
paths, kind, byte/entry counts, manifest SHA256, sensitivity, replace intent,
and the observed old destination SHA256. Private-key content and credential
store paths are hard-denied even with `allow_sensitive=true`; soft-sensitive
paths require that flag and highlighted approval. Existing directories are
never merged or replaced. Existing regular files require `replace=true`, are
rechecked immediately before publication, and retain a documented residual
race: publication is not linearizable against an uncooperative concurrent
writer.

Exchange downloads remain available. Exchange uploads are enabled lazily only
if an exact, self-cleaning probe proves `renameat2(RENAME_NOREPLACE)`, regular
file replacement, file fsync, and directory fsync on the real mount. A failed
probe is cached and leaves Exchange download-only. Transfers use normalized
streaming tar internally; archive bytes, private keys, absolute roots, and SSH
parameters are never exposed to the model.

There is deliberately no SCP/SFTP/general sync, agent/X11 forwarding,
local/remote/dynamic forwarding, arbitrary SSH destination, generic proxy, or
workstation network access.

## Validation

Run focused checks before live activation:

```sh
python3 -m unittest apps/hermes-agent/test_workstation_approval_scope_patch.py
python3 -m unittest infra/hermes-plugins/test_spartan_workstation_terminal.py
python3 -m unittest scripts/test_workstation_bridge.py
python3 -m unittest scripts/test_workstation_terminal_broker.py
python3 -m unittest infra/hermes-plugins/test_workstation_transfer_core.py
python3 -m unittest infra/hermes-plugins/test_workstation_transfer_protocol.py
python3 -m unittest infra/hermes-plugins/test_workstation_transfer_plugin.py
python3 -m unittest scripts/test_workstation_transfer_broker.py
python3 -m unittest scripts/test_quarantine_workstation_key.py
docker compose -f infra/compose/compose.yml config --quiet
```

Live acceptance must report host and actual Docker paths separately. It must
prove normal terminal behavior is unchanged; safe remote command/file
round-trips work; dangerous, denial, timeout, and hardline behavior is real;
PTY consent is one-time; host-key mismatch fails before authentication; no key
is visible in Hermes; no relay binds wildcard/LAN; arbitrary destinations fail;
Blender still passes; reset/disable revokes capability; and exact temporary
files/processes are removed.

## Workstation-side contract

The plugin does not alter Windows/Tailscale/firewall, WSL sshd, the pinned host
key, `hermes-worker`, the exact Exchange mount, disabled Windows interop/drive
automounts, SSH forwarding restrictions, Blender's Windows-loopback binding, or
GPU/ROCm state. If Portmaster, Fort Firewall, or Pi-hole blocks a check, report
the exact symptom instead of broadening exposure.

## Manual WSL package maintenance

Run `scripts/open-hermes-workstation-root.cmd` locally on the Windows
workstation as the Windows account that owns `Hermes-Workstation`. It asks for
confirmation, then opens a root shell in
`/home/hermes-worker/workspace`. Install only the required package:

```sh
apt-get update
apt-get install --no-install-recommends PACKAGE_NAME
exit
```

This operator-only console does not grant `sudo` to `hermes-worker` or expose
root through Hermes. Services bound to WSL `127.0.0.1` are not reachable through
the Windows/Tailscale IP. Exposing another application requires a separately
reviewed, fixed-port relay and firewall rule; do not replace that boundary with
`0.0.0.0`, a generic proxy, or a broad portproxy.
