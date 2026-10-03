# Reimagining Spartan Gate: a trusted workbench with a small safety boundary

Date: 2026-10-02. Status: **proposal and reset handoff; no runtime changes authorized or performed by this document**.

For the shorter explanation, read the [version 2 plan](spartan-gate-plan-v2-2026-10-02.md) or open the [visual HTML guide](spartan-gate-plan-v2-2026-10-02.html).

**Start with the [implementation blueprint](spartan-gate-implementation-blueprint-2026-10-02.md) for the deeper follow-up.** It adds installed-source findings, actual resource settings, concrete patch designs, current ecosystem comparisons, and replacement experiments. Its implementation sequence supersedes the broad sequence below; this document retains the original baseline and strategic tradeoffs.

## The recommendation

Keep Spartan Gate, but narrow its job. Make it the place where a trusted Hermes can work autonomously without taking down the laptop, accidentally inheriting every credential, or turning a bad dependency into host access. Stop making it simultaneously maintain a security distribution, multiple agent workflows, two browser integrations, two routers, and a growing observability product.

The best direction is **one everyday Hermes, specialists on demand, bounded execution, one supported persistent browser, one credential-routing authority, and optional observability outside the critical path**. A specialist does not need a permanent gateway process. A new model does not need another orchestration layer. A useful safety control should protect an identifiable asset, have a tested enforcement point, and justify its maintenance and workflow cost.

Start with Path A below, make Path B the intended personal-workbench direction, and adopt the resource separation from Path C only where measurements justify it. Do not begin with a rewrite or a local Langfuse deployment.

This is a strategic assessment, not a completed security audit or an OOM root-cause diagnosis. Findings combine current repository evidence, safe runtime metadata, and official upstream documentation. Three subagents independently investigated architecture, resources, and OSS alternatives; their evidence is consolidated here.

## 1. What problem are we actually solving?

The original idea—separate agent execution from network reachability—is sound. The current implementation also carries personal integrations, profile hosting, browser persistence, subscription routing, voice, compatibility layers, and developer-agent workflows. Each feature is reasonable individually; their interactions have become the product.

A better mission statement:

> Give my trusted assistant broad autonomy over reversible work, a bounded amount of compute, and only the authority needed for the current task. Make expensive failures contained and recovery routine.

Trust in Hermes is relevant. It supports fewer repetitive approvals, fewer permanent specialist personas, and simpler default workflows. It does not remove compromised packages, hostile web content, account-session misuse, implementation bugs, or accidental resource exhaustion. Better models can make more calls faster; that can increase the need for scheduling even as it reduces the need for scripted reasoning workflows.

Protect, in priority order:

1. Host availability and the ability to stop/recover the system.
2. Provider credentials, browser sessions, private data, and account authority.
3. Irreversible or externally visible actions: publication, account changes, deployments, payments, destructive persistence.
4. Integrity of the boundary and separation of unrelated tasks.
5. Reversible workspace mistakes, primarily through versioning, checkpoints, and restore.

Do not optimize primarily for preventing a trusted assistant from downloading ordinary documentation or editing its assigned workspace.

## 2. Baseline: facts, interpretation, and uncertainty

### Architecture and maintenance

| Evidence | What it means |
| --- | --- |
| [Security contract](security.md), especially Reader's explicit public-exfiltration exception | The existing gate is already **not a general confidentiality boundary**. GET hostnames, paths, and queries can carry data. Whitelisting still restricts unexpected direct connections; it does not prove that accessible information cannot leave. |
| [Layer matrix](layers.md): L0–L4, legacy ClawRoute addon, current Spartan Route | There is a substantial compatibility and validation matrix. Fewer supported shapes can reduce maintenance; they do not automatically save runtime RAM. |
| [Patch taxonomy](patches.md) and `apps/hermes-agent/Dockerfile:175–191` | Current source carries 17 build-time Hermes patches. Earlier activation notes report 16 before the managed-browser addition. Classify their purpose before deleting or converting them. |
| [Profile contract](profiles.md) | Profiles can be manual specialists or resident gateways. Separate profile identities/API keys are not OS isolation when workloads share a UID, container, and accessible data. |
| `infra/compose/compose.yml:502–512,674` | Hermes startup waits on several services; health also probes dependencies. Optional search/retrieval failures should not necessarily prevent model-and-terminal work. |
| [Workstation contract](workstation.md) | The existing broker keeps execution keys outside Hermes and scopes authority. Reuse this idea rather than building a competing host executor. |
| `.github/agents/coordinator.agent.md:13–36,90–125,193–205` | A copied development workflow permits up to 12 parallel subagents and extensive role handoffs. This is a workflow ceiling, not evidence of 12 live processes or the source of OOM. It is nevertheless excessive as the default for ordinary work. |
| [Task list](../todo-list.md) versus [current state](../_project_specs/session/current-state.md) | The task list still calls the Hermes refresh pending while the session state records activation. Reconcile these before doing duplicate work. Browserless retirement is already a planned direction. |

