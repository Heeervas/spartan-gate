# Development

## Authority and safety

`AGENTS.md` and `.agents/` are operator-local and ignored, not prerequisites
available in a public clone. Tracked development/CI commands below are the public
mirror of the operator's `.agents/validation.md`. Read security, setup,
operations and publishing docs for their respective contracts. Preserve unrelated
worktree changes. Never inspect private profiles to obtain test inputs.

Use Node 22, Python 3.14 and Docker with the Compose plugin. Start with focused
tests; broaden for shared/security boundaries. Fixture validation never requires
starting the stack, rebuilding images or rendering private configuration.

## Isolated host setup

```sh
npm ci --ignore-scripts
python3 -m venv .agents/test-venv
.agents/test-venv/bin/python -m pip install -r infra/spartan-route/requirements.txt PyYAML==6.0.3 python-dotenv==1.2.1 packaging==26.3 requests==2.32.5 httpx==0.28.1 ruamel.yaml==0.18.16
export PATH="$PWD/.agents/test-venv/bin:$PATH"
# The native Pi loader test requires a real installed package, not a stub.
npm install --prefix .agents/pi-test --ignore-scripts --no-audit --no-fund @earendil-works/pi-coding-agent@1.3.0
export PI_CODING_AGENT_DIR="$PWD/.agents/pi-test/node_modules/@earendil-works/pi-coding-agent"
```

Operator-local `.agents/test-requirements.txt`, when provisioned, pins the same
minimal host requirements. It is not the complete Hermes native dependency set.
No global installation is needed. Spartan Route owns its runtime dependency pin
in `infra/spartan-route/requirements.txt`; the retired TypeScript router has no
build/test/package lane. Keep `config/clawroute` and routing auto aliases used by
Spartan Route.

## Portable current-stack gates

```sh
npm run test:route    # Python API/transport/telemetry and JavaScript dashboard
npm run test:hermes   # surviving Python patches and host fixtures
npm run test:pi       # gateway and actual Pi extension loader
npm run test:camofox  # Python and JavaScript browser-boundary fixtures
npm run test:scripts  # operator helpers and full public Compose matrix
npm run test:reader  # SSRF and bounded inbound lifecycle
python3 -m unittest discover -s infra/caddy -p 'test_edge_sessions.py'
find scripts apps/hermes-agent infra -name '*.sh' -type f -print0 | xargs -0 -r -n 1 bash -n
docker compose -f infra/compose/compose.yml config --quiet
docker compose -f infra/compose/compose.yml --env-file .env.example config --quiet
git diff --check
```

Source-backed Hermes/Camofox tests can skip without native inputs. Those skips
must be reported: a green portable lane does **not** satisfy the native gate.
Hermes JavaScript tests were dedicated to the retired Browserless implementation;
there are no surviving `apps/hermes-agent/test_*.js` entries. Do not run a literal
unmatched Node glob and mistake its zero-test success for coverage.

Doctor is an operator preflight, not an unconditional public CI command. Its
security tests use disposable public trees and synthetic Compose secrets. Run
real `scripts/doctor.sh` only with explicit local operator authority; it reads
local configuration and Docker metadata. Do not paste private renders in logs.

## Mandatory pristine-source integration

Provide **unmodified program source**, not installed patched source or a profile:

- Hermes commit `e8c97320ac8691d4de92af49f98459f9ef9ddb08`, corresponding to
  base OCI digest `sha256:5edbc9c277d22f1b74f2707b23b77ebdd7f412d34511363a20df880004a42fde`
  in `apps/hermes-agent/Dockerfile` (see `docs/patches.md`). Set
  `HERMES_UPSTREAM_SOURCE` to the extracted pristine root and
  `HERMES_SYNC_SOURCE` to its `tools/skills_sync.py`. Add that root to
  `PYTHONPATH` and provision the upstream-declared native dependencies in an
  isolated compatible environment. Minimal host packages alone are insufficient.
