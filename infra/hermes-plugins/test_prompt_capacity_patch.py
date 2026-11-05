"""Gate-owned prompt-capacity patch proof using explicit pristine Hermes input.

General shared-masterv2 plugin/native assembly tests live with that independent
plugin. This gate retains the image patch's atomic preflight and idempotence.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest


PATCH_FILE = Path(__file__).resolve().parents[2] / "apps/hermes-agent/patches/patch_shared_master_prompt_capacity.py"


class CapacityPatchTests(unittest.TestCase):
    def test_idempotent_and_preflight_before_any_write(self):
        source = Path(os.environ["HERMES_UPSTREAM_SOURCE"])
        spec = importlib.util.spec_from_file_location("capacity", PATCH_FILE)
        capacity = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(capacity)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in capacity.EDITS:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, target)
            original = {p: (root / p).read_bytes() for p in capacity.EDITS}
            broken = root / "agent/system_prompt.py"
            broken.write_text("# unsupported source\n")
            with self.assertRaisesRegex(ValueError, "anchor"):
                capacity.patch_tree(root)
            self.assertEqual((root / "hermes_cli/plugins_dispatch.py").read_bytes(),
                             original["hermes_cli/plugins_dispatch.py"])
            broken.write_bytes(original["agent/system_prompt.py"])
            capacity.patch_tree(root)
            first = {p: (root / p).read_bytes() for p in capacity.EDITS}
            capacity.patch_tree(root)
            self.assertEqual(first, {p: (root / p).read_bytes() for p in capacity.EDITS})
            self.assertNotEqual(first, original)


if __name__ == "__main__":
    unittest.main()