Repository references describe this dirty working tree, not necessarily every deployed artifact. Line numbers are navigation aids and may move.

### Resource snapshot from this investigation

These are aggregate machine/service observations, not a private account or profile inventory. Memory is rounded; Docker's displayed working set excludes inactive file cache and therefore differs from cgroup charges.

| Observation | Interpretation |
| --- | --- |
| Host RAM approximately 7.66 GiB; swap 2 GiB, approximately 1.42 GiB occupied; approximately 3.98 GiB MemAvailable at the sample | A small shared machine with past or current swapping. Low current pressure does not prove peak workloads fit. Swap occupancy alone is not proof of thrashing. |
| Nine running containers, approximately 3.00 GiB total Docker-displayed memory | The service count alone is a poor optimization target. |
| Hermes approximately 2.27 GiB displayed against a live 3 GiB ceiling | Hermes dominates the measured stack. Public Compose's 2 GiB default is not the effective local limit. |
| Hermes cgroup approximately 2.81 GiB charged, lifetime peak 3.00 GiB, `memory.events max=11025`, `oom=0`, `oom_kill=0`; approximately 524 MiB swap | The current cgroup has repeatedly encountered its memory ceiling. This is evidence of reclaim/ceiling pressure, **not 11,025 OOMs and not proof of the reported intermittent OOM cause**. |
| Substantial Python and Node child processes alongside Hermes processes | Tool/subprocess memory inside the same container is a strong attribution target. Process RSS can double-count shared pages; command contents and profile data were not needed. |
| Camofox approximately 568 MiB displayed; 2.5 GiB limit; approximately 1.02 GiB lifetime cgroup peak; no recorded OOM kills | Browser peaks need budgeting, but this sample does not establish Camofox as the offender. |
| Spartan Route approximately 60 MiB displayed | Replacing the router is not a compelling immediate RAM optimization. |
| Proxy + DNS + Reader + Caddy roughly 0.1 GiB combined (approximately 90–110 MiB across samples) | Deleting these small services would sacrifice useful boundaries for little measured memory benefit. |
| Caddy reached its approximately 64 MiB cap, with `max` events but no recorded OOM kills | Tiny limits can themselves cause pressure. Measure before tightening every service cap. |
| Browserless, legacy ClawRoute, and Pocket TTS were not running | Retiring those paths can save complexity, but cannot reclaim idle-container RAM that was not being used. |
| Root filesystem approximately 3.7 GiB available | Below the repository's 5 GB floor. Restore disk headroom through a separately scoped cleanup before builds, downloads, or storage-heavy experiments. No cleanup was performed. |

Current-container counters reset when cgroups are recreated and cannot reconstruct historical incidents. The sampled major containers reported no Docker OOM-killed state or restarts. Existing daily monitoring recorded container-pressure/restart and swap-out warnings, but no host OOMs; the readable seven-day kernel journal likewise contained no OOM matches. Neither source proves complete historical coverage. The monitor's `sustained_swap_out` label can be triggered by a historical maximum and is not itself proof of continuous thrashing. Use the existing diagnostics documented in [operations](operations.md), including `scripts/memory-status.py`, before adding another sampler.

The nine active container limits sum to approximately 6.66 GiB against 7.66 GiB physical RAM. They are ceilings rather than reservations, but simultaneous saturation leaves only approximately 1 GiB for the host. Per-task peak attribution and a representative workload baseline remain necessary.

## 3. What is good, what is overkill, and what should change

