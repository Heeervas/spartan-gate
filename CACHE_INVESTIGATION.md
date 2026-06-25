# ClawRoute Prompt Cache Investigation

Date: 2026-06-25

Scope: production Hermes -> ClawRoute -> Codex/OpenAI-compatible endpoint path for `market-radar-discovery` and `market-radar-source-hardening-review`, with `market-radar-watchlist-surveillance` included where it provides the clearest same-day comparison.

All private values are redacted or shortened. This document does not include secrets, raw file contents, authorization headers, cookies, or full account inventories.

## 1. Executive Conclusion

The confirmed production problem is not a single terminal-specific cache bug. The strongest confirmed causes are:

1. **Cache-domain instability from ClawRoute slot/account selection.** Recent market-radar rows keep the same model, cache key hash, and tool-schema fingerprint inside a turn, but some turns switch Codex slots early. `market-radar-discovery` on 2026-06-25 moved slot `3 -> 4 -> 3` in calls 1-3; `market-radar-watchlist-surveillance` moved `4 -> 3` in calls 1-2. Those moves send identical-prefix traffic to a different Codex account/cache domain, producing expected cold misses.
2. **The current in-memory cache lease is global, not keyed per prompt/session.** Current code has one `activeCacheLease` in [codex-transport.ts](packages/clawroute/src/codex-transport.ts:175). A different affinity clears it in `applyCacheLease`, so concurrent sessions can evict each other's cache affinity even with `CODEX_BALANCE_LOADER_MODE=on`.
3. **Daily cold starts are expected unless extended retention is explicitly available and the prefix remains byte-stable.** Current OpenAI guidance says `gpt-5.5` supports extended prompt-cache retention up to 24 hours, with `24h` as the supported policy. ClawRoute does not currently send `prompt_cache_retention`, and several market-radar prefixes changed across runs anyway.
4. **Terminal is correlated, not causal.** Terminal-associated requests can be cold, but terminal-associated rows also hit 90-99% cached in the same telemetry. In the current logs, terminal rows preserve the same model, slot, cache key, tool count, and tool fingerprint as hot read/search rows.
5. **Large early file reads dilute percentages and sometimes expose cold cache domains, but do not by themselves invalidate the earlier prefix.** Later rows in the same turns show 66K-96K cached tokens after large reads, proving the stable prefix can be reused.

OpenAI documentation relevant to the analysis:

- Prompt caching threshold, routing, and retention: https://developers.openai.com/api/docs/guides/prompt-caching
- Tool ordering and append-only conversation guidance: https://developers.openai.com/cookbook/examples/prompt_caching101 and https://developers.openai.com/cookbook/examples/prompt_caching_201

## 2. Hermes Configuration Precedence

Observed runtime source chain:

1. `HERMES_HOME` selects the profile home. In the running market profile, config comes from `/opt/data/profiles/market/config.yaml`.
2. Hermes loads `$HERMES_HOME/config.yaml` first, with `/opt/hermes/cli-config.yaml` as fallback only when user config is absent or `HERMES_IGNORE_USER_CONFIG=1`.
3. File config is deep-merged into defaults. Model config is normalized before the merge.
4. Environment expansion is applied to config values, for example `${OPENAI_BASE_URL}`.
5. Managed scope overlays are applied last.
6. Constructor/CLI arguments override selected runtime fields such as `model`, `provider`, `base_url`, and `max_turns`.
7. Cron job fields can override the model/provider for a job.
8. Environment variables override only specific fields. The model is intentionally not read from `LLM_MODEL` or `OPENAI_MODEL`; `config.yaml` is authoritative for model unless the caller passes a model.

Evidence:

