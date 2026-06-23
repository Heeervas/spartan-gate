# Sharing With A Colleague

Spartan Gate is meant to be shareable without your private runtime.

Before sharing, assume everything under `docs/` is public. Move personal notes into `private/docs/`.

## What To Share

Share the Git repo. Do not share:

- `private/`
- `.env`
- `runtime/`
- Codex auth directories
- Caddy password hashes from real machines
- Telegram, Discord, GitHub, Raindrop, provider, or OAuth tokens

## Colleague Setup

```sh
git clone https://github.com/Heeervas/spartan-gate.git spartan-gate
cd spartan-gate
scripts/install.sh --tier L0
scripts/doctor.sh
```

For the simplest setup, start colleagues at L0 or L1. Use L4 only when they
need the full proxy, DNS, reader, ClawRoute, Caddy, and browser boundary.
If they need ClawRoute routing but not the rest of the gate, enable it as an
addon:

```sh
scripts/install.sh --tier L1 --with clawroute
```

Send them [layers.md](layers.md) when they need to choose between L0-L4 or
understand what changes when ClawRoute is added to L0/L1.

The filename `local.env` is only a convention from this repo. A colleague can keep it, or use another file and set:

```sh
export SPARTAN_ENV_FILE=/absolute/path/to/their.env
```

## First Run

```sh
source scripts/aliases.sh
sg-tier list
sg-tier set L1
# Optional ClawRoute addon: sg-tier set L1 --with clawroute --hermes free
sg-tier show
sg-up
sg-status
```

Hermes starts with the default public stack. They can recreate only Hermes after editing their own tokens and paths:

```sh
sg up -d hermes
```

## Support Boundary

Ask them to send command output from:

```sh
scripts/doctor.sh
sg-tier doctor
sg-status
```

They should redact token values and avoid sending `private/env/*.env`.
