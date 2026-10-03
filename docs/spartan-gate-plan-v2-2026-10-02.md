# Spartan Gate plan — version 2

Date: 2026-10-02. Status: proposal. This plan does not change the system.

[Open the visual guide](spartan-gate-plan-v2-2026-10-02.html).

## The decision in one minute

Keep Hermes and the small security services.
First, reduce the number of tasks that start together.
Then measure the result.
Add another service only if the result shows a need.

The objective is simple: Hermes must complete useful work without making the laptop unavailable.

## What we know

These values come from the earlier investigation on 2026-10-02.
They are not current measurements.

| Observation | Meaning |
| --- | --- |
| The computer has about 7.7 GiB of memory. | Desktop programs and Spartan Gate share this memory. |
| Hermes used about 2.27 GiB in the Docker report. | Hermes was the largest measured memory user. |
| Four small security services used about 0.1 GiB together. | Their removal would release little memory. |
| Hermes reached its 3 GiB container limit. | The container had memory pressure. |
| Current container records showed no OOM kills. | The cause of the reported failures is not yet known. |
| The disk had about 3.7 GiB free. | Restore the required free space before new builds or large downloads. |

An OOM kill occurs when the operating system stops a process because memory is insufficient.
Container memory pressure does not always cause an OOM kill.
Docker and container measurements can differ because they count some cached data differently.

## Choose a path

### Path A — improve the current setup

This is the recommended first path.
Keep the current services.
Improve memory checks, reduce simultaneous work, and compare test results.
Some tasks can take longer to start.

### Path B — separate heavy work

Use this path if Path A is insufficient.
Keep Hermes available for messages.
Run heavy code tasks in a separate worker with a memory limit.
The worker adds setup and maintenance work.

### Path C — move heavy work to another computer

Use this path if the laptop cannot support the required tasks.
Keep the local interface and private access controls.
Move selected workers or Langfuse to another computer.
This path needs another computer and a reliable connection.

Paths B and C can be combined.

## The proposed changes

“Start here” means the first implementation group.
“Test next” means a small test before normal use.
“Only if needed” means the change depends on measured results or a required function.

### 01. Show the cause of memory pressure

Stage: **Start here**.

**Change:** Improve the existing memory report. Correct the Hermes check that can miss memory use from other programs.

**Benefit:** You can distinguish a memory limit from a program stop caused by insufficient memory.

**Cost:** The report needs code changes. The Hermes correction can require a new image.

**Test:** Compare the report with container measurements. Check missing data and container restarts.

**Return to the previous setup:** Restore the previous report or Hermes image.

**Important condition:** The current Hermes check can measure one process instead of all processes in its container.

### 02. Start fewer tasks at the same time

Stage: **Start here**.

**Change:** Test two active sessions in one profile. Limit child tasks. Start optional tool servers only when necessary.

**Benefit:** Fewer tasks compete for memory. Idle tool servers can stop.

**Cost:** Some tasks must wait or try again. A stopped tool server needs time to start.

**Test:** Run normal tasks. Check response time, memory use, message delivery, and the stop command.

**Return to the previous setup:** Restore the previous settings. Restart the selected Hermes program and check the active settings.

**Important condition:** These settings do not impose one limit on all profiles. Some child tasks can still run directly.

### 03. Use the same tasks to compare changes

Stage: **Start here**.

**Change:** Create a small test set. Include a code repair, a document search, a browser task, and task cancellation.

**Benefit:** You can see whether a change improves results, speed, or memory use.

**Cost:** Real model tests consume time and model quota.

**Test:** Use the same test data and model settings before and after each change.

**Return to the previous setup:** Remove the test configuration. Keep the results for comparison.

**Important condition:** Use artificial test data. Do not use private messages or account sessions.

### 04. Use fewer permanent Hermes programs

Stage: **Test next**.

**Change:** Test two profiles under one Hermes program. Keep their messages, settings, and browser identities separate.

**Benefit:** Shared program code can reduce memory use.

**Cost:** Some profile controls can behave differently. One program failure can affect both profiles.

**Test:** Use two artificial profiles. Check identity separation, message delivery, and separate stop and restart operations.

**Return to the previous setup:** Restore separate Hermes programs and their previous start settings.

**Important condition:** Hermes already has a migration function. Do not enable it directly on the existing profiles.

### 05. Add a record of task steps

Stage: **Test next**.

**Change:** Connect the existing Hermes Langfuse plugin to another computer or a hosted service.

**Benefit:** You can see slow tools, repeated calls, and failed task steps.

**Cost:** The service needs storage and maintenance. Hosted operation can have a charge.

**Test:** Inspect an artificial task record. Disconnect Langfuse and make sure that Hermes continues to work.

**Return to the previous setup:** Disable the plugin. Apply the chosen deletion policy to records already sent.

**Important condition:** Use metadata mode first. Exclude message content and tool values. Check the identifiers that remain.

### 06. Run heavy tasks in a separate worker

Stage: **Only if needed**.

**Change:** Give heavy code tasks a separate worker with its own memory limit. Test OpenSandbox for worker control.

**Benefit:** A task that exceeds its limit should stop without stopping Hermes.

**Cost:** The worker needs memory and extra control code. A worker on this laptop does not add physical memory.

**Test:** Exceed the worker limit in a controlled test. Check that Hermes still answers and can stop the task.

**Return to the previous setup:** Restore the previous execution method. Stop only the test workers.

**Important condition:** Keep one worker for the complete task. Preserve files between commands. Exclude browser logins and provider secrets.

### 07. Keep unrelated functions available during a failure

