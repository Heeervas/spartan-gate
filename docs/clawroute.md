# ClawRoute Layout

Spartan Gate keeps the legacy ClawRoute package and the shared runtime routing
config separate on purpose:

- `packages/clawroute/`: source code, tests, package metadata, Dockerfile and
  development defaults.
- `config/clawroute/`: publishable runtime routing configuration mounted into
  Spartan Route in L4 and into ClawRoute when the legacy tier/addon is enabled.

Spartan Route is the current L4 router under `infra/spartan-route/`. It reads
the same publishable runtime config but writes its own `spartan-route.db`. See
[spartan-route.md](spartan-route.md).

## Source Package

Use `packages/clawroute/` when you are changing ClawRoute itself:

```sh
npm --prefix packages/clawroute ci
npm --prefix packages/clawroute test
npm --prefix packages/clawroute run build
```

The files under `packages/clawroute/config/` are package/development defaults.
They are useful for tests and standalone development.

## Runtime Config

The Compose stack mounts:

```yaml
- ../../config/clawroute:/app/config:ro
```

That means a running Spartan Gate stack reads provider/model routing from
`config/clawroute/`, not from `packages/clawroute/config/`, when started
through the public Compose file.

Keep publishable routing defaults in `config/clawroute/`. Put private provider
tokens in `.env` or `private/env/local.env`, not in JSON config files. The
Codex catalog can expose selectable GPT-5.6 entries without making them default
routes; the checked-in Codex provider profile remains on GPT-5.5/GPT-5.4 until
an operator intentionally changes tier mappings.

Spartan Route also provides authenticated live overrides at
`/dashboard-config`. These overrides cover only the four primary models used by
the active `clawroute/auto` classifier, are stored in `spartan-route.db`, and
never modify this read-only directory. Resetting the dashboard override restores
the file-loaded mappings. Use the JSON files for publishable defaults and the
dashboard for local runtime intent.

## Quick Rule

- Legacy ClawRoute code change: edit `packages/clawroute/`.
- L4 router code change: edit `infra/spartan-route/`.
- Publishable model/provider defaults: edit `config/clawroute/`.
- Live L4 auto-routing override: use `/dashboard-config`.
- Secret or host-specific auth: edit `private/env/local.env` or
  `private/compose.local.yml`.
