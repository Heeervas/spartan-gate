# Go-Live Checklist

Use this before treating Spartan Gate as the definitive stack.

For the longer migration path from an older Hermes stack, see `docs/cutover.md`.

This file is public and generic. Keep local acceptance notes, real ports, profile names, old-stack names, and token ownership details in `private/docs/go-live-local.md`.

## 1. Static Validation

```sh
scripts/doctor.sh
docker compose -f infra/compose/tiers/compose.l0.yml config --quiet
docker compose -f infra/compose/tiers/compose.l0.yml -f infra/compose/tiers/compose.clawroute.yml config --quiet
docker compose -f infra/compose/tiers/compose.l1.yml config --quiet
docker compose -f infra/compose/tiers/compose.l1.yml -f infra/compose/tiers/compose.clawroute.yml config --quiet
docker compose -f infra/compose/tiers/compose.l2.yml config --quiet
docker compose -f infra/compose/tiers/compose.l2.yml -f infra/compose/tiers/compose.l3.yml config --quiet
docker compose -f infra/compose/compose.yml --env-file .env.example config
docker compose -f infra/compose/compose.yml -f private/compose.local.yml --env-file private/env/local.env config
npm --prefix packages/clawroute test
npm --prefix packages/clawroute run build
```

## 2. Stack Smoke

```sh
source scripts/aliases.sh
sg-tier set L4
sg-tier show
sg-up
sg-status
```

Check ClawRoute, the selected browser service, reader, proxy, DNS, and Hermes
for L4. For smaller installs, check only the services in the selected tier.

## 3. Network Policy

```sh
scripts/whitelist-domain.sh --no-restart example.com
```

Then verify allowed domains work through proxy/DNS and non-whitelisted domains fail from a controlled test container.

## 4. Hermes Runtime

After paths, ports, tokens, and profile variables are checked, recreate Hermes if needed:

```sh
sg up -d hermes
sg-logs-hermes
```

Confirm Hermes calls `http://clawroute:18790/v1`, not provider APIs directly.

## 5. Profiles

```sh
sg-hermes-profiles
```

Before starting a gateway profile, confirm it has a dedicated messaging token
and a unique API port if the API server is enabled. Start/stop profiles through
Hermes' s6-aware lifecycle commands:

```sh
scripts/hermes-api-port-preflight.py
sg exec hermes hermes -p <profile> gateway start
sg exec hermes hermes -p <profile> gateway stop
```

Use `HERMES_AUTOSTART_PROFILES` only as a legacy bootstrap for profiles that do
not yet have `gateway_state.json`; it will not override an existing stopped
state.

## 6. Cutover

Keep the old stack stopped or isolated while testing shared tokens, browser sessions, and host ports. Do not point Spartan Gate at old data paths until you intentionally migrate.

Minimum cutover acceptance:

- Old Hermes is stopped before Spartan Gate Hermes starts.
- Spartan Gate uses isolated data paths.
- No two live gateways share the same Telegram or Discord token.
- Edge ports resolve to Spartan Gate, not the old stack.
- A rollback is possible by running `sg-tier down` and starting the old stack from its own directory.
