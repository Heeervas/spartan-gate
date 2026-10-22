# Validation authority

## Current-stack integration and retirement

Public mirror: `docs/development.md`. Existing policy baseline, TOP floor,
external measurement dependency and exception contract below remain unchanged.
Local minimal host requirements are pinned in `.agents/test-requirements.txt`;
public CI explicitly provisions the same pins because `.agents/` is ignored.

```sh
.agents/quality-venv/bin/python -m pip install -r .agents/test-requirements.txt
export PATH="$PWD/.agents/quality-venv/bin:$PATH"
npm ci --ignore-scripts
npm run test:route
npm run test:hermes
npm run test:pi
npm run test:camofox
npm run test:scripts
npm run test:reader
python3 -m unittest discover -s infra/caddy -p 'test_edge_sessions.py'
python3 -m unittest scripts.test_doctor_security scripts.test_tooling_camofox
find scripts apps/hermes-agent infra -name '*.sh' -type f -print0 | xargs -0 -r -n 1 bash -n
docker compose -f infra/compose/compose.yml config --quiet
docker compose -f infra/compose/compose.yml --env-file .env.example config --quiet
git diff --check
```

Pi tests require the real installed Pi 1.3.0 package: provision it in an isolated
prefix as documented in Development or set `PI_CODING_AGENT_DIR` explicitly to
that package directory. Here this variable is a test loader input, not the agent
configuration home. No global install is required.

Mandatory source gate inputs: pristine Hermes commit
`e8c97320ac8691d4de92af49f98459f9ef9ddb08`, matching Dockerfile digest; pristine
Camofox v1.15.0 commit `771b610a7b5994759c138b912741de58b0edd588`; actual upstream
Hermes native dependencies and installed matching Camofox Express module. Set
`HERMES_UPSTREAM_SOURCE`, `HERMES_SYNC_SOURCE` to its `tools/skills_sync.py`,
`PYTHONPATH` to the native checkout, `CAMOFOX_UPSTREAM_SOURCE` and
`CAMOFOX_EXPRESS_MODULE` to explicitly provisioned program inputs. Never discover
these by traversing private profiles or parent directories. CI fetches the pinned public program archives and validates exact SHA-256
before extraction; the executable clean-clone procedure is in
`.github/workflows/ci.yml`. Required native host dependencies include
`ruamel.yaml==0.18.16`; the local pinned requirements include it.

```sh
npm run test:hermes:native
npm run test:camofox:native
```

Native edge acceptance additionally requires `CADDY_BIN` pointing at a real
compatible Caddy binary and `python3 -m unittest discover -s infra/caddy -p
 'test_*.py'`; missing Caddy intentionally fails its native test. Session unit
tests alone are not HTTP/WebSocket/expiry integration evidence.

The Hermes native command fails on missing inputs or any skip in the seven
source-consuming suites. Camofox requires real source/Express inputs before the
full suite; zero cancellations and zero source/Express skips are required.
Portable results may contain documented skips but never satisfy native
acceptance. CI covers current stack fixtures and a separate real pinned-source/native
Caddy integration lane. It does not
pretend the external home shared assurance package is provisioned in CI.
The retired Hermes Browserless JavaScript suite has no surviving test files;
its unmatched glob must not be run or counted as passing coverage.

Doctor scratch/confidentiality is tested only with synthetic public fixtures.
This integration does not authorize real doctor/private renders, stack starts,
image builds, deployments or runtime profile operations.


## Optional quality CLI image acceptance

The build/recreation and runtime canary contract is documented in
`docs/operations.md` under Optional quality CLIs; accepted evidence is in
`docs/reviews/hermes-quality-cli-20261021.md`. These are explicit operational
checks, not a new native lint/quality gate. Use the reviewed deployment's actual
runtime UID/GID, writable scratch/cache and read-only root. Preserve profile
disables/membership and existing networking/resource policy. Optional missing
analyzers remain reported coverage gaps.

Proven operator build/activation commands, after source review and capacity gates:

```sh
sg build hermes
# Run the isolated image canary before activation.
sg up -d --no-deps --no-build --pull never --force-recreate hermes
```

The image canary must exercise canonical Qlty commands, pinned version/ownership,
Desloppify import isolation, actual Bandit detection and preparation. Repeat the
bounded local check in the live container and verify ordinary/login-shell
availability and native controls before accepting deployment. The 2026-10-21
canary used `--skip-slow` for explicitly omitted optional duplication coverage.

## Native external skill links checks

Scope: the external-skill sync build-time patch, its focused regression tests, and Dockerfile integration. TOP quality floor; immutable baseline `75651cc0c5baa1db5a6523b790564988698f2a43`. This scoped policy does not claim repository-wide enforcement.

The external canonical measurement dependency is builder-assurance v1.1.0, resolved at `$HOME/.agents/skills/shared/builder-assurance`. Its schema is vendored unchanged. Use the isolated Python setup below; do not resolve retired aliases or mutate the shared package. Run from the repository root:

```sh
python3 "$HOME/.agents/skills/shared/builder-assurance/scripts/validate_quality_policy.py" .agents/quality.yaml
python3 "$HOME/.agents/skills/shared/builder-assurance/scripts/quality_report.py" --repo . --base 75651cc0c5baa1db5a6523b790564988698f2a43 --scope all --level top --config .agents/quality.yaml --format json
python3 -m unittest discover -s apps/hermes-agent -p test_external_skill_links_patch.py
git diff --check
```