| Setting | Intended value | Effective runtime value | Configuration source | Precedence | File and line |
| --- | --- | --- | --- | --- | --- |
| Market profile model | ClawRoute auto | `custom-1/clawroute/auto` | Profile config | Profile config over defaults | `/opt/data/profiles/market/config.yaml:1-4` |
| Market profile API mode | Chat Completions into ClawRoute | `chat_completions` | Profile config | Profile config | `/opt/data/profiles/market/config.yaml:7` |
| Market profile reasoning | High | `high` in Hermes config; not present in ClawRoute routing rows | Profile config | Profile config | `/opt/data/profiles/market/config.yaml:26` |
| Hermes base URL | ClawRoute internal endpoint | `http://clawroute:18790/v1` | `.env` via `${OPENAI_BASE_URL}` | Env expansion inside config | `/opt/data/profiles/market/.env:21` |
| Source-hardening cron model | Inherit profile | `custom-1/clawroute/auto` | Cron job `model: null` | Inherits profile | `/opt/data/profiles/market/cron/jobs.json` |
| Discovery cron model | ClawRoute auto | `clawroute/auto` with provider `custom-1` | Cron job | Cron override | `/opt/data/profiles/market/cron/jobs.json` |
| Watchlist cron model | ClawRoute auto | `custom-1/clawroute/auto` | Cron job | Cron override | `/opt/data/profiles/market/cron/jobs.json` |
| Auxiliary summary/compression | Smaller Codex model | `codex/gpt-5.4-mini` via custom-1 | Profile config | Auxiliary-only | `/opt/data/profiles/market/config.yaml:142-149` |
| Auxiliary web extract / MCP / approval | Smaller Codex model | `codex/gpt-5.4-mini` via custom-1 | Profile config | Auxiliary-only | `/opt/data/profiles/market/config.yaml:176-224` |
| Subagent model | Inherit parent | empty model/provider/base_url | Profile config | Inherit | `/opt/data/profiles/market/config.yaml:343-351` |
| Context engine | LCM | `lcm` | Profile config | Profile config | `/opt/data/profiles/market/config.yaml:333-334` |
| ClawRoute provider | Codex | `codex` | Private env | Compose env | `private/env/local.env` redacted |
| ClawRoute balancer | Enabled | `CODEX_BALANCE_LOADER_MODE=on` | Private env / live container | Compose env | `private/env/local.env` redacted |
| ClawRoute content logging | Enabled | `CLAWROUTE_LOG_CONTENT=true` | Private env / live container | Compose env | `private/env/local.env` redacted |

The running cron jobs use the market profile. Discovery explicitly overrides only the model string shape (`clawroute/auto` instead of `custom-1/clawroute/auto`); ClawRoute still resolves both forms to `codex/gpt-5.5` in the examined rows.

## 3. Effective Model Matrix

| Skill/call type | Hermes configured model | Effective model | ClawRoute route | Upstream endpoint/model | Cache supported |
| --- | --- | --- | --- | --- | --- |
| Initial `market-radar-discovery` | `clawroute/auto`, provider `custom-1` from cron | `codex/gpt-5.5` | Codex provider, slot-selected | Codex Responses body built from Chat input, model `gpt-5.5` | Yes, but first daily request is cold |
| Initial `market-radar-source-hardening-review` | Inherits `custom-1/clawroute/auto` | `codex/gpt-5.5` | Codex provider, slot-selected | Codex Responses body, model `gpt-5.5` | Yes |
| `read_file` continuation | Same parent model | `codex/gpt-5.5` | Same route unless slot selection changes | Same upstream model | Yes |
| `search_files` continuation | Same parent model | `codex/gpt-5.5` | Same route unless slot selection changes | Same upstream model | Yes |
| `terminal` continuation | Same parent model | `codex/gpt-5.5` in examined rows | Same route unless slot selection changes | Same upstream model | Yes; terminal rows can hit 90%+ |
| Browser tools | Same parent model for next LLM call | `codex/gpt-5.5` in examined rows | Same route unless slot selection changes | Same upstream model | Yes; external suffix may dilute or miss |
| Web search | Same parent model for next LLM call | `codex/gpt-5.5` in examined rows | Same route unless slot selection changes | Same upstream model | Yes; live suffix may dilute or miss |
| Alpaca/MCP | Same parent model for next LLM call; auxiliary MCP model exists for auxiliary operations | `codex/gpt-5.5` in market rows | Same route unless slot selection changes | Same upstream model | Yes |
| Subagent | Empty override means inherit parent | Not observed in market rows as separate model | Inherits unless caller overrides | Inherits | Expected yes |
| Fallback/retry | No model change observed in market rows | `codex/gpt-5.5` | Retry may advance slot on 401/429/5xx | Same model, possibly different account | Cache can be lost if slot/account changes |

There is no telemetry evidence that `terminal`, browser, search, or Alpaca changed the main LLM model in the examined market-radar rows.

## 4. Prompt and Tool Stability

Within each 2026-06-25 turn, ClawRoute's cache-relevant fingerprints stayed stable:

