# Cutover From An Existing Hermes Stack

This path is for replacing an older Hermes stack with Spartan Gate without corrupting old runtime data.

This is the public, generic cutover flow. Put your exact old stack path, actual port map, real profile list, token ownership notes, and rollback details in `private/docs/cutover-local.md` or a machine-specific private runbook.

## Rule Zero

Do not point Spartan Gate at old Hermes data paths until the new stack has passed a clean smoke test with isolated paths.

Use isolated paths in your private env file:

```env
SPARTAN_HERMES_DATA_PATH=/absolute/path/to/new/spartan-gate/hermes
SPARTAN_CLAWROUTE_DATA_PATH=/absolute/path/to/new/spartan-gate/clawroute
SPARTAN_GOGCLI_DATA_PATH=/absolute/path/to/new/spartan-gate/gogcli
```

## 1. Validate New Stack Without Starting It

```sh
scripts/doctor.sh
docker compose -f infra/compose/compose.yml -f private/compose.local.yml --env-file private/env/local.env config
```

Check for host port conflicts:

```sh
docker ps --format '{{.Names}}\t{{.Ports}}'
```

If the old stack owns ports you want Spartan Gate to use, change the Spartan Gate private ports first or stop the old stack before starting Caddy.

Record the actual old-stack directory and port conflicts in private docs only.

## 2. Start Spartan Gate

```sh
source scripts/aliases.sh
sg-up
sg-status
```

Confirm the new stack is healthy before touching the old Hermes process. If token or port ownership could conflict, stop the old stack first or keep Spartan Gate on isolated private ports.

## 3. Stop Old Hermes

From the old stack directory, stop only the old stack you intend to replace:

```sh
docker compose down
```

Do not run broad Docker cleanup commands during cutover. Avoid pruning volumes until the new stack has been running correctly for a while.

## 4. Start Spartan Gate Hermes

Back in the Spartan Gate repo:

```sh
sg up -d hermes
sg-logs-hermes
```

Confirm:

- Hermes creates state in the new `SPARTAN_HERMES_DATA_PATH`.
- Hermes uses `http://clawroute:18790/v1`.
- Caddy routes work over local HTTP or your private Tailscale bind.
- Telegram/Discord tokens are not shared by two live gateway processes.
- Autostart profiles have unique tokens and `API_SERVER_PORT` values.

## 5. Migrate Deliberately

Only after the clean stack works, decide what to migrate from the old stack:

- durable profile files
- selected skills
- `SOUL.md`
- curated `config.yaml`
- memories or plans that are intentionally reusable

Do not blindly copy runtime state such as `state.db`, `auth.lock`, `logs/`, `sessions/`, or `sandboxes/`.

## Rollback

If Spartan Gate fails during cutover:

```sh
sg-down
```

Then restart the old stack from its own directory. Because Spartan Gate used isolated paths, the old runtime data should remain untouched.
