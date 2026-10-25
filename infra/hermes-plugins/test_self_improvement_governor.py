"""One native prompt-composition check; run in the pinned Hermes environment.

No agent is constructed, review target executed, thread started or provider called.
"""
import importlib.util
from pathlib import Path
import unittest


class NativeReviewPromptTests(unittest.TestCase):
    def test_additive_skill_rules_leave_native_loop_and_memory_prompt_unchanged(self):
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
                    self.assertEqual(getattr(owner, name), original[owner, name] + "\n\n" + plugin.REVIEW_RULES)
            for memory, skills, name in ((False, True, names[0]), (True, True, names[1]), (True, False, names[2])):
                with self.subTest(memory=memory, skills=skills):
                    target, prompt = spawn(AIAgent, [], review_memory=memory, review_skills=skills, task_cfg={})
                    self.assertTrue(callable(target))  # Deliberately never executed.
                    self.assertEqual(prompt, getattr(AIAgent, name))
                    self.assertEqual(prompt.count(plugin.REVIEW_RULES), int(skills))
            self.assertIn("skill-optimizer and skill-workflow-refiner in full", plugin.REVIEW_RULES)
            self.assertIn("mandatory\nHermes adapters and supports", plugin.REVIEW_RULES)
            self.assertIn("For additions and removals", plugin.REVIEW_RULES)
            self.assertIn("SKILL.md pointer and when-to-read routing", plugin.REVIEW_RULES)
        finally:
            for (owner, name), text in original.items():
                setattr(owner, name, text)


if __name__ == "__main__":
    unittest.main()