| Keep | Simplify or retire | Improve |
| --- | --- | --- |
| Non-root workloads, protected installed code, minimal mounts, localhost/private edges | Browserless/CDP support after workflow migration; retain credential data untouched | One documented browser contract, with explicit unsupported workflows |
| Credential custody outside general execution; authenticated Route and host broker | Legacy router product path after proving no required deployment relies on it | One routing owner; compatibility aliases during migration |
| Browser identity separation and checkpoint/restore | Personas implemented as always-on gateways without an actual inbox requirement | One primary interface; manual specialists; measure plugin/MCP residency |
| Host/LAN isolation and fail-closed security initialization | Five layers and cross-product addon choices as the long-term public interface | Small baseline plus explicit capabilities; separate personal preference from supported product matrix |
| Bounded queues, PID/memory ceilings, cancellation | Mandatory multi-agent ceremonies for tiny changes; broad context duplication | One owner, a concrete task budget, independent review where impact warrants it |
| Narrow regressions for boundary behavior | Duplicate adapters already owned by upstream; permanent emergency overlays | Patch owner, reason, upstream issue, parity test, and retirement condition |
| Existing bounded SQLite operational telemetry | Building every trace/evaluation view inside the router | Portable trace export, retention, optional external analysis |
| Durable storage and tested recovery | Startup coupling to optional capabilities | Report core readiness separately from search, browser, speech, and telemetry readiness |

Some complexity is earned. Native Responses replay, stream commit semantics, credential refresh, and quota affinity are not interchangeable with a generic HTTP proxy. A replacement must prove parity or explicitly surrender those features. The [Route design](spartan-route.md) already records why a simpler Python implementation replaced the old policy graph; avoid repeating that rewrite without a new reason.

Do not blindly turn every patch into a plugin. Current upstream middleware can fail open, and some browser selectors lack the necessary extension seam. Optional behavior belongs in plugins; hard authority boundaries need enforcement outside mutable agent behavior.

## 4. The security bargain: spend friction where it buys something

| Choice | Gain | Sacrifice / residual exposure | Enforcement required |
| --- | --- | --- | --- |
| Autonomous reads, tests, and reversible edits in an assigned workspace | Faster everyday work; fewer interrupts | Bad edits and dependency mistakes remain possible | Workspace scope, resource limits, versioning, restore |
| Broader public-web access for a trusted workflow | Less domain-admin work; better research and package usability | Accept public exfiltration and more download/supply-chain exposure | Global-address-only egress, redirect/DNS checks, deny host/LAN/metadata destinations, no direct fallback |
| Task-scoped authority for a repeated workflow | Avoid approving every command | A bad action inside the granted scope remains possible | Real caller/resource/scope/expiry binding, revocation, audit; prompt instructions alone do not enforce a lease |
| Public research without logged-in browser state | Lower credential blast radius | Some sites require a separate account-enabled step | Separate browser identity and explicit credentialed capability |
| Account-enabled browsing when needed | Useful real work on authenticated services | Cookies usually confer broad account authority; domain allowlists do not prevent account misuse | Task authorization records consent; provider-scoped credentials or an action broker are needed for enforceable action restrictions |
| Strict mode for unfamiliar code or sensitive jobs | Stronger containment | More friction, lower compatibility | Separate disposable execution and existing whitelist policy |

The proposed public-web mode is a **future policy change**, not permission to connect Hermes directly to the external network. Existing L0/L1 convenience modes do not automatically provide host/LAN isolation. A public-web gate must validate actual connection destinations, IPv4/IPv6, mixed DNS answers, redirects, rebinding, and CONNECT behavior. Reader is a useful narrow pattern, not a drop-in generic package/terminal proxy.

The “trusted” label should describe allowed authority, not imply that arbitrary tool code is trustworthy. A plugin, package, or subprocess can act with the same authority as Hermes. Profiles in a shared UID/container should be treated as organizational separation, not a strong multi-tenant boundary. A task description or confirmation does not technically narrow an authenticated browser session; unless the provider or a dedicated action broker enforces narrower authority, explicitly accept the whole-session risk.

## 5. Four paths from here

Effort ranges are rough engineering sessions, not delivery promises. Hooks, migrations, and acceptance may expand them.