Behavior tests require `HERMES_SYNC_SOURCE` pointing to the unmodified supported upstream `tools/skills_sync.py`, and a native Hermes checkout on `PYTHONPATH`. They run actual patched upstream code with disposable profile, bundled and external trees; no live home writes. The patch CLI defaults to `/opt/hermes/tools/skills_sync.py` and accepts an explicit disposable target path. Build/recreate acceptance remains separate.


## Pi and Spartan Route usage

The accepted Pi/Route usage implementation extends the existing TOP policy to
`infra/pi/**`, `infra/spartan-route/**`, and the quality runner. Preserve the
immutable baseline above and existing Hermes coverage. Oxlint is pinned to 1.80.0
in the root development dependencies. The canonical measurement dependency is
builder-assurance v1.1.0 under `$HOME/.agents/skills/shared/builder-assurance`;
`BUILDER_ASSURANCE_DIR` may select an explicitly provisioned equivalent package.
Provision its declared pinned Python requirements in the repository-local ignored
`.agents/quality-venv`. The runner automatically selects that environment when
present; `BUILDER_ASSURANCE_PYTHON` explicitly overrides it. Without either it
uses `python3`, which must already satisfy the declared requirements. Missing
dependencies are failures, not skipped checks. No global installation is needed.

Run from the repository root:

```sh
python3 -m venv .agents/quality-venv
.agents/quality-venv/bin/python -m pip install -r "$HOME/.agents/skills/shared/builder-assurance/requirements.txt"
npm ci --ignore-scripts
npm run pi:quality
python3 -m unittest discover -s infra/spartan-route -p 'test_*.py'
node --test infra/pi/test_*.mjs
python3 -m unittest discover -s infra/pi -p 'test_*.py'
git diff --check
```

Pi native-loader checks require the installed `@earendil-works/pi-coding-agent`
package; set `PI_CODING_AGENT_DIR` to its package directory for non-global layouts.
Route API tests require loopback socket access and its pinned requirements.
The quality runner collects snapshot-bound metrics immediately before reporting;
behavioral checks and independent review remain separate acceptance gates.


## Authorized audit remediation assurance

The reviewed-plan prerequisite extends the existing scope additively to Python
operator helpers (`scripts/*.py`), Hermes Python patches and their focused Python
tests, proxy bootstrap, Reader, and the affected public proxy/DNS/Caddy/Compose
boundaries. Existing Pi/Route/Hermes coverage, TOP floor, all-scope dirty/untracked
handling, thresholds, immutable baseline and independently approved exceptions
remain unchanged. This is not repository-wide coverage. Changes outside the
allow-list require an explicit coverage decision before their implementation.
The coordinator owns acceptance and independent policy review; policy edits do
not approve themselves.

Semantic measurement coverage is **Python AST** and pinned **JS/TS cyclomatic
complexity** only. `core_source_v1` inventories changed files; it does not parse
or validate shell, Dockerfile, Compose/YAML, Caddy, HTML or configuration semantics.
The canonical reporter does not currently emit an unsupported-language gap for
all these formats: its PASS must not be read as semantic coverage for them.
Applicable shell syntax, fixture regressions, public Compose matrix, Caddy
contracts and security review remain separate required gates in the remediation
plan/AGENTS.md. Missing semantic adapters remain a disclosed coverage limitation,
not an invented AST adapter or permission to skip native validation. Do not run
the real doctor before its confidentiality fix; use synthetic fixtures.

Focused prerequisite checks (no images, stack starts or private configuration):

```sh
bash -n scripts/check-pi-route-quality.sh
.agents/quality-venv/bin/python "$HOME/.agents/skills/shared/builder-assurance/scripts/validate_quality_policy.py" .agents/quality.yaml
npm run pi:quality
git diff --check
```

The runner validates policy, collects fresh metrics, then reports the same
baseline/all-scope TOP snapshot. Canonical artifacts remain ignored at
`.agents/oxlint-metrics.json` and `.agents/pi-route-quality.json`; retain raw
command receipts under ignored `.agents/quality-receipts/` when freezing a batch.
Recollect immediately before the report after every coherent source edit.
A scoped PASS_WITH_EXCEPTION is not product, infrastructure or release acceptance.
Existing/new debt may still fail on subsequent production edits: report exact
findings; never move the baseline or self-approve exceptions. Manual execution
here does not claim CI enforcement.

### Reviewed partial commits

For a reviewed staged batch while an independent worker's untracked candidate
remains in the integration tree, collect and report the exact index candidate.
This does not replace the final all-scope feature gate. Use the same immutable
baseline and policy, and verify staged source bytes match the tested/reviewed ones:

```sh
python3 "$HOME/.agents/skills/shared/builder-assurance/scripts/collect_oxlint_metrics.py" --repo . --base 75651cc0c5baa1db5a6523b790564988698f2a43 --scope index --oxlint ./node_modules/.bin/oxlint --output .agents/oxlint-metrics.json
python3 "$HOME/.agents/skills/shared/builder-assurance/scripts/quality_report.py" --repo . --base 75651cc0c5baa1db5a6523b790564988698f2a43 --scope index --level top --config .agents/quality.yaml --format json --output .agents/pi-route-quality.json
```
