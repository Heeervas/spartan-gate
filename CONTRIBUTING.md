# Contributing

Thanks for improving Spartan Gate. Keep changes small, documented, and aligned
with the security boundaries in [docs/security.md](docs/security.md).

## Development Setup

```sh
git clone https://github.com/Heeervas/spartan-gate.git
cd spartan-gate
npm --prefix packages/clawroute ci
```

Use Node.js 22 for ClawRoute work and Docker with the Compose plugin for
Compose validation.

## Before A Pull Request

Run the narrowest useful checks first, then broaden when the change touches a
shared security or runtime boundary.

```sh
npm run clawroute:build
npm run clawroute:test
python3 -m unittest discover -s apps/hermes-agent -p "test_*.py"
node --test apps/hermes-agent/test_*.js
python3 -m unittest discover -s scripts -p "test_*.py"
python3 -m unittest discover -s infra/reader -p "test_*.py"
docker compose -f infra/compose/compose.yml config --quiet
scripts/doctor.sh
```

## Security Rules

- Keep Hermes off the external network.
- Keep public edge bindings on localhost.
- Keep general HTTP and DNS egress whitelist controlled.
- Keep Reader GET-only and SSRF-guarded.
- Keep real credentials, private paths, browser profiles, local domains, and
  operator notes out of tracked files.
- Make invalid, expired, overflowing, or malformed policy data fail closed.

## Pull Request Expectations

- Explain the behavior change and the validation you ran.
- Include focused tests for bug fixes when practical.
- Update public docs when operator-visible behavior changes.
- Do not commit `private/`, `.env`, `runtime/`, `node_modules/`, `dist/`, or
  browser profile data.
