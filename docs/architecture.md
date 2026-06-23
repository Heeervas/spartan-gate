# Architecture

Spartan Gate separates agent execution from network reachability.

## Core Services

- **Hermes** runs the agent gateway on the internal network only.
- **ClawRoute** exposes an OpenAI-compatible endpoint to Hermes and selects upstream models/providers.
- **Tinyproxy** is the only general outbound HTTP proxy path for controlled services.
- **DNS relay** resolves only whitelisted domains for internal workloads.
- **Reader** fetches public pages through a GET-only API, blocks internal/private targets, and returns bounded text content.
- **Browserless** provides Chromium/CDP sessions for Hermes without direct container egress.
- **Camofox** provides an optional Camoufox/Firefox-based browser backend for Hermes browser tools.
- **SearXNG** provides internal search.
- **Caddy** exposes authenticated local edge ports.

## Network Model

`spartan_internal` is an internal Docker network. Hermes, ClawRoute,
Browserless, Camofox, DNS, reader, proxy, SearXNG, and Caddy can communicate
there.

`spartan_external` is attached only to services that need egress or edge exposure:
proxy, DNS, SearXNG, reader, and Caddy. Reader is a narrow direct-GET egress
exception for public page text; it is not published to the host.

Hermes does not join the external network. It calls ClawRoute at
`http://clawroute:18790/v1`, uses Browserless for CDP-backed browsing, can opt
into Camofox for Hermes browser tools, and relies on the reader/search services
for controlled retrieval.

## Whitelist Model

The outbound policy is split into two fragments:

- `infra/outbound-proxy/whitelist.txt`: public, generic, safe to publish.
- `private/outbound-proxy/whitelist.private.txt`: local/project/personal entries, ignored by Git.

Tinyproxy and the DNS relay merge both fragments at container startup. The helper
`scripts/whitelist-domain.sh` writes to the private fragment by default.

## ClawRoute Layout

`packages/clawroute/` is source code and tests. `config/clawroute/` is runtime
routing config mounted into the container. See [clawroute.md](clawroute.md).

## Reader

Reader is for static page text. It does not execute JavaScript, labels fetched
text as untrusted content, and does not replace Browserless or Camofox. See
[reader.md](reader.md) for tests and agent prompts.