Stage: **Test next**.

**Change:** Permit Hermes to start when optional search or page-reading services are unavailable.

**Benefit:** You can still use functions that do not need the failed service.

**Cost:** The status report must show which functions are unavailable.

**Test:** Stop a test search service. Check a code task and the stop command. Then restore search.

**Return to the previous setup:** Restore the previous start conditions and health check.

**Important condition:** Keep mandatory security checks. Do not select another browser automatically when Camofox fails.

### 08. Test a replacement for Spartan Route

Stage: **Only if needed**.

**Change:** Compare CLIProxyAPI or LiteLLM with Spartan Route in a separate test setup.

**Benefit:** An external project could maintain more provider connection code.

**Cost:** A replacement might lose our quota rules, account selection, or session recovery behavior.

**Test:** Check account access, interrupted responses, retries, session separation, and quota records.

**Return to the previous setup:** Keep production requests on Spartan Route until the replacement passes the required tests.

**Important condition:** Langfuse records task activity. Langfuse does not replace model routing or provider credentials.

### 09. Remove browser code that no task needs

Stage: **Only if needed**.

**Change:** Make Camofox the supported browser. Remove Browserless connections after checking every dependent task.

**Benefit:** Fewer browser connections and local code changes need maintenance.

**Cost:** Some meeting or Chrome-specific functions can need a replacement.

**Test:** Check each browser task before removal. Preserve browser account data.

**Return to the previous setup:** Restore the previous code and configuration for the affected connection.

**Important condition:** Browserless was not running during the measurement. Its removal will not release memory from an inactive service.

### 10. Make tool and network choices easier to understand

Stage: **Only if needed**.

**Change:** Add a network explanation command. Give each task only the tool descriptions that the task needs.

**Benefit:** You can see why a connection fails. The model receives less unrelated tool information.

**Cost:** A smaller tool set can omit a required function.

**Test:** Check allowed and blocked connections. Repeat the document-search task with the smaller tool set.

**Return to the previous setup:** Restore the previous tool list. Remove the new explanation command if it is not useful.

**Important condition:** Temporary domain access already exists. Broader public internet access needs a separate decision and network tests.

## The first implementation group

1. Restore sufficient free disk space through a separate cleanup task.
2. Add the improved memory report from proposal 01.
3. Record the current result for the test tasks in proposal 03.
4. Apply proposal 02 to one selected profile.
5. Restart that profile through the existing operator procedure.
6. Check that the new settings are active.
7. Repeat the same test tasks.
8. Keep the change only if useful task results remain correct.

Record memory use, task duration, message delivery, and the result of the stop command.
Do not change service memory limits during this first comparison.
If a required function fails, restore the previous settings.

Keep at least 5 GB of disk space free during operations.
Start a Hermes image build only when more than 10 GB is free.

## Which protections should remain?

| Keep | Reason |
| --- | --- |
| Separate provider secrets from general task code. | A failed or unsafe tool must not receive every account secret. |
| Restrict access to the host and private network. | Public web access must not permit access to private devices. |
| Keep browser identities separate. | One task must not use another profile's account by mistake. |
| Keep memory limits and a working stop command. | One task must not make the whole computer unavailable. |
| Keep file recovery and previous configurations. | A reversible error should be easy to correct. |

Reduce repeated approval requests for reversible work in an assigned workspace.
Keep clear authorization for publication, payments, account changes, and destructive operations.

Trust in Hermes can reduce unnecessary interruptions.
Trust does not stop a software defect or a tool that uses too much memory.
Browser login data can give broad account access.
A task description does not technically restrict every browser action.

## What should we add?

| Tool | Proposed use | Decision |
| --- | --- | --- |
| Langfuse | Show task steps, duration, and failures. | Test on another computer or a hosted service. |
| OpenSandbox | Create and remove separate task workers. | Test only if heavy tasks still affect Hermes. |
| Promptfoo | Run repeatable task checks. | Optional. A small Python test program can come first. |
| CLIProxyAPI or LiteLLM | Replace some custom model connection code. | Compare with Spartan Route before replacement. |

Do not install all these tools together.
Each new tool must solve a measured problem or replace code that we maintain.

## Terms

- **Hermes:** The assistant program that receives requests and uses tools.
- **Profile:** A set of assistant settings, message connections, and session data.
- **Session:** One conversation and its related task state.
- **Process:** One running program instance.
- **Container:** An environment with separate program settings and resource limits. Containers still share computer resources.
- **Worker:** A separate program environment that performs an assigned task.
- **Tool server:** A program that gives Hermes access to a tool. MCP is one protocol for this connection.
- **Plugin:** An extension that adds a function to Hermes.
- **Memory:** Temporary storage that running programs use. Memory is different from disk space.
- **GiB:** A unit that measures memory capacity.
- **Metadata:** Information about a task, such as its duration or tool name. Metadata can still contain sensitive identifiers.
- **Router:** A service that sends model requests to the selected provider. Spartan Route is the current router.
- **Image:** A packaged version of a program and its dependencies.

## Technical reference

The [implementation blueprint](spartan-gate-implementation-blueprint-2026-10-02.md) contains exact settings, source references, file changes, and detailed tests.
The [first assessment](spartan-gate-reimagining-2026-10-02.md) contains the original memory measurements and design choices.

This version changes the explanation. It does not authorize deployment or removal of services.

Language note: This document uses ASD-STE100 writing principles and defined technical terms.
A formal check of all dictionary words and their permitted meanings is still necessary for a conformance claim.
See the [official ASD-STE100 description](https://www.asd-ste100.org/about_STE.html).
