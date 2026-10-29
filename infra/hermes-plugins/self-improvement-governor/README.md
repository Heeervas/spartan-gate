# Native skill learning and maintenance

The plugin overrides Hermes' two skill-bearing review prompts, retaining native
lesson, read-before-write, protected-ownership and memory-routing blocks. It
replaces update pressure with evidence-based candidates and package-level,
subtraction-first review of total required reading. No-op is successful. Native
spawning, tools, memory-only review and write-approval configuration are unchanged.
Unsupported native prompt shapes refuse registration before partial assignment.
These are model instructions, not an OS filesystem boundary.

Ordinary learning stages eligible proposals; it never approves its own changes.
A separately authorized native cron may review and apply local skill-text changes
using optimizer/refiner, then verify files and exact profile/proposal IDs and
report. External/shared packages, executable changes, ownership/config changes
and whole-package deletion/renaming remain excluded. No parallel skill writers.
The cron's outcome-led prompt and pinned model/reasoning live in its native job.

`pending_skill_review.py` is a read-only wake precheck for Hermes' native scripts
directory: no pending records means no model call; unreadable records or a disabled
approval gate wake the reviewer to report the problem. It uses the native profile
registry and separate profile contexts, never copies queue stores. Installation,
job creation and runtime adoption are separately authorized operations; no new
mount, queue, service or skill package is required.

Focused checks in the pinned Hermes environment (no agent/provider calls):

```sh
python -B infra/hermes-plugins/test_self_improvement_governor.py
```

Required review methods must be available under existing native controls; this
plugin does not install/adopt them. The historical `scripts/hermes-self-improvement-governor.py`
embeds a superseded implementation and is not this plugin's activation route.
