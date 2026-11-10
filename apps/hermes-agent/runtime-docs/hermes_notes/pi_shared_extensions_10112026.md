# Pi shared extensions — 10/11/2026

Status: applied operator-local `pi-rename-thread` enrollment; broader package parity
is a pending recommendation, not an automatic synchronization policy.

## Applied

The existing Hermes Pi owner now loads the maintained `pi-rename-thread` package
from the already approved shared project collection. Native `pi install` adds a
local package declaration; it resolves to
`/opt/data/workspace/projects/shared/pi-extensions/pi-rename-thread`. There is no
copied extension entry, additional mount, package download, image build or service
restart. Public baseline/example settings are unchanged. Existing native settings,
credentials, sessions, trust, model selection and subagent extension defaults remain
unchanged apart from this parent package enrollment.

A fresh native SDK loader running as workload UID 1000 on container Pi 1.5.0 /
Node 26.12.1 loaded the package once, registered `rename-thread` and `thread-inbox`,
and reported zero extension errors. Route provider/cache/usage, subagents and Fast
remained loaded. Checks were offline, with no model requests or session lifecycle
startup; they do not prove live naming output or interactive rendering. New Pi
processes load the addition; existing processes need a normal reload or restart.
The existing Work owner shares the same Pi state and therefore receives it too;
other owners are not automatically provisioned.

Automatic naming applies only to eligible new interactive TUI sessions, not
headless/RPC/subagent jobs. It sends a bounded conversation excerpt to the configured
naming model and may incur inference cost. Manual status commands do not require
inference. Followups stay under the selected native agent directory; host sessions
and followup state are not shared.

## Pending recommendation

Share reviewed program code and a small explicit package selection, not the host
agent directory, auth, trust, sessions or mutable extension state. Navigation,
timing and plan packages are plausible next candidates, subject to native-version
and container behavior checks. Review stop/process-control behavior separately;
do not import host-wide kill assumptions. Keep web access on the approved managed
network/tool path rather than copying a second unreviewed transport. Orca-specific
host extensions should stay host-only without a supported container integration.

Local source edits are visible to future extension loads because the shared
collection is writable. This is coordinated source reuse, not an immutable release
or per-profile isolation. Native removal of this package declaration is the scoped
rollback; preserve private settings recovery and do not delete its shared source.

This note is an operator archive, not a published runtime guide.
