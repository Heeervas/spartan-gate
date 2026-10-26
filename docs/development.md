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
npm install --prefix .agents/pi-web-test --ignore-scripts --no-audit --no-fund pi-web-access@0.44.0
export PI_WEB_ACCESS_EXTENSION="$PWD/.agents/pi-web-test/node_modules/pi-web-access/dist/index.js"
```

Operator-local `.agents/test-requirements.txt`, when provisioned, pins the same
minimal host requirements. It is not the complete Hermes native dependency set.
No global installation is needed. Spartan Route owns its runtime dependency pin
in `infra/spartan-route/requirements.txt`; the retired TypeScript router has no
build/test/package lane. Keep `config/clawroute` and routing auto aliases used by
Spartan Route.

`npm run test:pi` also exercises unmodified OpenAI SDK 7.19.0 (pinned by Pi),
actual loopback Route/native selection and synthetic upstream HTTP. It proves
standard Responses tool/encrypted continuation and fresh-process resume without
client signing. No real provider, private config or active Pi install is used.
The native test-input locator supports both hoisted and bundled package layouts.
Keep the 1.3.0 compatibility gate; additionally run the Pi suite with an explicit
existing 1.4.0 package directory for current-consumer acceptance. Do not upgrade a
global Pi installation just to run tests. See [the contract and rollback limits](pi-native-responses.md)
and the standard replay section of the local `.agents/validation.md` authority.

## Portable current-stack gates

```sh
npm run test:route    # Python API/transport/telemetry and JavaScript dashboard
npm run test:hermes   # surviving Python patches and host fixtures
npm run test:pi       # gateway and actual Pi extension loader
npm run test:camofox  # Python and JavaScript browser-boundary fixtures
npm run test:scripts  # operator helpers and full public Compose matrix
npm run test:reader  # SSRF, total fetch deadline and bounded DNS/worker lifecycle
npm run test:tools   # scoped auth/transport/browser policy and synthetic installer
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

`test:route` seals its Python fixture environment/auth state and denies
non-loopback DNS/TCP before discovery. Focused Route Python tests must use
`python3 infra/spartan-route/run_tests.py --pattern <test-file.py>` (optional
`-k <name>`), not direct unsealed discovery. Actual catalog parsing/publication
is still exercised through owned loopback HTTP with synthetic auth; no feature
is disabled to make tests green. This is a process-local Route fixture boundary,
not an OS/subprocess/UDP sandbox. Caddy fixtures also bind scratch HOME/XDG paths.

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

## Shared tools: isolated native/container acceptance

The [shared-tool contract](shared-tool-access.md) remains opt-in and deployment
pending until separately activated. `test:pi` requires both actual Pi program code
and the pinned public web extension above. Keep Pi 1.3 compatibility and additionally
bind **both** `PI_CODING_AGENT_DIR` and `PI_TEST_PACKAGE_DIR` to an explicitly
provisioned Pi 1.4 package for current-consumer acceptance. These variables are
program test inputs before fixture setup, not an instruction to load active homes.

Source preflight starts no services:

```sh
python3 infra/tool-access/isolated_acceptance.py \
  --browser-image "$TOOL_TEST_BROWSER_IMAGE" --caddy-image "$TOOL_TEST_CADDY_IMAGE" \
  --patched-server-source "$CAMOFOX_PATCHED_SERVER"
```

Exact existing program images and the extracted **program-only** `server.js` must
be explicitly provisioned; never search private stores or copy a live container's
runtime/profile. No pull/build is performed. `CAMOFOX_PATCHED_SERVER` also enables
the native already-patched-source upgrade regression. After resource/operation
authority, the separate real joint fixture command is:

```sh
python3 infra/tool-access/isolated_acceptance.py \
  --browser-image "$TOOL_TEST_BROWSER_IMAGE" --caddy-image "$TOOL_TEST_CADDY_IMAGE" \
  --patched-server-source "$CAMOFOX_PATCHED_SERVER" \
  --python-image "$TOOL_TEST_PYTHON_IMAGE" --execute --resource-approved \
  --native-pi-package "$PI14_PACKAGE" --web-access-extension "$PI_WEB_ACCESS_EXTENSION"
```

The Python/aiohttp image runs only fixture entrypoints, never a router/provider.
One 2-GiB browser at a time, sufficient host reserve and 5-GB free-disk floor are
required. Only fresh synthetic credentials/data and owned networks are used;
cleanup is label-scoped, never main-stack `--remove-orphans`. Three networks are
internal; a dedicated edge publishing bridge enables effective loopback binding.
The restricted client must fail backend/intermediary/legacy and synthetic
publishing-peer HTTP attempts. Edge itself has normal bridge reachability.

Report real versus fixture proof precisely: actual Caddy and Camofox/PNG;
actual Reader HTTP with a declared test-only public-address connection mapping;
fresh native Pi SDK processes with actual tool/image hooks and disk resume/fork,
but a **synthetic model** (no paid inference). Browser runs existing test-only
analytics/addons-disabled mode, **not default-UBO runtime proof**. No active Pi,
production credentials or deployment is used. Candidate hashes, eight phase
receipts and cleanup remain separate from later operational acceptance.

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