| Turn | Calls | Cache key hash | Tool fingerprint | Tool count | Message/tool char growth |
| --- | ---: | --- | --- | ---: | --- |
| `market-radar-discovery` `8187d86b/0d788d` | 19 | `08e39a84...` | `f53ea103...` | 85 | Messages grew from 46,950 added chars on call 1 to 298,369 total message chars near the end; tool schema chars stayed 109,473 |
| `market-radar-source-hardening-review` `d05ab214/4ecda1` | 13 | `df5a72a3...` | `102b35df...` | 85 | Tool schema chars stayed 112,957 |
| `market-radar-watchlist-surveillance` `6de9d175/b6e4e5` | 10 | `72ee6093...` | `b50c7a9c...` | 101 | Tool schema chars stayed high and stable |

Across days, the tool surface changed materially:

| Date/run | Skill | Tool count | Tool fingerprint |
| --- | --- | ---: | --- |
| 2026-06-22 discovery | `market-radar-discovery` | 23 | `714e5818...` |
| 2026-06-25 discovery | `market-radar-discovery` | 85 | `f53ea103...` |
| 2026-06-22 source-hardening | `market-radar-source-hardening-review` | 23 | `7d30a98e...` |
| 2026-06-25 source-hardening | `market-radar-source-hardening-review` | 85 | `102b35df...` |

Daily cross-run cache reuse is therefore not expected even before TTL is considered: the final provider-bound tool array is different. The exact raw final provider-bound request body is not historically stored, but ClawRoute's logged tool count and ordered schema fingerprint are enough to prove the 2026-06-22 and 2026-06-25 first-call prefixes differ.

Hermes' built-in toolset merge sorts merged tool names in `/opt/hermes/toolsets.py:597-602`, so the base Hermes toolset ordering is deterministic there. The fingerprint changes are more likely from a different enabled tool catalog/toolset composition than from random ordering.

## 5. Call-by-Call Timeline

### Sequence A: `market-radar-discovery`, 2026-06-25

| Call | Associated tool/result | Total input | New suffix | Cached input | Cache rate | Reusable-prefix estimate | Effective model/route | First differing field |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1 | user input | 36,411 | cold | 0 | 0.0% | 0 | `codex/gpt-5.5`, slot 3 | first request |
| 2 | `read_file` x4 | 47,218 | +10,807 | 0 | 0.0% | 36,411 | same model, **slot 4** | slot changed |
| 3 | `search_files` x4 | 51,543 | +4,325 | 0 | 0.0% | 47,218 | same model, slot 3 | slot changed back |
| 4 | `read_file`, Alpaca | 63,060 | +11,517 | 47,104 | 74.7% | 51,543 | same model, slot 3 | cache starts on slot 3 |
| 5 | `web_search`, `terminal` | 66,771 | +3,711 | 0 | 0.0% | 63,060 | same model, slot 3 | provider cache miss despite stable local fields |
| 6 | `terminal`, web extract, Alpaca | 69,408 | +2,637 | 62,976 | 90.7% | 66,771 | same model, slot 3 | none local |
| 7 | `terminal`, read/search | 73,660 | +4,252 | 0 | 0.0% | 69,408 | same model, slot 3 | provider cache miss despite stable local fields |
| 8 | read/search | 78,120 | +4,460 | 73,216 | 93.7% | 73,660 | same model, slot 3 | none local |
| 9 | browser navigate/search | 82,632 | +4,512 | 0 | 0.0% | 78,120 | same model, slot 3 | provider cache miss despite stable local fields |
| 10 | browser click | 82,764 | +132 | 77,824 | 94.0% | 82,632 | same model, slot 3 | none local |
| 11 | browser snapshot | 85,679 | +2,915 | 82,432 | 96.2% | 82,764 | same model, slot 3 | none local |
| 12 | browser navigate | 88,155 | +2,476 | 0 | 0.0% | 85,679 | same model, slot 3 | provider cache miss despite stable local fields |
| 13 | browser snapshot | 90,580 | +2,425 | 0 | 0.0% | 88,155 | same model, slot 3 | provider cache miss despite stable local fields |
| 14 | browser console | 92,668 | +2,088 | 0 | 0.0% | 90,580 | same model, slot 3 | provider cache miss despite stable local fields |
| 15 | terminal | 97,113 | +4,445 | 0 | 0.0% | 92,668 | same model, slot 3 | provider cache miss despite stable local fields |
| 16 | terminal | 98,319 | +1,206 | 0 | 0.0% | 97,113 | same model, slot 3 | provider cache miss despite stable local fields |
| 17 | terminal | 99,009 | +690 | 0 | 0.0% | 98,319 | same model, slot 3 | provider cache miss despite stable local fields |
| 18 | terminal | 99,745 | +736 | 96,768 | 97.0% | 99,009 | same model, slot 3 | none local |
| 19 | read/terminal/search | 101,190 | +1,445 | 90,112 | 89.1% | 99,745 | same model, slot 3 | none local |

