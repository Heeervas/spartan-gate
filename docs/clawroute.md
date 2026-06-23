# ClawRoute Layout

Spartan Gate has two ClawRoute directories on purpose:

- `packages/clawroute/`: source code, tests, package metadata, Dockerfile and
  development defaults.
- `config/clawroute/`: runtime routing configuration mounted into the running
  container by Compose.

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
`config/clawroute/`, not from `packages/clawroute/config/`, when started through
the public Compose file.

Keep publishable routing defaults in `config/clawroute/`. Put private provider
tokens in `.env` or `private/env/local.env`, not in JSON config files.

## Quick Rule

- Code change: edit `packages/clawroute/`.
- Runtime model/provider policy: edit `config/clawroute/`.
- Secret or host-specific auth: edit `private/env/local.env` or
  `private/compose.local.yml`.
