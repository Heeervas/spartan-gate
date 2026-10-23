"""Opt-in required-skill guidance at Hermes' native catalog renderer.

Usage: python3 patch_skill_selection_guidance.py [prompt_builder.py]
"""
from pathlib import Path
import sys

TARGET = Path('/opt/hermes/agent/prompt_builder.py')
MARKER = '# Spartan Gate: profile-configured skill selection, before prose composition.'
HELPER = '''# Spartan Gate: profile-configured skill selection, before prose composition.
def _spartan_required_skill_selection() -> bool:
    skills = _config_readonly("skills.selection_policy").get("skills")
    return isinstance(skills, dict) and skills.get("selection_policy") == "required"


'''
ANCHOR = 'def _render_skills_index(\n'
BRANCH = '''    from agent.oneshot_footprint import ONESHOT_SKILLS_LOAD_GUIDANCE, is_single_query_session
    if is_single_query_session():
'''
REPLACEMENT = '''    from agent.oneshot_footprint import ONESHOT_SKILLS_LOAD_GUIDANCE, is_single_query_session
    if _spartan_required_skill_selection():
        return (
            "## Skills\\n"
            "Load skills explicitly requested, required by the active profile/project, or clearly needed "
            "for this task, using skill_view(name). Include every mandatory method and only distinct "
            "additional support; partial topic overlap alone is insufficient. Familiarity does not waive "
            "local requirements, including in one-shot work. Read selected skills fully and their required "
            "adapters/supports; reuse only complete current in-context reads and reload pruned material. "
            "Preserve native disables and capability limits. Skill maintenance requires separate applicable "
            "authority; one-shot sessions still cannot create or edit skills.\\n"
            + "\\n<available_skills>\\n" + "\\n".join(index_lines) + "\\n</available_skills>"
            + hidden_note
        )
    if is_single_query_session():
'''
CACHE = '        _oneshot_prompt_variant(),\n'
CACHE_REPLACEMENT = '        _oneshot_prompt_variant(), _spartan_required_skill_selection(),\n'


def patch_text(source: str) -> str:
    """Accept only the exact supported source or the complete patched shape."""
    patched = MARKER in source
    replacements = ((ANCHOR, HELPER + ANCHOR), (BRANCH, REPLACEMENT), (CACHE, CACHE_REPLACEMENT))
    for original, changed in replacements:
        expected = changed if patched else original
        if source.count(expected) != 1:
            raise ValueError('skill selection anchors changed or patch is incomplete')
    result = source
    if not patched:
        for original, changed in replacements:
            result = result.replace(original, changed, 1)
    compile(result, str(TARGET), 'exec')
    return result


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) == 2 else TARGET
    source = target.read_text(encoding='utf-8')
    result = patch_text(source)
    if source != result:
        target.write_text(result, encoding='utf-8')
    print('Skill selection guidance ready')


if __name__ == '__main__':
    main()
