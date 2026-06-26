# Spartan Route V1

`infra/spartan-route/` is a parallel router package for rebuilding ClawRoute
with a smaller reliability-first surface. It does not replace
`packages/clawroute/`; both can exist in the same checkout and both can run in
Compose while cutover is evaluated.

## Implementation Choice

Options considered:

- Trim the current TypeScript ClawRoute package. This keeps the strongest
  compatibility but leaves the service inside the same large policy graph that
  caused slow recovery and fragile failure modes.
- Restart from the original ClawRoute TypeScript repo. This restores the early
  simplicity but would require replaying Codex slots, prompt-cache affinity,
  image generation, dashboards, and telemetry anyway.
- Build a Python `aiohttp` proxy. This is the v1 choice: one runtime dependency,
  simple async streaming, direct SQLite telemetry, fewer background loops, and a
  smaller failure domain. The tradeoff is that v1 intentionally supports fewer
  native provider formats than ClawRoute.
- Build in Go. This would likely be faster and easy to ship as one binary, but
  it would slow iteration on Codex auth, dashboards, and telemetry while the
  behavior is still being rediscovered.

Winner: Python `aiohttp`, because the service is an OpenAI-compatible proxy and
the bottleneck is upstream latency/reliability, not local CPU. The v1 goal is to
make the route decision and failure handling obvious.

## V1 Surface

Relevant files:

- `infra/spartan-route/Dockerfile` and `requirements.txt`: Python 3.12 runtime
  with `aiohttp`.
- `infra/spartan-route/spartan_route/app.py`: HTTP server, auth, routing,
  OpenAI-compatible endpoints, provider passthrough, image endpoints, and
  dashboard APIs.
- `infra/spartan-route/spartan_route/codex.py`: Codex OAuth slots, retry and
  cooldown handling, prompt-cache key/affinity, SSE conversion, usage checks,
  and reset-credit fetch/normalization.
- `infra/spartan-route/spartan_route/telemetry.py`: isolated SQLite schema,
  daily cache rollups, live sessions, usage snapshots, and reset-credit tables.
- `infra/spartan-route/spartan_route/config.py`: reads existing
  `config/clawroute` provider profiles while defaulting to
  `/app/data/spartan-route.db`.
- `infra/spartan-route/web/dashboard.html`: session and turn dashboard.
- `infra/spartan-route/web/dashboard-codex.html`: Codex usage, slot state,
  daily cache percentage, and mandatory reset-credit showcase.
- `infra/compose/compose.spartan-route.yml`: full-stack side-by-side overlay.
- `infra/compose/tiers/compose.spartan-route.yml`: tier-helper side-by-side
  overlay.
- `infra/spartan-route/test_*.py`: focused regression suite.

Kept in v1:

- OpenAI-compatible `/v1/chat/completions`, `/v1/responses`, `/v1/models`,
  `/v1/images/generations`, and `/v1/images/edits`.
- `clawroute/auto` and `custom-1/clawroute/auto` model aliases for Hermes.
- Multiple Codex auth slots from `OPENAI_CODEX_AUTH_PATHS`.
- Prompt-cache keys based on explicit `prompt_cache_key`, `user`, or stable
  system/tool prefix shape.
- Per-key Codex cache leases, rate-limit cooldowns, transient 5xx cooldowns,
  and retry across slots/accounts.
- Codex image generation and no-mask image edits, with OpenAI image endpoint
  passthrough when `OPENAI_API_KEY` is available.
- Reset-credit telemetry and dashboard display. V1 only reads and persists
  reset-credit metadata; it never redeems credits.
- Daily cache hit rows from `routing_log`.

Reliability contract:

- Codex Responses streams are converted to Chat Completions with explicit
  handling for text, tool calls, structured reasoning, raw/encrypted reasoning,
  incomplete responses, and terminal upstream errors.
- A successful upstream Codex response with no usable text, tool call, or
  reasoning payload is not returned to Hermes as `200` empty content. Spartan
  Route classifies it as retryable `502 codex_empty_response`, rotates to the
  next eligible slot/account, and only returns that error after slot retry is
  exhausted.
- An incomplete Codex response that contains tool calls is treated as retryable
  `502 codex_incomplete_response`; Spartan Route must not expose incomplete
  tool-call arguments to Hermes as a successful `finish_reason="length"` turn.
- Streaming tool-call deltas are buffered and emitted only after a terminal
  successful Codex event, so Hermes never receives partial tool arguments from
  an upstream stream that later becomes incomplete.
- Cache diagnostics in `context_info` are metadata-only: cache-key hash,
  message/tool counts, request-shape hashes, tool-schema fingerprint, and
  same-session prefix comparison. Raw prompts, raw tool schemas, and auth values
  are not added to these diagnostics.

Cut from v1 on purpose:

- Balance-loader scheduling, early activation, manual slot activation, capacity
  multipliers, cold-migration approval flow, cache-miss breaker, alerting, and
  the analysis dashboard.
- Native Anthropic Messages format. Use OpenAI-compatible Chat Completions or
  OpenRouter-compatible models.
- Broad provider-specific translation. V1 passes through OpenAI-compatible
  providers and returns clean JSON for unsupported native providers.

## Run

Tier overlay:

```sh
docker compose \
  -f infra/compose/tiers/compose.l1.yml \
  -f infra/compose/tiers/compose.spartan-route.yml \
  --env-file private/env/local.env \
  config --quiet
```

Full-stack side-by-side overlay:

```sh
source scripts/aliases.sh
sg route up -d
```

This uses the normal full-stack private Compose path plus
`infra/compose/compose.spartan-route.yml` and optional
`private/compose.spartan-route.local.yml` overrides. Hermes points at
`http://spartan-route:18790/v1` in this mode. Plain `sg up -d` remains the
normal ClawRoute path and does not load Spartan Route-only private overrides.

Spartan Route is exposed through Caddy on `SPARTAN_ROUTE_EDGE_PORT`, default
`18791`. For local private stacks that still reserve legacy Mission Control
port `18791`, set `SPARTAN_ROUTE_EDGE_PORT=18792`. Private HTTPS stacks should
use the Spartan Route URL printed by `sg-urls`; plain HTTP stacks use
`http://localhost:<SPARTAN_ROUTE_EDGE_PORT>/dashboard-codex`. The existing
ClawRoute service and `packages/clawroute/` remain untouched.

## Validation

```sh
python3 -m py_compile infra/spartan-route/spartan_route/*.py infra/spartan-route/test_*.py
docker build -t spartan-route:test infra/spartan-route
docker run --rm -v "$PWD/infra/spartan-route:/tests:ro" -w /tests \
  spartan-route:test python3 -m unittest discover -s /tests -p 'test_*.py'
docker compose -f infra/compose/compose.yml -f infra/compose/compose.spartan-route.yml config --quiet
docker compose -f infra/compose/tiers/compose.l1.yml -f infra/compose/tiers/compose.spartan-route.yml config --quiet
```
