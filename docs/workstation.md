# Bounded Workstation Access

Spartan Gate can use a more capable Tailscale-connected PC without placing
Hermes on an external Docker network. The laptop exposes only selected relays
on the `spartan_internal` gateway:

```text
Hermes workstation profile
|- SSH terminal -> laptop gateway -> Tailscale -> WSL sshd
`- Blender MCP  -> laptop gateway -> restricted SSH forward -> Windows loopback
```

This is a manual operator capability. Do not autostart the workstation profile,
publish a generic proxy, or expose application ports directly on Tailscale.

## Main PC contract

Use Windows 11 with Tailscale on Windows only and a dedicated Ubuntu 24.04 WSL2
distro in mirrored-networking mode. Inside WSL, run OpenSSH on port `22022` as
the unprivileged `hermes-worker` user. Disable Windows executable interop and
automatic Windows-drive mounts for this distro; keep its workspace at
`/home/hermes-worker/workspace`. Windows can open it through
`\\wsl.localhost\<distro>\home\hermes-worker\workspace`.

Expose only WSL SSH through a Windows relay bound to the Windows Tailscale IPv4.
The Tailscale policy and Windows Firewall rule must both limit TCP `22022` to
the laptop's Tailscale IPv4. Application services remain on Windows loopback.

Use separate authorized keys:

- The Hermes execution key allows commands and PTY but disables agent, X11,
  and port forwarding.
- The app-tunnel key disables commands and PTY and permits forwarding only to
  declared loopback endpoints, initially `127.0.0.1:9876` for Blender.

Record the WSL Ed25519 host public key and SHA256 fingerprint locally before
transferring them to the laptop. `sg-workstation up` compares the live endpoint
against both values before creating any relay.

The RX 9070 XT can use a currently supported AMD WSL/ROCm combination, but GPU
driver installation is a separate privileged step. Verify `rocminfo` before
treating the worker as GPU-capable.

## Laptop setup

Copy the example and replace every remote/key placeholder with the handoff from
the main PC:

```sh
mkdir -p private/workstation/keys
cp private.example/workstation/bridges.json private/workstation/bridges.json
chmod 700 private/workstation private/workstation/keys
chmod 600 private/workstation/bridges.json private/workstation/keys/*
sg-workstation validate
```

The configuration is intentionally strict:

- `remote.host` must be inside Tailscale's `100.64.0.0/10` range.
- SSH identity files must be owner-only regular files, never symlinks.
- Every application target must be remote `127.0.0.1`.
- Gateway and local-forward ports must be unique.
- Unknown fields, malformed keys, and fingerprint mismatches fail closed.

Operate the relays manually:

```sh
sg-workstation up
sg-workstation status
sg-workstation test
sg-workstation down
```

`up` first validates all configuration, key files, binaries, bind addresses,
and the live SSH host key. Partial startup is rolled back. `down` signals only
processes whose PID and Linux start time still match its private runtime state.

## Hermes profile

Create a dedicated manual profile, with no messaging, API port, or autostart:

```sh
sg-hermes profile-new workstation --manual --no-telegram --no-discord --no-autostart
```

Set these values in that profile's private `.env`, using the actual Spartan
gateway address and an execution-key path visible inside `HERMES_HOME`:

```env
TERMINAL_ENV=ssh
TERMINAL_SSH_HOST=172.29.0.1
TERMINAL_SSH_PORT=22022
TERMINAL_SSH_USER=hermes-worker
TERMINAL_SSH_KEY=/opt/data/profiles/workstation/ssh/hermes_execution_ed25519
TERMINAL_SSH_PERSISTENT=true
TERMINAL_CWD=/home/hermes-worker/workspace
```

Keep the profile's `config.yaml` interactive and fail closed:

```yaml
approvals:
  mode: manual
  cron_mode: deny
```

Before the first Hermes connection, copy the generated execution
`known_hosts` entry from the bridge runtime state into `/opt/data/.ssh/known_hosts`
with owner-only permissions. Hermes currently uses `accept-new`; pre-seeding the
verified key prevents first-connection trust from becoming the security check.

Install the catalog Blender MCP only in the `workstation` profile and set
`BLENDER_HOST` to the Spartan gateway with `BLENDER_PORT=9876`. The Blender
add-on remains bound to Windows loopback.

Blender's `execute_blender_code` runs arbitrary Python with the authority of the
normal Windows user. Profile separation and manual approvals reduce accidental
use but are not an OS sandbox. Keep that tool disabled unless it is needed for
the current task.

## Adding applications

Add one application at a time. Each adapter requires a loopback-only service on
the main PC, a `bridges.json` entry, an app-tunnel `permitopen` restriction, and
a positive/negative test. Do not replace these adapters with SOCKS, arbitrary
port forwarding, or a general Windows shell.
