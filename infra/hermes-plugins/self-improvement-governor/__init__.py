"""Append method/routing instructions to native review prompts; no loop changes."""

REVIEW_RULES = """Skill review methods and content routing:
Before creating, editing, moving or removing skill content in this review, read
skill-optimizer and skill-workflow-refiner in full, including their mandatory
Hermes adapters and supports, and apply their relevant procedures. Naming a skill
or reading a summary is not a full read. If a required method is disabled or
unavailable, skip its dependent changes rather than bypassing that restriction.
For additions and removals, keep generic reusable class-level procedure in
SKILL.md. Put detailed domain/tool-specific procedures, examples and rare cases
in topical references/ files, extending an existing topic when appropriate. Add
or update the SKILL.md pointer and when-to-read routing. Preserve useful generic
rules when moving detail. Private client/project facts belong in their authorized
Brain/project owner, not generic references; use approved pointers where needed.
Preserve existing permissions, canonical ownership and read-before-write rules."""


def register(_ctx):
    # Native prompts are module defaults plus AIAgent class attributes; the
    # native spawn function explicitly supports these prompt overrides.
    from agent import background_review
    from run_agent import AIAgent

    for owner in (background_review, AIAgent):
        for name in ("_SKILL_REVIEW_PROMPT", "_COMBINED_REVIEW_PROMPT"):
            original = getattr(owner, name)
            if REVIEW_RULES not in original:
                setattr(owner, name, original + "\n\n" + REVIEW_RULES)
