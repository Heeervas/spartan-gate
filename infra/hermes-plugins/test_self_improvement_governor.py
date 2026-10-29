"""Focused native prompt and isolated approval checks; no agent/provider calls."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class NativeReviewPromptTests(unittest.TestCase):
    def test_candidate_rules_preserve_native_safety_memory_and_loop(self):
        from agent import background_review
        from run_agent import AIAgent

        path = Path(__file__).parent / "self-improvement-governor" / "__init__.py"
        spec = importlib.util.spec_from_file_location("review_prompt_fixture", path)
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        names = ("_SKILL_REVIEW_PROMPT", "_COMBINED_REVIEW_PROMPT", "_MEMORY_REVIEW_PROMPT")
        owners = (background_review, AIAgent)
        original = {(owner, name): getattr(owner, name) for owner in owners for name in names}
        spawn = background_review.spawn_background_review_thread
        try:
            plugin.register(None)
            plugin.register(None)
            self.assertIs(background_review.spawn_background_review_thread, spawn)
            for owner in owners:
                self.assertEqual(owner._MEMORY_REVIEW_PROMPT, original[owner, "_MEMORY_REVIEW_PROMPT"])
                for name in names[:2]:
                    current = getattr(owner, name)
                    self.assertEqual(current.count(plugin.REVIEW_RULES), 1)
                    for pressure in ('Be ACTIVE', 'missed learning opportunity', 'warrants action',
                                     'any one is enough', 'Patch it NOW', 'Otherwise, act'):
                        self.assertNotIn(pressure, current)
                    self.assertIn('Protected skills (DO NOT edit these):', current)
                    self.assertIn('Read-before-write (ENFORCED', current)
                    self.assertIn(background_review._DO_NOT_CAPTURE_BLOCK, current)
                    self.assertLess(len(current), len(original[owner, name]))
                self.assertIn(background_review._MEMORY_ROUTING_BLOCK, owner._COMBINED_REVIEW_PROMPT)
                composed = owner._SKILL_REVIEW_PROMPT
                plugin.register(None)
                self.assertEqual(owner._SKILL_REVIEW_PROMPT, composed)
            for memory, skills, name in ((False, True, names[0]), (True, True, names[1]), (True, False, names[2])):
                with self.subTest(memory=memory, skills=skills):
                    target, prompt = spawn(AIAgent, [], review_memory=memory, review_skills=skills, task_cfg={})
                    self.assertTrue(callable(target))  # Deliberately never executed.
                    self.assertEqual(prompt, getattr(AIAgent, name))
                    self.assertEqual(prompt.count(plugin.REVIEW_RULES), int(skills))
            for (owner, name), text in original.items():
                setattr(owner, name, text)
            with patch.object(AIAgent, '_COMBINED_REVIEW_PROMPT', 'unsupported native shape'):
                before = {(o, n): getattr(o, n) for o in owners for n in names}
                with self.assertRaises(ValueError):
                    plugin.register(None)
                self.assertEqual(before, {(o, n): getattr(o, n) for o in owners for n in names})
        finally:
            for (owner, name), text in original.items():
                setattr(owner, name, text)

    def test_native_stage_approve_and_wake_gate_in_disposable_home(self):
        script = Path(__file__).parent / 'self-improvement-governor' / 'pending_skill_review.py'
        # The subprocess prevents native profile/config caches leaking into the fixture.
        code = '''
import contextlib, io, json, runpy, subprocess, sys
from pathlib import Path
from unittest.mock import patch
from hermes_constants import get_hermes_home
from tools import write_approval as wa
from tools.skill_manager_tool import skill_manage
from hermes_cli.write_approval_commands import handle_pending_subcommand
root = get_hermes_home()
root.joinpath('config.yaml').write_text('skills:\\n  write_approval: true\\n')
def wake():
    return json.loads(subprocess.check_output([sys.executable, '-B', sys.argv[1]], text=True))
def files():
    return {str(p.relative_to(root)): (p.stat().st_mode, p.read_bytes() if p.is_file() else None)
            for p in root.rglob('*')}
before = files()
assert wake()['wakeAgent'] is False
assert files() == before, 'empty-queue precheck mutated profile files or permissions'
root.joinpath('profiles').mkdir()
output = io.StringIO()
with patch('hermes_cli.profiles._iter_named_profile_dirs', side_effect=PermissionError('registry unreadable')):
    with contextlib.redirect_stdout(output):
        runpy.run_path(sys.argv[1], run_name='__main__')
coverage = json.loads(output.getvalue())
assert coverage['wakeAgent'] is True and 'coverage_error' in coverage
skill = root / 'skills' / 'review-fixture' / 'SKILL.md'
skill.parent.mkdir(parents=True)
content = '---\\nname: review-fixture\\ndescription: Use when checking review fixtures.\\n---\\nKeep old guidance.\\n'
skill.write_text(content)
result = json.loads(skill_manage(action='patch', name='review-fixture',
                                 old_string='old guidance', new_string='better guidance'))
assert result['staged'] and skill.read_text() == content
pid = result['pending_id']
other = wa.stage_write('skills', {'action': 'patch', 'name': 'untouched'},
                       summary='unrelated', origin='foreground')['id']
other_file = root / 'pending' / 'skills' / (other + '.json')
other_bytes = other_file.read_bytes()
assert wake()['wakeAgent'] is True
reply = handle_pending_subcommand('skills', ['approve', pid])
assert 'Approved 1 skills write(s).' in reply, reply
assert skill.read_text() == content.replace('old guidance', 'better guidance')
assert wa.get_pending('skills', pid) is None
assert other_file.read_bytes() == other_bytes
assert wa.write_approval_enabled('skills') is True
handle_pending_subcommand('skills', ['reject', other])
assert wake()['wakeAgent'] is False
root.joinpath('pending/skills/unreadable.json').write_text('{')
assert wake()['wakeAgent'] is True
'''
        with tempfile.TemporaryDirectory() as home:
            subprocess.run([sys.executable, '-B', '-c', code, str(script)], check=True,
                           env={**os.environ, 'HERMES_HOME': home,
                                'PYTHONDONTWRITEBYTECODE': '1'})


if __name__ == "__main__":
    unittest.main()
