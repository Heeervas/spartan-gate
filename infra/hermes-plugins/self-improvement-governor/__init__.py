"""Outcome-led skill learning on native review prompts; no loop or gate changes."""

REVIEW_RULES = """Skill learning: candidates, not obligations.
Observations are candidates, not obligations; 'Nothing to save.' is a successful
outcome. There is no quota of updates, proposals or token reduction.
Before proposing skill content, read skill-optimizer and skill-workflow-refiner
in full, including their mandatory Hermes adapters and applicable supports.
Missing or disabled methods block dependent proposals, never justify a bypass.
Consider the package's purpose, current evidence and existing guidance together.
Prefer correcting, replacing, merging or removing redundancy over appending;
add only a justified durable improvement. Evaluate total required reading,
including entry methods, adapters and supports, not just SKILL.md size. Moving
mandatory text into references is not a saving; never sacrifice protected rules.
Keep generic procedure in SKILL.md, optional topical depth in existing references
where suitable, with clear when-to-read routing. A preference belongs in its
owning task skill, or USER.md if cross-cutting and no skill owns it, never both.
Private client/project facts stay with their authorized Brain/project owner.
Preserve permissions, native read-before-write and canonical ownership. A shared
package remains externally owned even through a local link or bind mount.
Do not adopt, fork, duplicate or switch targets to evade protected ownership;
report a useful observation to its owner instead. Keep skills.write_approval on.
Ordinary learning only stages eligible proposals through skill_manage; never
approve, apply or discard pending records from this background review. A staged
change is not applied; verify its exact pending ID exists before claiming saved.
"""


def _review_prompt(original, native, *, combined):
    """Retain native safety/routing blocks, replacing the obligation-led prose."""
    if REVIEW_RULES in original:
        return original
    read_start = original.index('Read-before-write (ENFORCED')
    read_end = original.index('\n\nUser-preference embedding', read_start)
    protected_start = original.index('Protected skills (DO NOT edit these):')
    protected_end = original.index('Do NOT capture', protected_start)
    protected = original[protected_start:protected_end]
    # Native opt-in advice is not authority to transfer canonical ownership.
    protected = protected.replace(
        "recommend 'hermes curator adopt <name>'", 'refer it to its canonical owner')
    prefix = ''
    if combined:
        prefix, separator, _ = original.partition('**Skills**:')
        if not separator:
            raise ValueError('native combined review memory/skills boundary changed')
        prefix = prefix.replace('update two things:', 'consider two possible updates:')
    return (prefix + REVIEW_RULES + '\n' + native._LESSON_LAYER_BLOCK
            + original[read_start:read_end] + '\n\n' + protected
            + 'Do NOT capture as skills' + native._DO_NOT_CAPTURE_BLOCK
            + "If no justified, eligible proposal remains, say 'Nothing to save.' and stop.")


def register(_ctx):
    # Native prompts are module defaults plus AIAgent class attributes; the
    # native spawn function explicitly supports these prompt overrides.
    from agent import background_review
    from run_agent import AIAgent

    # Validate every native shape before changing either owner. No partial override.
    updates = [
        (owner, name, _review_prompt(getattr(owner, name), background_review,
                                    combined=name == '_COMBINED_REVIEW_PROMPT'))
        for owner in (background_review, AIAgent)
        for name in ('_SKILL_REVIEW_PROMPT', '_COMBINED_REVIEW_PROMPT')
    ]
    for owner, name, prompt in updates:
        setattr(owner, name, prompt)