### Sequence B: `market-radar-source-hardening-review`, 2026-06-25

| Call | Associated tool/result | Total input | New suffix | Cached input | Cache rate | Reusable-prefix estimate | Effective model/route | First differing field |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 1 | user input | 43,301 | cold | 0 | 0.0% | 0 | `codex/gpt-5.5`, slot 4 | first request |
| 2 | reads/search | 61,136 | +17,835 | 0 | 0.0% | 43,301 | same model/slot | provider cold/miss |
| 3 | searches | 66,493 | +5,357 | 0 | 0.0% | 61,136 | same model/slot | provider cold/miss |
| 4 | reads | 82,601 | +16,108 | 0 | 0.0% | 66,493 | same model/slot | provider cold/miss |
| 5 | searches | 85,064 | +2,463 | 66,048 | 77.6% | 82,601 | same model/slot | cache starts |
| 6 | read | 88,060 | +2,996 | 0 | 0.0% | 85,064 | same model/slot | provider miss |
| 7 | terminal x2 | 88,776 | +716 | 0 | 0.0% | 88,060 | same model/slot | provider miss |
| 8 | terminal | 89,754 | +978 | 0 | 0.0% | 88,776 | same model/slot | provider miss |
| 9 | read | 92,862 | +3,108 | 0 | 0.0% | 89,754 | same model/slot | provider miss |
| 10 | read | 95,387 | +2,525 | 82,432 | 86.4% | 92,862 | same model/slot | cache resumes |
| 11 | terminal | 95,575 | +188 | 88,576 | 92.7% | 95,387 | same model/slot | none local |
| 12 | terminal | 100,148 | +4,573 | 95,232 | 95.1% | 95,575 | same model/slot | none local |
| 13 | read/terminal/read | 104,411 | +4,263 | 89,600 | 85.8% | 100,148 | same model/slot | none local |

The theoretical attainment is the important metric. For source-hardening call 5, request-level cache rate is 77.6%, but cached tokens are 66,048 against an 82,601 prior request, so the provider recovered most of the reusable prefix after the first four cold calls.

## 6. Terminal Sequence Analysis

The dashboard association is off by one unless it is labeled carefully:

1. LLM request N receives conversation history and asks for a tool call in its response.
2. Hermes executes the tool.
3. LLM request N+1 contains both the previous assistant tool call and the appended tool result.
4. ClawRoute logs request N+1 after the upstream response returns. Its `request_trace.delta.items` therefore includes an assistant item with `toolCalls` and one or more `tool` result messages already present in the input.

Representative terminal rows:

| Row | Skill | Tool association in request input | Cached | Local cache-relevant fields |
| ---: | --- | --- | ---: | --- |
| 50002 | discovery | `terminal`, web extract, Alpaca results | 62,976 / 69,408 = 90.7% | same model, slot, cache key, tool fingerprint |
| 50003 | discovery | `terminal`, read/search results | 0 / 73,660 = 0.0% | same model, slot, cache key, tool fingerprint |
| 50011 | discovery | `terminal` result | 0 / 97,113 = 0.0% | same model, slot, cache key, tool fingerprint |
| 50014 | discovery | `terminal` result | 96,768 / 99,745 = 97.0% | same model, slot, cache key, tool fingerprint |
| 50022 | source-hardening | `terminal`, `terminal` results | 0 / 88,776 = 0.0% | same model, slot, cache key, tool fingerprint |
| 50026 | source-hardening | `terminal` result | 88,576 / 95,575 = 92.7% | same model, slot, cache key, tool fingerprint |

Conclusion: terminal output may create volatile suffixes, but it is not rewriting the stable prefix in a way that always kills caching. The same final request shape can produce both zero and high cache hits. The root investigation should focus on cache-domain stickiness and provider availability before terminal normalization.

## 7. Large File-Read Analysis

`market-radar-source-hardening-review` 2026-06-25 added large suffixes early:

| Call | Tool/result group | Input delta | Added chars logged | Cached input | Interpretation |
| ---: | --- | ---: | ---: | ---: | --- |
| 2 | reads/search including watchlist/candidate/reference material | +17,835 tokens | 65,127 chars | 0 | cold/miss, not just dilution |
| 3 | search results | +5,357 tokens | 19,979 chars | 0 | cold/miss |
| 4 | reads | +16,108 tokens | 55,319 chars | 0 | cold/miss |
| 5 | searches | +2,463 tokens | 8,965 chars | 66,048 | cache starts, proving earlier prefix was cacheable |

