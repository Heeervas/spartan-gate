#!/usr/bin/env python3
"""Bind native Hermes /plan to enabled portable planning, preserving plan-only UX."""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import stat
import tempfile

# Exact supported source: agent/plan_prompt.py from the inspected native source.
# Rebase deliberately on upstream drift; never replace an unknown prompt policy.
UPSTREAM_SHA256 = "81a4ce937a78eb5b766c6ecdc414a7d0570e8875845b46f1a63b5fe70ec1b45b"
PATCHED_SHA256 = "832a53a6e9961f0a28123eddd79c3955ab25c209253a324407eefba3efe01b77"

PLAN_MODE_RULES = """\
For this turn, you are in PLAN MODE — planning only.

- Do not implement code.
- Do not edit project files except the plan markdown file itself.
- Do not run mutating terminal commands, commit, push, or perform external
  actions. You may inspect context with read-only commands/tools.
- Save one markdown plan in the active backend workspace. Resolve its sole
  output owner in this order:
  1. If the runtime provides a specific target path, use that exact path. An
     immutable backend target is the sole explicitly mapped owner; report a
     material conflict with project routing instead of creating another plan.
  2. Otherwise use the existing declared canonical plan owner, including an
     explicitly authorized user output target or established project/vault map.
  3. For a governed code project without a declared plan owner, use `plans/`
     with a meaningful markdown filename.
  4. For an unmapped/non-code workspace, preserve the native default
     `.hermes/plans/YYYY-MM-DD_HHMMSS-<slug>.md`.
- Use backend-aware file tools; create only the containing directory needed
  for that artifact. Do not maintain two active plans or change project routing
  files during this turn. The only writable deliverable is the plan itself.
- After saving, reply briefly with scope, actual path and unresolved gates.
  Do not start executing in this turn, even if earlier implementation authority
  exists. A delivered plan is not necessarily execution-ready.
"""

PLAN_CRAFT = """\
Planning is common to every active profile and domain. A development plan from
Work or Market does not require switching to Builder. Preserve the active
profile's actual capabilities, permissions and domain authority. Non-code plans do not require repository structure,
code conventions, code snippets, TDD or commit steps. Scale depth to the outcome.

Use the enabled canonical writing-plans method when native discovery permits it.
First resolve the active profile/platform's enabled skill discovery and controls.
Do not bypass a disabled/unavailable method by alias, filesystem path, preload,
compatibility router or another profile. File presence is not enabled discovery.

When shared-skills and writing-plans are both available and enabled, actually
read these through the native skill loader, in this order:
1. skill_view(name="shared-skills")
2. skill_view(name="shared-skills", file_path="references/hermes.md")
3. skill_view(name="writing-plans")
4. skill_view(name="writing-plans", file_path="references/hermes.md")
Then read the method's mandatory task supports through their enabled owners.
Discovery/slash expansion does not automatically include these references.
Apply that one planning method only with its complete enabled and bound support
chain, within the plan-only/output rules above. It owns proportional depth,
dependencies, ownership and evidence; this native entrypoint adds no timed task
sizes, mandatory full code, universal TDD or commit ritual.
If any required core, adapter or mandatory task support is missing, disabled, unavailable or unbound,
even after partial shared loading, stop the dependent shared operation. Disclose the incomplete shared-method gate
and exact missing requirement, then continue with the native fallback below.
Do not claim that partial loading completed the shared method.

Independent minimal native fallback: when that complete chain cannot be used,
do not load its contents by another route. State that the shared method did not run
to completion. Under the same plan-only rules, save a native plan describing
the requested result, inspected facts and assumptions, proposed approach,
decisive observable checks and unresolved decisions or gates. Do not reproduce
the denied method or pretend its required review/readiness gates passed.

Delivery and execution readiness are separate. Substantial plans involving
material uncertainty, coupling, consequence or independently owned work packages
require independent adversarial plan review before implementation. Respect
explicit user/project review policy and endpoint; fully specified, bounded,
reversible work may stay lightweight. If required review is unavailable, mark
execution readiness incomplete. Do not delegate merely to finish this plan-only turn.
Neither native fallback nor delivery satisfies a missing review requirement.

If the request is clear enough, plan directly. If a material ambiguity remains,
ask only the clarification needed. An empty task may use conversation context.
This /plan turn ends after the saved plan and brief path response; execution
requires a subsequent request within its actual authority.
"""


def patch_source(source: str) -> str:
    """Replace only known policy assignments; all other bytes remain unchanged."""
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if digest == PATCHED_SHA256:
        return source
    if digest != UPSTREAM_SHA256:
        raise ValueError("Unsupported Hermes plan prompt fingerprint; rebase the patch")
    tree = ast.parse(source)
    replacements = {"_PLAN_MODE_RULES": PLAN_MODE_RULES, "_PLAN_CRAFT": PLAN_CRAFT}
    edits = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in replacements:
                edits.append((node.lineno - 1, node.end_lineno,
                              f"{name} = {replacements.pop(name)!r}\n"))
    if replacements:
        raise ValueError("Hermes plan policy assignments changed")
    lines = source.splitlines(keepends=True)
    for start, end, replacement in reversed(edits):
        lines[start:end] = [replacement]
    result = "".join(lines)
    compile(result, "agent/plan_prompt.py", "exec")
    return result


def patch_file(target: Path) -> bool:
    """Validate before effects and atomically replace the one regular source file."""
    if target.is_symlink() or not target.is_file():
        raise ValueError("Hermes plan target must be an existing regular file")
    source = target.read_bytes()
    result = patch_source(source.decode("utf-8")).encode("utf-8")
    if result == source:
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), stat.S_IMODE(target.stat().st_mode))
            handle.write(result)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", type=Path,
                        default=Path("/opt/hermes/agent/plan_prompt.py"))
    args = parser.parse_args()
    try:
        patch_file(args.target)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Hermes portable plan patch refused: {exc}\n")


if __name__ == "__main__":
    main()
