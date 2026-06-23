# Development

## Local Agent Instructions

`AGENTS.md` is intentionally local and ignored. Each operator should create
their own copy for machine-specific agent preferences, telemetry paths, private
runbooks, or working style. Do not rely on `AGENTS.md` for public project
contracts.

Durable project rules belong in tracked docs:

- security and network invariants: `docs/security.md`
- setup, operation, and publishing contracts: `docs/setup.md`,
  `docs/operations.md`, and `docs/publishing.md`
- current public development workflow: this file

Before changing behavior, inspect the current worktree and preserve unrelated
local changes. Prefer `rg` and targeted line ranges before reading large files.
For ordinary scoped work, make the change, run the narrowest useful validation,
and expand tests according to blast radius.

## ClawRoute

```sh
npm --prefix packages/clawroute ci
npm --prefix packages/clawroute test
npm --prefix packages/clawroute run build
```

## Compose

```sh
docker compose -f infra/compose/compose.yml config
```

Do not run `docker compose up` during validation unless you explicitly intend to start the stack. For substrate-only checks, target individual services.

For a substrate-only smoke test:

```sh
docker compose -f infra/compose/compose.yml config
docker compose -f infra/compose/compose.yml up -d proxy dns searxng reader clawroute browserless caddy
```

Stop the smoke stack with:

```sh
docker compose -f infra/compose/compose.yml down
```

## Repository Checks

```sh
scripts/doctor.sh
```

Keep source changes scoped. Public files should stay free of personal paths, private env files, generated builds, dependency folders, certificates, and runtime state.

Scratch notes can be useful for coordination, but they are not a durable public
explanation of shipped behavior. Promote lasting decisions to tracked docs
before publishing.
