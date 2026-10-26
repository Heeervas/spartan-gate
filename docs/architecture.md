# Architecture

Spartan Gate separates agent execution from network reachability.

## Core Services

- **Hermes** runs the agent gateway on the internal network only.
- **Spartan Route** exposes an OpenAI-compatible endpoint to Hermes and selects upstream models/providers in the full stack.
- **Tinyproxy** is the only general outbound HTTP proxy path for controlled services.
- **DNS relay** resolves only whitelisted domains for internal workloads.
- **Reader** fetches arbitrary public pages through a GET-only API, blocks every
  non-global target, and returns bounded text content.
- **Camofox** provides the supported Camoufox/Firefox-based persistent browser.
- **SearXNG** provides internal search.
- **Caddy** exposes authenticated local edge ports.
- **Shared tool access** is an opt-in source integration for host Pi and scoped
  HTTP clients. A dedicated tool-only Caddy edge and small fixed-target adapter
  preserve the existing services. See [shared-tool-access.md](shared-tool-access.md);
  source availability does not mean the selected installation is activated.

## Network Model

`spartan_internal` is an internal Docker network. Hermes, Spartan Route,
Camofox, DNS, Reader, proxy, SearXNG and Caddy can communicate
there.

`spartan_external` is attached only to services that need egress or edge exposure:
proxy, DNS, SearXNG, reader, and Caddy. Reader is an intentional direct-GET
exception to the Tinyproxy/DNS whitelist for arbitrary public page text; it is
not published to the host.

Hermes does not join the external network. It calls Spartan Route at
`http://spartan-route:18790/v1`, uses Camofox for browser tools, and relies on the Reader/search services
for controlled retrieval.

The optional `tool-access` profile adds internal client/adapter ingress networks
and a dedicated normal publishing bridge joined only by tool-edge. The restricted
client never joins backend or publishing networks. Docker suppresses effective
host publication on an all-internal topology; the normal bridge is intentional.
Only the fixed-target edge gains ordinary bridge reachability, not the client.
The legacy multi-listener Caddy service never joins tool-client ingress.

## Whitelist Model

The outbound policy is split into two fragments:

- `infra/outbound-proxy/whitelist.txt`: public, generic, safe to publish.
- `private/outbound-proxy/whitelist.private.txt`: local/project/personal entries, ignored by Git.

Tinyproxy and the DNS relay merge both fragments at container startup. The helper
`scripts/whitelist-domain.sh` writes to the private fragment by default.

## Router Layout

`infra/spartan-route/` is the current full-stack router source code and tests.
The legacy ClawRoute application is retired. `config/clawroute/` remains
runtime routing config mounted into Spartan Route. See
[spartan-route.md](spartan-route.md) and [clawroute.md](clawroute.md).

## Reader

Reader is for static page text. It does not execute JavaScript, labels fetched
text as untrusted content, and does not replace Camofox. See
[reader.md](reader.md) for tests and agent prompts.
