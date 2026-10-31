# Native Codebase Memory and shared MCPHub — applied

Applied: 31 October 2026. This is an operator archive note, not a published runtime
guide and not permission to change other profiles or mount additional source.

## Applied outcome

- Shared account/API connectors remain in MCPHub: seven connected backends and
  101 exact tools. The former shared Codebase backend and its eleven grants were
  removed only after native client acceptance. Closed sentinel and distinct
  group-scoped client keys remain; no wildcard or network change.
- Codebase Memory0.12.1 uses native local stdio in selected Hermes and host/container
  Pi clients. Each connection indexes its selected canonical Git root into a fresh
  protected cache and exposes thirteen analysis tools, not arbitrary indexing.
- Full indexing includes operator source such as `scripts/`; native fast mode
  filters it and is not used. Graph persistence into the source tree and
  doc-candidate generation are explicitly off, as are UI, watchers and auto-index.
- Actual native index/search/snippet passed: host Pi on the real project checkout;
  container Pi and Hermes on an owned Git fixture in the existing container
  workspace. This does not make unmounted host repositories visible. Ordinary Pi
  sessions and Hermes hub CLI rediscovered the final shared catalog without model
  inference. No Hermes rebuild or service restart was required.

## Operating boundaries

Select the repository before connection: launch Pi there, or select Hermes'
native coding directory with `hermes chat --in DIR`. The generic workspace/home
is not automatically an authorized repository. Reconnect after ordinary edits,
ignore/public-template changes or selecting another repository. Existing sessions
retain their indexed snapshot; terminal `cd` does not re-anchor their MCP.

Visible sensitive paths and opaque nested repositories/submodules fail closed.
An exact operator-audited public template may be approved by relative filename
plus content hash; absent pins grant nothing, changed or unknown bytes refuse.
Never approve credentials this way. These are conservative filename/workflow
checks, not a universal secret detector or hostile-change filesystem sandbox.

Use short protected cache prefixes for native Unix-socket coordination. Fresh
session caches contain source-derived data; they are not shared across roots.
The launcher deliberately does not delete possibly-live caches or signal unknown
PIDs. Exact cache cleanup requires verified consumer cessation, never an age rule.

## Google authorization remains operator-owned

Agents use only their MCPHub bearer keys. GTM/GA4 separately use the operator's
Google OAuth and account permissions. Existing expired grants remain pending;
no consent or Workspace-token transfer occurred. The Workspace login helper does
not populate these MCPHub bindings. A unified read-only operator login is a
separate, unimplemented capability, not an instruction to reuse its broad grant.

Source checks, reviewed candidate identity, private installation backups and
native acceptance receipts belong to the existing MCPHub task. The independent
Pi foundation ownership/commit dependency remains separate from runtime usability.
