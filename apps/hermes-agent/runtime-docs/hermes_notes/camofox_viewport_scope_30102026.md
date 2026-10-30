# Camofox viewport: working service, unavailable terminal client

## Status

Investigation verified on 30 October 2026. A small native-tool source repair is
implemented and under validation; **the running gateway has not adopted it**.
This operator archive note is not a published runtime guide or deployment authority.

## Finding

The deployed Camofox service retains the authenticated Spartan viewport patch.
A scratch-tab check against `https://example.com/`, with `relaunch: false`, proved:

| Request | Effective inner viewport | PNG dimensions | Strategy |
| --- | --- | --- | --- |
| 1440 × 900 | 1440 × 900 | 1440 × 900 | `pageViewport` |
| 390 × 844 | 390 × 844 | 390 × 844 | `pageViewport` |

At 390 px, the evaluated `max-width: 430px` media query was true. The browser
generation remained unchanged. No shared-browser relaunch, service restart or
configuration change was performed. Scratch tabs were removed; the empty scratch
context remains subject to the service's normal idle cleanup. No production page
or browser credential store was inspected.

The endpoint/authentication gap was independently reproduced for both the root
and Work profile under the actual runtime UID/GID:

- Native profile secret scopes supply the Camofox endpoint and bearer credential.
- The terminal child environment produced by the native environment builder has
  neither `CAMOFOX_URL` nor `CAMOFOX_API_KEY`.
- A Python child importing `tools.browser_camofox` consequently resolves no
  Camofox endpoint. Importing `_get` / `_post` alone cannot restore the parent's
  context-local secret scope.

Relevant ownership:

- `tools/browser_camofox.py`: `get_camofox_url`, `_auth_headers`, `_get`, `_post`.
- Upstream `tools/environments/local.py` and `local_env_policy.py`: child-process
  filtering; these are runtime application sources, not this repository's files.
- `apps/hermes-agent/runtime-guides/local/camofox.md`: documents these helpers for
  low-level calls but does not supply a working terminal scope bridge.
- `infra/camofox/patch-server.js`: verified service-side viewport route.

A targeted old/new root and Work configuration comparison found no Camofox
terminal passthrough entries in either generation. This does not establish the
historical mechanism that made the previous workflow succeed, nor identify an
introducing migration commit. It does rule out a missing Camofox passthrough
entry in those four compared configurations. The service patch is not missing.

## Native repair — source implemented, activation pending

`patch_camofox_viewport.py` adds `browser_viewport(width, height)` to the existing
browser toolset in Camofox mode. It reuses the current durable session, native
managed secret scope, private-page guard and Spartan route. It never creates a
tab or relaunches the browser; it returns numeric viewport evidence rather than
page URLs or raw transport errors. Browser credentials and terminal filtering
remain unchanged. Dockerfile wiring and the native test gate include the patch.

Five focused tests cover successful sizes/session routing, invalid size/missing
tab, stale/fixed transport errors, patch idempotence/input rejection and pinned
native-source compatibility. A disposable process in the installed runtime
registered the exact candidate and exercised real `browser_navigate` followed by
`browser_viewport`: both target sizes matched runtime and PNG dimensions, with
mobile media queries active and the browser generation unchanged. This did not
modify gateway modules on disk or load the tool into the live gateway process.

Independent candidate review and final source acceptance are tracked by the
owning task. Deployment remains separate: a controlled Hermes adoption/restart
or future image build needs operational authorization. The original experiment's
page and a model-driven conversation using the new tool remain unvalidated.

## Diagnostic limit

An initial scratch creation using `about:blank` was rejected with HTTP 400. A
follow-up query for its exact generated scratch owner found zero tabs, with the
browser generation unchanged. The successful checks above used the documented
allowlisted HTTPS target instead; no destructive recovery was used.
