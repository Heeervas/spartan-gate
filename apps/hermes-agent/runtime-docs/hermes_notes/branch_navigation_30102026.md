# Branch navigation — activated, live journey verification pending

Status: source/native checks and independent integrated review passed. A bounded
derivative of the existing deployed image was built and Hermes alone recreated
on 30 October 2026. Installed helpers and command registration match the reviewed
candidate. Container health is healthy, API health is HTTP 200, and restart count
is zero. This archive note is not published into runtime guides.

The source adds terminal/Discord `/start-branch` (native `/branch` alias),
`/start-fresh`, `/return` and `/cancel`. Terminal return restores the native parent
and acknowledges a branch-only summary with tools disabled for that turn. Discord
creates a child thread while preserving the parent; a return queues behind a busy
parent and posts its own acknowledgment outcome back to the child. Nested branches
return to their immediate parent. Cancellation imports nothing and is not a tool
or filesystem rollback. Histories/threads are retained. Native Discord in-place
`/branch`/`/fork`/`/start-branch` (`--here`, DMs or failed thread creation) remain
ordinary forks without navigation metadata; use native `/resume` to switch back.

Native fixtures cover branch-only boundaries across compression, parent advancement
and replacement rejection, native queued recursion, duplicate-return refusal,
summary failure, tool pin/refresh/dispatch denial and ordinary tool restoration.
They use synthetic providers and adapters, not live model or Discord traffic.
Pending/restarted or interrupted return states require history inspection rather
than blind retries; no crash-safe exactly-once delivery is promised.

Full-image canaries imported the assembled CLI and initialized a real AIAgent
without network access; temporary acknowledgment tool restriction and restoration
passed. No real model request or Discord test message was sent. Discord logged
initial connection, but command synchronization hit rate limiting; registration
success remains unconfirmed. Do not force synchronization or reconnect loops.
Astra high advised retaining the healthy deployment and reporting these limits,
not adding speculative code or weakening acceptance.

Operator next step: one ordinary terminal return and one return from a newly
created Discord child thread in an explicitly selected test conversation. Observe
parent summary/acknowledgment and child completion notice. Operational acceptance
and task closeout remain pending until these live journeys are checked. Existing
threads created before this patch lack navigation metadata; use a new branch.
The previous image and exact private Compose beforeimage are retained for scoped
rollback; restore only the Hermes image selection, preserving later foreign edits.
Public usage and recovery contract: `docs/hermes-branch-navigation.md`.