| Path | Shape | Main benefit | What you give up | Effort / decision trigger |
| --- | --- | --- | --- | --- |
| **A. Lean Gate** | Current security contract; one browser/router; fewer resident gateways; optional capabilities start only when needed | Lowest-risk simplification and immediate resource diagnosis | Domain friction remains; no architectural isolation between tools and gateway yet | Small–medium, roughly 3–6 focused sessions; start here |
| **B. Trusted Workbench** | A plus explicit public-web policy, autonomous reversible workspace work, task-scoped high-impact capabilities | Fits a highly trusted personal Hermes; removes daily friction | Broader accepted public-data egress and software download risk | Medium, roughly 5–10 sessions after A; pilot one low-sensitivity workflow |
| **C. Small gateway with leased execution** | Persistent inbox/controller; bounded disposable tool workers; specialists are task configuration; browser/speech acquired as needed | Protects gateway responsiveness and controls aggregate concurrency | Queueing and cold starts; worker isolation/dispatch requires integration work | Medium–large; prototype only if attribution shows shared tool/gateway contention |
| **D. Move heavy work off the laptop** | Keep private entry/control locally; run selected workers, browser, or observability on a dedicated machine | Larger memory envelope and isolation from desktop activity | Another machine, patching/backups, network latency and outage dependency | Medium plus operating cost; choose if useful workloads cannot fit after A/C |

**Recommended sequence: A → a narrow B pilot → the minimum useful C.** D is preferable to endlessly engineering around an insufficient hardware budget when measurements show a real capacity need. B and C are independent: you can improve resource isolation while retaining strict egress, or relax public-web policy without inventing a scheduler.

A fifth, deliberately radical alternative is near-upstream Hermes with one browser and native provider access. It maximizes simplicity, but sacrifices centralized subscription/account routing, quota behavior, and some credential separation. Consider it only if those features no longer matter. Do not pretend Langfuse supplies their replacement.

## 6. Resource plan: fix admission before buying more services

### First establish the failure class

For 24–48 hours of representative ordinary use, collect bounded metadata: container/cgroup memory and swap, `memory.events` deltas, pressure stall information, disk headroom, restart events, gateway responsiveness, active turns, browser contexts/tabs, subprocess count, and task IDs. Use short intervals around incidents and bounded retention; do not retain full command lines, prompts, or credentials by default.

Distinguish host OOM, container OOM, PID exhaustion, disk-full errors, browser watchdog recycle, upstream model errors, and plain latency from reclaim. Record the incident time and cgroup/process involved. Attribute task peaks with PSS/cgroup accounting where available rather than summing raw RSS. Inspect historical kernel metadata only if accessible; lack of history is an uncertainty, not a clean bill of health.

### A starting experiment, not new production defaults

For a machine with approximately 7.7 GiB usable RAM, tentatively reserve 2.5–3 GiB for the host, desktop, and safety margin. Admit workload combinations only when their measured peak plus baseline fits the remaining approximately 4.5–5 GiB envelope. Derive the actual reserve from desktop peaks; this is a proposed budget, not a validated capacity claim.

Initially allow **one heavy execution task and at most two active agent turns across the stack**, with a small bounded waiting queue (for example four pending jobs), timeout, cancellation, and visible busy responses. These are experiment parameters to tune against latency and completion quality. Rate-limit browser creation separately and count all active profiles. A per-agent limit does not create a global limit.

Model workload memory as:

`host reserve + resident baseline + sum(admitted task peak estimates) + recovery margin <= physical RAM`

Container ceilings are last-resort containment, not reservations and not a scheduler. Increasing Hermes from 3 to 4 GiB may move failure into the host. Lowering every ceiling can cause avoidable reclaim or kills. Swap/zram can absorb some bursts but cannot make permanently excessive concurrency healthy; do not enlarge disk-backed swap with current disk headroom.

If heavy tool children cause interference, prototype execution in a separate cgroup/container while preserving gateway memory. Prefer existing Hermes execution hooks or the existing broker over a new orchestration service. Never give Hermes an unrestricted Docker socket. Any worker launcher must narrowly constrain images, mounts, identity, networking, resource limits, deadlines, and cleanup.

In the long run, reserve capacity for stop/cancel, health, and browser persistence. Optional work should queue or fail busy before core control is starved. Leases must be reaped after crashes and expiry; teardown must not delete persistent identities or user data.

