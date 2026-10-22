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
need the full proxy, DNS, reader, Spartan Route, Caddy, and browser boundary.
Use L3 for internal Spartan Route with the gated services; the legacy router
addon is retired. Send them [layers.md](layers.md) to choose between L0-L4 and
[development.md](development.md) for reproducible checks and native prerequisites.

The filename `local.env` is only a convention from this repo. A colleague can keep it, or use another file and set:

```sh
export SPARTAN_ENV_FILE=/absolute/path/to/their.env
```

## First Run

```sh
source scripts/aliases.sh
sg-tier list
sg-tier set L1
# Internal Spartan Route: sg-tier set L3 --hermes gated
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
