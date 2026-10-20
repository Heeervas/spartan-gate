# Pi session cache key

`extensions/spartan-route-cache.ts` supplies an explicit prompt-cache key for
ordinary Pi requests whose selected model uses provider `spartan-route` and API
`openai-completions`. It targets Pi's `before_provider_request` extension hook;
the hook contract was inspected in `@earendil-works/pi-coding-agent` 1.3.0.

For an object payload with a missing or blank `prompt_cache_key`, the extension
returns a shallow copy containing `prompt_cache_key: "pi:<session-id>"`, using
the current Pi session ID. It preserves an existing nonblank key, leaves the
original payload untouched, and skips invalid payloads or unavailable session
IDs. New sessions and forks receive their own keys; resuming the same session
keeps its ID. A first request after switching from a different key may be cold.

## Install

With Pi installed, run from the repository root:

```sh
mkdir -p ~/.pi/agent/extensions
cp infra/pi/extensions/spartan-route-cache.ts ~/.pi/agent/extensions/
```

Start Pi, or run `/reload` in an existing Pi session. Select the `spartan-route`
provider with an `openai-completions` model. Copy the source again and reload
after future extension updates. To remove it, remove the copied file and reload.

## Check

The focused regression suite requires an installed Pi package and its bundled
dependencies. For a global npm installation:

```sh
PI_CODING_AGENT_DIR="$(npm root -g)/@earendil-works/pi-coding-agent" node --test infra/pi/test_spartan_route_cache.mjs
```

For another installation layout, set `PI_CODING_AGENT_DIR` to the installed
`@earendil-works/pi-coding-agent` package directory. Local checks establish the
extension and payload contract; they do not establish live provider cache hits.

## Limits

An explicit key helps identify the session to Route and the provider. Actual
provider prompt caching still depends on compatible prompt prefixes, account
routing and provider cache availability. A stable key does not guarantee a hit,
preserve cache residency or establish a fixed saving. Costs remain unknown
until actual requests and their usage are measured.

The extension adds no warming requests, timers or TTL settings. Pi's separate
cache-warming feature may make extra requests if enabled; those requests can
have a cost. The extension supplies the key independently of Pi's cache-retention
setting, including `none`; it does not change the retention setting itself.

Pi 1.3.0 exposes the selected model through the hook context, without the actual
request model or its request options. This extension therefore covers ordinary
requests for the selected provider/API. It cannot reliably distinguish calls
redirected by other extensions or virtual models. Nested calls must pass through
Pi's hook to receive this behavior; direct provider calls outside that hook are
not covered. Another payload handler can also change the final result.