### Acceptance

- No new host/container OOM kills during the representative baseline and controlled replay; record event-counter deltas, not just final health.
- Stable queue size and no unbounded memory/disk growth across repeated tasks and cancellation.
- Interactive gateway latency no worse than an agreed baseline (initial target: p95 under two seconds excluding model/network latency).
- At least the measured host reserve remains during admitted peak combinations; otherwise lower concurrency or move capacity.
- Disk remains above 5 GB; require more than 10 GB before any Hermes rebuild. Build only when the normal repository rules allow it.

## 7. Langfuse and other current tools: buy capabilities, not another platform

Official sources were checked on 2026-10-02. Current upstream docs can be ahead of the pinned runtime; verify features against that exact revision before planning migrations.

| Tool | Appropriate job | Cost / limitation | Recommendation |
| --- | --- | --- | --- |
| Upstream Hermes native features/plugins | Remove duplicate integration code; optional observability | Pinned-version parity required; middleware failure behavior is unsuitable as sole hard enforcement | First place to look for simplification |
| Langfuse | Trace exploration, evaluations, prompt/version experiments | Web + worker + PostgreSQL + ClickHouse + Redis/Valkey + object storage; not a router or execution boundary | Trial remotely, or use a hosted instance if data policy permits |
| OpenTelemetry | Portable spans, filtering, sampling, bounded export | Transport/processing, not a dashboard or trace database | Start with direct export or bounded local records; add Collector only for a demonstrated need |
| Phoenix | Local trace/evaluation UI with a simpler topology | SQLite single-container option; actual memory still needs measurement; Elastic License 2.0 rather than permissive OSS | Optional short-lived analysis tool if it answers a real question |
| gVisor | Additional isolation for disposable code workers | Compatibility and CPU/memory/I/O costs; does not solve scheduling | Optional experiment for unfamiliar code, not a mandatory layer |

