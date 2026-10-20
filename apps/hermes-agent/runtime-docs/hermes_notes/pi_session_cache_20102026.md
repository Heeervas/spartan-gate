# Pi session cache key

Date: 2026-10-20 (Europe/Madrid).
Status: source and host extension applied; local validation passed. Activation
in the existing interactive Pi session is pending the operator's `/reload`.

The Pi extension is tracked under
`infra/pi/extensions/spartan-route-cache.ts`, with installation and focused
check instructions in `infra/pi/README.md`. Once loaded, it supplies
`prompt_cache_key: "pi:<session-id>"` for ordinary requests
whose selected model uses provider `spartan-route` and API
`openai-completions`, when the payload has no nonblank key. It preserves existing
explicit keys and does not mutate the original payload. New sessions and forks
use distinct Pi session IDs.

This is an explicit session-key change, not evidence of a provider cache hit.
The first request after changing keys can be cold. Provider cache reuse still
depends on the prompt, routing and upstream availability; savings and costs
remain unknown without actual request usage. No live provider cache validation
is recorded here.

The extension adds no warming requests, timers or TTL changes. Pi's independently
enabled cache warming may send billable requests. Key injection is independent
of Pi's cache-retention setting, including `none`. In Pi 1.3.0 the hook exposes
the selected model rather than the actual request model; redirected, virtual or
nested provider calls are not a guaranteed coverage boundary.

Local acceptance covers native extension loading, four focused regression
tests, a synthetic transport check and an offline CLI smoke check. The synthetic
check verified the serialized session key and normalized cache usage using a
fake response; it made no external provider request. These checks do not prove
activation in an already running interactive session.

This Pi change does not require a Hermes image build or a Compose deployment.