In the 2026-06-22 source-hardening run, call 2 already cached 27,648 tokens and call 3 cached 63,488 tokens. That older run used one slot and a smaller 23-tool schema. The contrast supports this interpretation:

- Large reads lower request-level cache percentage because the denominator grows.
- A large new suffix should not erase an earlier stable prefix.
- The observed zeroes are genuine provider-reported misses, not denominator dilution.

## 8. Daily Cold-Start Analysis

Daily first-call cold starts have three independent explanations:

1. For `gpt-5.5`, OpenAI documents extended prompt-cache retention up to 24 hours and says `24h` is the supported policy. ClawRoute's current upstream request builder does not send `prompt_cache_retention`, so the effective retention policy for the Codex-compatible path is not proven from local telemetry alone.
2. The final tool/schema prefix is not stable across days. Discovery changed from 23 tools on 2026-06-22 to 85 tools on 2026-06-25. Source-hardening changed from 23 to 85 tools across the same dates.
3. Several schedules are outside, near, or at the 24-hour maximum even under extended retention: discovery runs Monday/Thursday, source-hardening runs Monday/Wednesday/Friday, and watchlist runs on weekdays.

The current evidence is enough to classify daily first calls as expected or ambiguous misses unless the selected endpoint uses extended retention for this Codex-compatible path, the request is inside the retention window, and the final provider-bound prefix remains byte-stable. No `prompt_cache_retention` field is visible in the current ClawRoute upstream body builder.

## 9. ClawRoute Transformation Analysis

Current ClawRoute transformation path:

- Hermes sends Chat Completions requests.
- ClawRoute resolves `prompt_cache_key` from the incoming request/execution context.
- ClawRoute builds a Responses API body in [codex-transport.ts](packages/clawroute/src/codex-transport.ts:1469):
  - `messages` are converted in order to `input`.
  - Chat `tool` messages become Responses `function_call_output` items.
  - Tool definitions are mapped in array order from Chat function format to Responses function format.
  - `prompt_cache_key` is forwarded when present.
  - Upstream streaming is always enabled and then normalized back to the client shape.
- Usage from Responses is normalized by reading `usage.input_tokens_details.cached_tokens` and writing Chat-style `prompt_tokens_details.cached_tokens` in [codex-transport.ts](packages/clawroute/src/codex-transport.ts:2614).

No code evidence shows ClawRoute reordering tools, changing message order, or dropping the cache key in the Codex path. The observed per-turn fingerprints also stay stable.

The important routing issue is slot/account selection:

- The private env has `CODEX_BALANCE_LOADER_MODE=on`.
- The current live container also has `CODEX_BALANCE_LOADER_MODE=on`.
- The examined rows show slot changes inside same prompt-cache-key turns.
- Current code keeps only one `activeCacheLease` globally, so unrelated sessions can clear each other's lease.
- The cache lease starts on selection, but it is not persisted in the database and cannot survive container restart.

## 10. Usage-Accounting Verification

ClawRoute stores:

```text
request cache rate = cached_input_tokens / input_tokens
uncached input = input_tokens - cached_input_tokens
```

The database rows have absolute cached token values. Zero-cache rows are stored as `cached_input_tokens = 0`. This is not caused by a dashboard percentage-only bug.

Code verification:

- Responses usage parser reads `usage.input_tokens_details.cached_tokens`.
- Logger writes `cached_input_tokens` directly to `routing_log`.
- Cost formulas use `(input_tokens - cached_input_tokens)` for uncached pricing and `cached_input_tokens` for cached pricing.

The separate dashboard unit bug reported earlier (`154k` rendered as `154M`) is a display issue and should not be confused with prompt cache accounting.

## 11. Canary Results

No live canaries were run during this investigation because they would spend production Codex tokens and the historical telemetry already provides strong evidence.

Historical canary-equivalent observations:

| Canary | Historical evidence | Result |
| --- | --- | --- |
| Exact append-only continuation on same slot | 2026-06-22 discovery rows 49623-49625 terminal continuations | 93.6-99.2% cached |
| Same turn with slot change | 2026-06-25 discovery rows 49997-49999 | Slot change produced cold calls |
| Appended terminal result | Rows 50002, 50014, 50026 | Terminal can cache 90%+ |
| Volatile terminal/browser result | Rows 50003, 50008-50013 | Some volatile-tool continuations miss despite stable local fields |
| Tool ordering/schema stability within run | 2026-06-25 rows | Tool fingerprint stable within turn |
| Tool schema drift across days | 2026-06-22 vs 2026-06-25 | Tool count/fingerprint changed substantially |