Langfuse's official Compose guidance recommends **at least four cores and 16 GiB RAM**. This is a deployment recommendation, not a measured minimum, but it exceeds this host's total RAM. Its stack belongs elsewhere for this experiment. See [architecture](https://langfuse.com/self-hosting) and [Compose deployment](https://langfuse.com/self-hosting/deployment/docker-compose).

Langfuse core is MIT licensed; some governance features, including managed retention and server-side masking, are enterprise features. Server-side masking happens after ingestion into blob storage. Therefore redact **before sending**, even when self-hosting. Verify [licensing](https://langfuse.com/self-hosting/license-key), [masking](https://langfuse.com/self-hosting/security/data-masking), and [installation telemetry](https://langfuse.com/self-hosting/security/telemetry). Do not assume a free deployment includes the desired retention mechanism; implement and test the chosen retention policy.

Current Hermes documentation includes an opt-in Langfuse integration with sampling and truncation. Prefer it over a homegrown adapter if present in the pinned version. Its availability-first behavior is appropriate for telemetry. The documented [built-in integrations](https://hermes-agent.nousresearch.com/docs/user-guide/features/built-in-plugins) and [middleware](https://hermes-agent.nousresearch.com/docs/developer-guide/middleware) do not justify relocating a fail-closed security boundary into a fail-open hook.

Phoenix supports [Docker with SQLite](https://arize.com/docs/phoenix/self-hosting/deployment-options/docker); its [actual license](https://github.com/Arize-ai/phoenix/blob/main/LICENSE) restricts offering substantial functionality as a hosted service. Fewer dependencies are promising, not proof of lower measured peak RAM. OpenTelemetry's [Collector configuration](https://opentelemetry.io/docs/collector/configuration/) describes processing and export; gVisor's [architecture](https://gvisor.dev/docs/architecture_guide/intro/) and [production tradeoffs](https://gvisor.dev/docs/user_guide/production/) explain its different isolation/cost bargain.

### Minimum useful observability

Use one task/parent/session correlation scheme. Record model, duration, token counters where available, retries, tool category, queue wait, error category, cancellation, browser leases, and resource-pressure events. Use opaque profile IDs; exclude prompts, tool results, headers, cookies, URLs with queries, and full command arguments by default. Truncation is not redaction. Test the actual exported payload.

Route remains authoritative for routing/account decisions and quota telemetry. Langfuse can correlate and visualize; it cannot infer exact subscription quota burn from tokens alone. Avoid double-counting router and Hermes observations as separate model generations.

Pilot with one synthetic redacted parent/child task, then a small representative workflow. Bound exporter queues and disk spool; drop optional telemetry with a counter under pressure. Stop the backend and prove tasks still succeed, memory remains bounded, and shutdown is timely. Continue only if it answers concrete questions such as “which tool caused the peak?” or “did this prompt change improve outcomes?” better than current records.

## 8. Fewer agents, better delegation

Separate two issues: runtime Hermes personalities/gateways, and the repository's coding-agent workflow. They are not the same system and should not share an accidental concurrency policy.

For daily Hermes use, prefer one generalist with persistent memory and a small curated toolset. Keep distinct resident gateways only for genuinely separate inboxes, availability requirements, or authority domains. Specialists can be instructions plus task-local context loaded on demand. Preserve independent credentials/session history; consolidation must not silently merge identities or channel ownership. Verify upstream dispatch support before promising transparent persona switching.

For repository development, replace the default five-role ceremony with:

- One owner implements ordinary changes and runs focused validation.
- At most two investigation workers when questions are independent and substantial; no automatic recursive delegation.
- An independent reviewer for security boundaries, migrations, and large behavioral changes.
- A concrete handoff: objective, owned files, constraints, evidence needed, and stop condition. Return findings rather than raw context dumps.

Do not mutate permanent agent instructions automatically because one task revealed an exception. Retain durable lessons only when they generalize. Load subsystem instructions when needed, not every role's entire manual at startup. Prefer a small capability vocabulary—research, code, browser, account action, workstation—over spawning a daemon for every persona.

As models improve, periodically test the single-agent baseline against delegation. Keep extra agents only when they improve task quality or latency after counting tokens, RAM, coordination, and review cost. One strong agent with one skeptical reviewer may outperform a standing committee; validate on representative tasks rather than assuming it.

## 9. Execution plan after this proposal

All steps below are future work. The current task creates this document only. A security-policy change or removal of a used integration needs an explicit selected scope; this proposal is not deployment approval.

| Batch | Work and likely files | Success gate | Rollback |
| --- | --- | --- | --- |
| 0. Reconcile and measure | Re-read dirty status, `todo-list.md`, session state; recover disk headroom under a separately scoped cleanup; add bounded resource diagnostics under `scripts/` if needed | Known deployed/source split; incident classification; 24–48 h baseline; no private-content capture | Remove diagnostic sampler; preserve all existing data and changes |
| 1. Reduce residency | Private gateway/autostart choices; plugin/MCP enablement; `docs/profiles.md`; avoid editing public files with private inventories | Only required inboxes resident; existing identities intact; stop/cancel works; measured idle and peak comparison | Restore prior private settings and selected gateway state |
| 2. Bound execution | Relevant Hermes execution seam/plugin or existing broker; `infra/compose/`; `docs/operations.md` | Admission/cancel/crash tests; no gateway starvation; no arbitrary launcher authority | Disable admission pilot/worker backend and restore prior execution path |
| 3. Remove dead branches | Browserless/CDP files, `scripts/aliases.sh`, installer/layer tests, `docs/layers.md`, `docs/patches.md`; then legacy-router decision | Every affected workflow replaced or explicitly retired; all supported Compose shapes render; credential stores untouched | Revert coherent source batch and restore previous configuration/images where retained |
| 4. Trim patch and workflow portfolio | `apps/hermes-agent/patches/`, Dockerfile, plugin sources, `.github/agents/`, `.github/prompts/` | Exact-version parity; boundary regressions pass; routine tasks need fewer handoffs | Restore individual patch/role definition; do not combine unrelated retirements |
| 5. Decouple optional health | Compose dependencies/healthchecks and owning runtime readiness, `docs/operations.md` | Search/telemetry/browser failure leaves valid unrelated work available; missing mandatory security policy still prevents execution | Restore previous dependency/readiness contract |
| 6. Pilot public-web policy | Explicit alternative policy in proxy/DNS boundary and selected private workflow; `docs/security.md` | Local/LAN/metadata/rebinding/mixed-answer/redirect denial; no fallback; accepted public-egress tradeoff | Select strict policy, drain tasks/connections, restart only affected services as required; verify strict policy active |
| 7. Trial observability | Existing Hermes integration if supported; optional deployment outside laptop; `docs/operations.md` | Redacted export, bounded outage behavior, no duplicate usage accounting, useful analysis | Disable exporter; apply verified trace deletion/retention procedure |

Do not do all batches at once. Batches 1–2 depend on baseline evidence. Batch 3 needs the workflow inventory. Patch retirement is independent of public-web policy. The observability trial should not delay resource fixes. A future public packaging change needs migration aliases and documentation before removing supported tiers.

Suggested investigation handoffs for a future agent: one worker attributes resources without reading secrets; another maps a single compatibility branch and its consumers; the owner selects one batch. After implementation, a fresh reviewer checks the resulting behavior and rollback. Use the actual agent tools available in that session, not copied orchestration syntax or a blanket twelve-agent ceiling.

### Validation strategy

Documentation-only preparation needs link/path, consistency, and diff checks; no stack startup, builds, or broad regression run. Implementation needs the narrow failing/characterization test first, then the affected subsystem suite. Security tests must demonstrate denial, not just successful operation. Render every affected supported Compose shape without starting services. Use Node 22 for ClawRoute checks if that code changes; do not build Browserless or ClawRoute images. A Hermes rebuild is a last resort with more than 10 GB free.

For resource work, use comparable workloads and report task quality, peak memory, latency, queueing, and failure counts—not just reduced idle RSS. For browser retirement, inventory Meet, Chrome DevTools MCP, debugger/profile seeding, CDP brokers, and non-Browserless consumers before deletion. For optional readiness, test failure and recovery without unsafe fallback. For worker isolation, test timeout, process-tree cleanup, revocation, denied mounts/network, and preservation of gateway health.

Stop an experiment on OOM, lost persisted state, identity crossover, unexpected external access, unbounded telemetry growth, or loss of cancellation. Returning to strict policy cannot retract data already sent; deleting a trace exporter cannot undo past exports. Rollback prevents further exposure, while incident recovery is a separate task.

## 10. Success criteria and decisions still needed

The redesign succeeds if it makes ordinary work easier while keeping the laptop responsive and the important assets recoverable. Proposed scorecard:

- Zero OOM kills in a seven-day representative acceptance period; stable stop/cancel behavior under an intentional bounded overload test.
- Measured resident/process and peak memory reduction without loss of required channel coverage; set a numerical target after attribution, not before.
- One supported persistent browser and one primary routing implementation, with intentional exceptions recorded.
- Every retained patch has an owner, reason, parity test, and removal condition.
- Ordinary tasks avoid mandatory multi-agent choreography; delegation proves value on a small task benchmark.
- Optional service failure does not take down unrelated work; security validation failure still denies the affected capability.
- Observability remains optional and resource-bounded, with verified data minimization and retention.
- Fewer user interruptions per completed task, without additional unintended external actions or lost work.

Decisions for implementation: which inboxes truly need independent resident gateways; which CDP-only workflows remain valuable; whether broader public egress is acceptable for selected data; whether another machine is available; and whether centralized routing/quotas justify their continuing maintenance. None needs to block this plan or justify speculative cleanup now.

## Reset handoff

Read this document, `agent-lessons.md`, `git status --short`, and the latest session state first. The worktree already contained substantial unrelated refresh changes; this investigation preserved them. Do not repeat the Hermes refresh merely because the older task list still lists it. Do not remove the active managed-browser overlay until its functionality is present and validated in the deployed image.

**Completed here:** architecture/resource/OSS investigation, this proposal, and independent architecture/OSS review. Review corrections clarified that workflow duplication is not proof of multiple running frameworks, and browser task consent is not enforced per-action account authority. Local Markdown links and formatting were checked. **Not completed or authorized here:** cleanup, configuration changes, service restarts, code removal, trace export, security relaxation, builds, commits, or deployment. Browser/profile stores were not inspected. The next useful implementation task is Batch 0, followed by a small measured residency/concurrency change—not a new framework.

This file is outside ignored `plans/` directories so it is visible to Git and survives a conversation reset as a normal workspace file. It is intentionally a proposal rather than a replacement for current operating/security contracts.
