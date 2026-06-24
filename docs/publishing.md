# Publishing

Use this before pushing Spartan Gate as the definitive public repo.

## Public Contract

The public repo should contain source, public examples, and docs only. It must not contain:

- `AGENTS.md`
- `.agents/`
- `.github/agents/`
- `.github/hooks/`
- `.github/prompts/`
- `.github/skills/`
- `private/`
- `runtime/`
- `.env`
- real tokens or OAuth files
- certificates or private keys
- `node_modules/`
- `dist/`
- personal host paths
- LAN-wide or `0.0.0.0` edge binds

The public CI workflow under `.github/workflows/` stays tracked. Agent packs,
local prompts, local hooks, and per-operator instructions are intentionally
ignored so each operator can maintain their own private agent environment.

## Pre-Push Checks

```sh
scripts/doctor.sh
npm --prefix packages/clawroute ci
npm --prefix packages/clawroute test
npm --prefix packages/clawroute run build
docker compose -f infra/compose/tiers/compose.l0.yml config --quiet
docker compose -f infra/compose/tiers/compose.l0.yml -f infra/compose/tiers/compose.clawroute.yml config --quiet
docker compose -f infra/compose/tiers/compose.l1.yml config --quiet
docker compose -f infra/compose/tiers/compose.l1.yml -f infra/compose/tiers/compose.clawroute.yml config --quiet
docker compose -f infra/compose/tiers/compose.l2.yml config --quiet
docker compose -f infra/compose/tiers/compose.l2.yml -f infra/compose/tiers/compose.l3.yml config --quiet
docker compose -f infra/compose/compose.yml --env-file .env.example config
git status --short --ignored
```

Expected ignored entries may include:

```text
!! AGENTS.md
!! .agents/
!! .github/agents/
!! .github/hooks/
!! .github/prompts/
!! .github/skills/
!! packages/clawroute/dist/
!! packages/clawroute/node_modules/
!! private/
```

Those ignored paths should not be staged.

## Commit And Push

```sh
git status --short
git log --oneline --max-count=5
git remote -v
git push origin master
```

If `git status --short` shows private files staged, stop and unstage them before pushing.

## Public README Promise

After publishing, a new user should be able to:

1. Clone the repo.
2. Run `scripts/doctor.sh`.
3. Run `scripts/install.sh --tier L0` for a portable Hermes baseline.
4. Move upward through L1-L4 with `sg-tier set` when they need more boundary services, or add ClawRoute with `--with clawroute`.
5. Run `docker compose ... config` for the selected public topology.
6. Replace or extend the bundled agent privately when they need a different runtime.

No public setup step should require checked-in local agent instructions.

## OSS Readiness Checklist

Before treating the repo as publishable:

- Confirm `AGENTS.md`, `.agents/`, `.github/agents/`, `.github/hooks/`,
  `.github/prompts/`, `.github/skills/`, `private/`, `runtime/`, `.env`, build
  output, and browser profile data are ignored and not staged.
- Run the layer Compose matrix from the pre-push checks, including L0/L1 with
  the ClawRoute addon.
- Review public docs for personal paths, account names, private domains, real
  hashes, certificates, OAuth files, Codex auth paths, and LAN-wide binds.
- Scan Git history for secret-like strings and personal paths. If a real secret
  was committed, stop, rotate it, and prepare a separate history-rewrite plan.
- Verify fresh installs do not seed `web-search-plus`; native Hermes SearXNG is
  the documented search path for layers that include SearXNG.

## Current Readiness Posture

The public repo is ready to present as a credible OSS baseline for local/private
agent hardening when the pre-push checks pass. Describe it as defensive
infrastructure for private deployments, not as a turnkey public-Internet
security product.

Keep these caveats visible in announcements and release notes:

- Public Compose edge bindings are localhost-first. Tailscale or other private
  exposure belongs in ignored private overrides.
- Browserless debugger WebSockets use `BROWSERLESS_EDGE_TOKEN` instead of
  browser Basic Auth so Safari/Firefox login flows do not loop during WebSocket
  upgrades.
- Hermes dashboard `/api/*` and `/ws/*` remain same-origin pass-through routes
  behind the private edge so the dashboard can call its own backend.
- Reader reduces SSRF risk with request validation and checked connection
  resolution, but fetched web content is still untrusted input.
- `scripts/doctor.sh`, CI, and `npm audit --omit=dev` are readiness gates, not
  substitutes for reviewing a private deployment's exposure model.
