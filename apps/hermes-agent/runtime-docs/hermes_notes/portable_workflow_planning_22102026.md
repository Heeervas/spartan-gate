# Portable workflow and native planning

As of 2026-10-22, native `/plan` already exists on the common CLI/gateway route for all profiles, including Work and Market. It builds a normal planning-only agent turn; the active profile still owns its tools, permissions and domain authority. A development plan does not require switching to Builder. Non-code planning can use the same entrypoint.

Use this today in the current profile, replacing the example task:

```text
/plan Plan development of a client portal for the current Work project. Planning only: inspect with read-only tools, save one Markdown plan and stop. Do not implement, commit or perform external actions. Describe the outcome, inspected facts and assumptions, proposed steps, decisive checks and unresolved gates. Apply code conventions only where this task needs code. Use shared writing-plans only if native discovery permits its entire enabled core, Hermes adapter and mandatory support chain. If any dependency is disabled, unavailable or unbound, disclose the incomplete shared-method gate and use a native plan under these same limits; do not bypass controls. Report whether independent review is still required before implementation.
```

For Market, replace the task with the desired development plan; for a non-code task, state that it is non-code and omit code structure, TDD and commit conventions. With no explicit runtime target, today's unpatched native prompt saves under `.hermes/plans/YYYY-MM-DD_HHMMSS-<slug>.md` in the active backend workspace. If the runtime supplies a target, that exact path owns the output. This explicit request works with the existing command; it does not certify that the running native prompt contains the proposed policy below. Native generation is model behavior subject to the actual profile's controls.

A shared plan requires actual native reads of shared-skills, its Hermes adapter, writing-plans, its Hermes adapter and every mandatory task support through their enabled owners. File presence or a partial load is insufficient. A missing governance/support binding in Work or another domain profile leaves shared-method compliance incomplete; the native alternative above remains available without enrolling that profile or changing its permissions.

The optional source patch `patches/patch_portable_plan.py` is retained for isolated opt-in evaluation. It is not running. Normal Dockerfile builds no longer invoke it; neither this note nor a passing test authorizes image builds, runtime patching or service recreation. The proposed policy preserves the plan-only turn, exact runtime target and sole output owner, and unchanged native function/task API. It adds profile/domain-neutral planning and an explicit native fallback for any missing, disabled, unavailable or unbound core/adapter/mandatory support, including failure after partial loading. It removes the upstream fixed task durations, mandatory full code, universal TDD and per-task commit prescription.

Plan delivery and implementation readiness are separate. Under the proposed policy, substantial work with material uncertainty, coupling, consequence or independently owned work packages requires independent adversarial plan review before implementation, subject to explicit user/project review policy. Missing review leaves readiness incomplete; a plan-only turn does not require delegation. Fully specified bounded reversible work stays proportional. Shared workflows retain explicit frame, inspect, plan, execute, verify and close endpoints with evidence-based resume.

To evaluate the patch against a disposable copy of the inspected source, run from this delivery directory:

```sh
HERMES_UPSTREAM_SOURCE=/path/to/disposable-hermes-source python -m unittest discover -s . -p test_portable_plan_patch.py -v
```

The supported pristine `agent/plan_prompt.py` SHA256 is `81a4ce937a78eb5b766c6ecdc414a7d0570e8875845b46f1a63b5fe70ec1b45b`; unknown or partially modified source is refused. Tests exercise the actual native prompt producer, preserved API/task interpolation, output-owner order, optional-policy wording, Dockerfile nonactivation, idempotence, atomic-write failure and drift refusal. Prompt policy assertions establish source behavior, not model compliance or deployed fallback behavior. Fresh runtime execution, comparative plan-quality evidence and independently reviewed activation remain separate gates.