Recommended opt-in canaries before production fixes:

1. ClawRoute-only, fixed slot/account, identical stable 50K+ prompt twice.
2. ClawRoute-only, fixed slot/account, append deterministic terminal result.
3. Full Hermes path with a forced single slot for one short run.
4. Full Hermes path with current balancer and per-session lease diagnostics enabled.
5. Compare `request cache rate` and `cache attainment` for all runs.

Expected decisive outcomes:

- If fixed-slot canaries cache but balancer canaries miss, fix slot affinity.
- If fixed-slot terminal canary misses from zero, inspect Hermes/ClawRoute request bytes for terminal.
- If raw upstream usage is nonzero while DB says zero, fix usage accounting. Current code evidence makes this unlikely.

## 12. Minimal Patch

Do not normalize terminal output or summarize external tools as the first production fix. The evidence does not show terminal output as the confirmed root cause.

Smallest production patch proposal:

1. Replace the single global `activeCacheLease` in [codex-transport.ts](packages/clawroute/src/codex-transport.ts:175) with a bounded map keyed by `affinityKey` / prompt-cache-key hash.
2. Keep lease value as `{accountKey, slotIndex, startedAt, lastUsedAt, nominalExpiresAt, maxExpiresAt}`.
3. Apply the lease only for the matching affinity key.
4. Do not clear other affinity leases when a new session starts.
5. Add database-backed or log-backed lease diagnostics so routing rows can explain `cache_lease_reuse`, `cache_lease_started`, `cache_lease_miss_ineligible`, or `cache_lease_evicted`.
6. Add a regression where two interleaved sessions keep separate leases:
   - A call 1 selects slot 3.
   - B call 1 selects slot 4.
   - A call 2 must still prefer slot 3.
   - B call 2 must still prefer slot 4.
7. Add a regression for the 2026-06-25 pattern:
   - Same prompt cache key, same tool fingerprint, no retry error, second call must not migrate slots.
8. Add a dashboard field showing `cache attainment = cached_input_tokens / previous_request_input_tokens` for continuations.

Rollback:

- Keep the current balancer selection as fallback.
- Gate the keyed lease map behind an env var such as `CODEX_CACHE_LEASE_MODE=keyed`, defaulting to current behavior until validated.

Before/after measurement target:

- `market-radar-discovery` should improve from 35.3% aggregate cached on 2026-06-25 toward the 67.2% observed on 2026-06-22 with a stable slot.
- `market-radar-source-hardening-review` should improve from 38.6% toward at least the 52.7% observed on 2026-06-22, with later rows preserving 85-95% hits.
- Watchlist should improve from 16.4% on the slot-switching 2026-06-25 run toward 70%+ observed in same-skill stable-slot historical runs.

## Ranked Hypotheses

| Hypothesis | Status | Evidence |
| --- | --- | --- |
| Provider TTL too short for daily cross-session reuse | Partly confirmed / partly ambiguous | `gpt-5.5` supports up to 24h extended retention, but ClawRoute does not set `prompt_cache_retention`; some schedules exceed 24h and cross-day prefixes changed anyway |
| ClawRoute load-balances identical prefixes across incompatible cache domains | Confirmed for recent discovery/watchlist early calls | Same cache key/tool fingerprint but slot changes |
| Tool definitions or schemas change across days | Confirmed | Tool count 23 -> 85/101 and fingerprint changes |
| Terminal selects a different model | Eliminated for examined rows | Terminal rows remain `codex/gpt-5.5` |
| Terminal rewrites old prefix | Not supported by current evidence | Terminal can hit 90-99% cached |
| Browser/search/MCP dynamically changes the tool registry inside a turn | Eliminated for examined rows | Tool fingerprint stable inside turn |
| Hermes config defines separate main tool models | Eliminated for main LLM loop | Auxiliary models exist, but market rows use `codex/gpt-5.5` |
| Subagents use another model | Not observed in target rows | Config inherits parent unless overridden |
| Retry/fallback changes upstream model | Not observed | Actual model stable in examined rows |
| ClawRoute usage parser reads the wrong field | Eliminated by code path | Reads Responses `input_tokens_details.cached_tokens` |
| Expected 70-90% target incompatible with suffix size | Partly false | Late rows reach 85-97%; early large suffixes can dilute but not explain zero |
