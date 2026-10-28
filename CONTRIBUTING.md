# Contributing

Thanks for improving Spartan Gate. Keep changes small, documented, and aligned
with the security boundaries in [docs/security.md](docs/security.md).

## Development Setup

```sh
git clone https://github.com/Heeervas/spartan-gate.git
cd spartan-gate
npm ci --ignore-scripts
python3 -m venv .venv/test
.venv/test/bin/python -m pip install -r infra/spartan-route/requirements.txt PyYAML==6.0.3 python-dotenv==1.2.1 packaging==26.3 requests==2.32.5 httpx==0.28.1 ruamel.yaml==0.18.16
export PATH="$PWD/.venv/test/bin:$PATH"
```

Use Node.js 22, Python 3.14 and Docker with the Compose plugin. See
[Development](docs/development.md) for the host requirements fallback in a public
clone, isolated Pi loader provisioning and mandatory pristine-source gates.
Keep writable dependencies checkout-local; do not symlink environments or
`node_modules` to another worktree.

## Before A Pull Request

Run the narrowest useful checks first, then broaden when the change touches a
shared security or runtime boundary.

```sh
npm run test:route
npm run test:hermes
npm run test:pi
npm run test:camofox
npm run test:scripts
npm run test:reader
docker compose -f infra/compose/compose.yml config --quiet
scripts/doctor.sh
```

Portable fixture success does not satisfy source-backed integration. Report
skips and missing native inputs explicitly; do not mark them passed. No stack
start, image build, private render or live doctor run is needed for fixture CI.

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
