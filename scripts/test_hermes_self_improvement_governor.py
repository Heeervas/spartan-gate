import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "hermes-self-improvement-governor.py"


BASE_CONFIG = """\
skills:
  creation_nudge_interval: 15
plugins:
  enabled:
  - node-search
  disabled: []
"""


class HermesSelfImprovementGovernorTests(unittest.TestCase):
    def test_installs_plugin_config_and_refiner_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config.yaml").write_text(BASE_CONFIG, encoding="utf-8")
            central_refiner = root / "skills" / "autonomous-ai-agents" / "skill-workflow-refiner"
            central_refiner.mkdir(parents=True)
            (central_refiner / "SKILL.md").write_text("# Refiner\n", encoding="utf-8")
            for profile in ("builder", "work"):
                profile_root = root / "profiles" / profile
                (profile_root / "skills" / "autonomous-ai-agents").mkdir(parents=True)
                (profile_root / "config.yaml").write_text(BASE_CONFIG, encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root)],
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("self-improvement-governor installed", proc.stdout)
            plugin_init = root / "plugins" / "self-improvement-governor" / "__init__.py"
            plugin_text = plugin_init.read_text(encoding="utf-8")
            self.assertIn("skill-optimizer", plugin_text)
            self.assertIn("skill-workflow-refiner", plugin_text)
            self.assertNotIn("Be ACTIVE", plugin_text)

            base_config = (root / "config.yaml").read_text(encoding="utf-8")
            self.assertIn("creation_nudge_interval: 30", base_config)
            self.assertIn("  - self-improvement-governor\n", base_config)

            work_config = (root / "profiles" / "work" / "config.yaml").read_text(encoding="utf-8")
            self.assertIn("creation_nudge_interval: 60", work_config)
            builder_config = (root / "profiles" / "builder" / "config.yaml").read_text(
                encoding="utf-8"
            )
            self.assertIn("creation_nudge_interval: 30", builder_config)

            for profile in ("builder", "work"):
                link = (
                    root
                    / "profiles"
                    / profile
                    / "skills"
                    / "autonomous-ai-agents"
                    / "skill-workflow-refiner"
                )
                self.assertTrue(link.is_symlink())
                self.assertEqual(
                    link.readlink(),
                    Path("/opt/data/skills/autonomous-ai-agents/skill-workflow-refiner"),
                )

            second = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root)],
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertIn("configs_changed: 0", second.stdout)
            self.assertIn("symlinks_created: 0", second.stdout)


if __name__ == "__main__":
    unittest.main()