- Camofox v1.15.0 commit `771b610a7b5994759c138b912741de58b0edd588` (see
  `docs/camofox-refresh.md`). Set `CAMOFOX_UPSTREAM_SOURCE` to its pristine
  extracted tree. Set `CAMOFOX_EXPRESS_MODULE` to an actual installed Express
  module from the matching upstream lockfile dependency installation, not a mock.

Verify extraction provenance against those immutable revisions before execution.
Patch-chain tests use disposable copies and must not modify the supplied source.
Paths below must be supplied by the operator/source provisioning lane, not guessed
by searching home directories or runtime stores:

```sh
: "${HERMES_UPSTREAM_SOURCE:?provide pinned pristine Hermes program source}"
: "${CAMOFOX_UPSTREAM_SOURCE:?provide pinned pristine Camofox program source}"
: "${CAMOFOX_EXPRESS_MODULE:?provide installed upstream Express module}"
export HERMES_SYNC_SOURCE="$HERMES_UPSTREAM_SOURCE/tools/skills_sync.py"
export PYTHONPATH="$HERMES_UPSTREAM_SOURCE${PYTHONPATH:+:$PYTHONPATH}"
npm run test:hermes:native
npm run test:camofox:native
```

The Hermes native command discovers the seven source-consuming suites and fails
if any test skips, independently of portable/root-only authority fixtures. The
Camofox command requires both real inputs before executing its full suite; review
the raw TAP result for zero source/Express skips and cancellations.
Missing sources/dependencies, incompatible revisions, skipped required tests or
cancellations are **INCOMPLETE/FAIL**, never accepted skips. CI's
`native-source-required` job fetches the exact public commit archives, checks
SHA-256 before extraction and installs Camofox dependencies from its lockfile
with install scripts disabled. The tested archive SHA-256 values are
`64f49146525ad21e63e4a9b0cf3418a35ead2173989a94c7b5e1304078625cf9` for Hermes
and `8fe0ae67988f29ee9da75c55aa277a8c2744c3cedbc53cdc53f2f1a04b0ee0a8` for Camofox.
See `.github/workflows/ci.yml` for the executable clean-clone provisioning lane.
An upstream archive byte change fails the checksum gate rather than silently
accepting new inputs. Portable CI alone cannot make full acceptance green.

## Native edge security gate

The edge session implementation additionally requires a real compatible Caddy
binary via `CADDY_BIN` and loopback sockets:

```sh
: "${CADDY_BIN:?provide a compatible native Caddy binary}"
python3 -m unittest discover -s infra/caddy -p 'test_*.py'
```

The native edge test deliberately fails without that binary; portable session
unit tests do not prove Caddy forward-auth, HTTP/WebSocket behavior or expiry.
The separate native CI lane extracts Caddy from the pinned stock image archive,
without starting a container, then runs the actual isolated loopback gate.
Portable CI alone does not prove this contract. Missing native inputs are
INCOMPLETE, not a skip or deployment claim.

## Deterministic quality and review

`npm run pi:quality` requires the explicitly provisioned canonical
builder-assurance v1.1.0 package via `BUILDER_ASSURANCE_DIR` (default documented
operator home), its isolated Python requirements and root pinned Oxlint 1.80.0.
It validates policy, collects fresh metrics and reports the immutable baseline
`75651cc0c5baa1db5a6523b790564988698f2a43`, all-scope, TOP candidate. The policy,
receipts and canonical commands are operator-local `.agents/` surfaces; portable
CI neither provisions that external package nor claims this gate is enforced.
Do not add an unprovisioned home-path invocation to public CI. Shell, Caddy,
Dockerfile and Compose semantics still require native syntax/config checks and
independent review; Python/JS metrics do not prove them correct.

Keep behavioral checks, deterministic quality and independent review separate.
A missing required native/quality gate blocks acceptance. Fixture success is not
deployed behavior or authorization to activate anything.
