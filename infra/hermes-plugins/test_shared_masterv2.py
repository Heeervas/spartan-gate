"""Focused native assembly proof. Requires explicit Hermes program source/dependencies.

Run in a disposable HOME with HERMES_UPSTREAM_SOURCE set to pinned program input.
SHARED_MASTER_NATIVE_RED=1 leaves the disposable core unpatched for a RED control.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
PATCH_FILE = REPO / "apps/hermes-agent/patches/patch_shared_master_prompt_capacity.py"
PLUGIN_FILE = REPO / "infra/hermes-plugins/shared-masterv2/__init__.py"


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CapacityPatchTests(unittest.TestCase):
    def test_idempotent_and_preflight_before_any_write(self):
        source = Path(os.environ["HERMES_UPSTREAM_SOURCE"])
        capacity = load_file("capacity", PATCH_FILE)
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


class NativeAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = tempfile.TemporaryDirectory()
        root = Path(cls.program.name)
        source = Path(os.environ["HERMES_UPSTREAM_SOURCE"])
        for package in ("agent", "hermes_cli"):
            shutil.copytree(source / package, root / package,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if os.environ.get("SHARED_MASTER_NATIVE_RED") != "1":
            load_file("capacity", PATCH_FILE).patch_tree(root)
        sys.path[:0] = [str(root), str(source)]
        home = root / "home"
        home.mkdir()
        cls.environment = patch.dict(os.environ, {
            "PATH": os.environ.get("PATH", ""), "HOME": str(home),
            "HERMES_HOME": str(home / "hermes"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CACHE_HOME": str(home / "cache"),
        }, clear=True)
        cls.environment.start()
        cls.addClassCleanup(cls.environment.stop)
        cls.network_guard = patch("socket.socket.connect", side_effect=AssertionError("Network forbidden in native source proof"))
        cls.network_guard.start()
        from hermes_cli import plugins
        from agent import system_prompt
        from run_agent import AIAgent
        cls.plugins, cls.system_prompt, cls.agent_type = plugins, system_prompt, AIAgent
        cls.plugin = load_file("shared_masterv2", PLUGIN_FILE)

    @classmethod
    def tearDownClass(cls):
        cls.network_guard.stop()
        cls.program.cleanup()

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name) / "common"
        self.root.mkdir()
        self.workspace = Path(self.scratch.name) / "workspace"
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("NATIVE_WORKSPACE_AGENTS_SENTINEL\n")
        self.master = "MASTER_BEGIN\n" + "x" * (16416 - 25) + "\nMASTER_END!"
        self.assertEqual(len(self.master), 16416)
        (self.root / "MASTER.md").write_text(self.master, encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.root), "TERMINAL_CWD": str(self.workspace),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manager = self.plugins.PluginManager()
        self.manager._discovered = True
        self.manager_patch = patch.object(self.plugins, "_plugin_manager", self.manager)
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        ctx = self.plugins.PluginContext(self.plugins.PluginManifest(
            name="shared-masterv2", key="shared-masterv2", source="user"), self.manager)
        reader_patch = patch.object(self.plugin, "_read_master", wraps=self.plugin._read_master)
        self.reader = reader_patch.start()
        self.addCleanup(reader_patch.stop)
        self.plugin.register(ctx)

    def agent(self, **kwargs):
        return self.agent_type(api_key="synthetic-key", base_url="http://127.0.0.1:1/v1",
                               model="test/model", provider="openai", platform="cli",
                               enabled_toolsets=[], quiet_mode=True, skip_memory=True,
                               skip_background_review=True, **kwargs)

    def test_common_root_complete_once_across_profiles_and_native_agents(self):
        profiles = ("default", "empty", "workstation", "ssr", "ssr-", "ssr_",
                    "ssr-test", "ssr_synthetic_consumer", "work", "evaluador", "reviewer")
        for profile in profiles:
            with self.subTest(profile=profile):
                home = self.root if profile == "default" else self.root / "profiles" / profile
                home.mkdir(parents=True, exist_ok=True)
                if home != self.root:
                    (home / "MASTER.md").write_text("WRONG_PROFILE_MASTER")
                os.environ["HERMES_HOME"] = str(home)
                self.plugins._plugin_managers_by_home.clear()
                self.plugins._plugin_manager = self.manager
                agent = self.agent(session_id="native-" + profile)
                prompt = agent._build_system_prompt()
                self.assertEqual(prompt.count(self.master), 1)
                self.assertIn("NATIVE_WORKSPACE_AGENTS_SENTINEL", prompt)
                self.assertNotIn("WRONG_PROFILE_MASTER", prompt)
                self.system_prompt.restore_plugin_prompt_sections(agent, prompt)
                self.assertEqual(agent._plugin_system_prompt_sections_snapshot[0].content, self.master)
        self.assertEqual(self.reader.call_count, len(profiles))
        self.assertEqual(len(self.manager._system_prompt_sections), 1)
        self.assertFalse(any(self.manager._middleware.values()))
        sections = self.manager.render_system_prompt_sections({})
        framed_chars = len(self.plugins.format_system_prompt_sections(sections))
        self.assertLessEqual(framed_chars, 24000)
        print(f"BUDGET: sections={len(sections)} content=16416 framed={framed_chars} total=24000")

    def test_freeze_disk_restore_then_invalidate_adopts_edit_once(self):
        from agent.conversation_loop import _restore_or_build_system_prompt
        from hermes_state import SessionDB
        db_path = Path(self.scratch.name) / "state.db"
        db = SessionDB(db_path=db_path)
        self.addCleanup(db.close)
        db.ensure_session("restore-master", source="cli", model="test/model")
        agent = self.agent(session_id="restore-master", session_db=db)
        _restore_or_build_system_prompt(agent, None, [])
        original = agent._cached_system_prompt
        self.assertEqual(original.count(self.master), 1)
        profile_line = self.system_prompt._active_profile_line(agent)
        legacy = original.replace("\n\n" + profile_line, "")
        self.assertEqual(self.system_prompt._restore_plugin_prompt_sections(legacy)[0].content, self.master)
        forged = original.replace(profile_line, "Active Hermes profile: forged")
        self.assertEqual(self.system_prompt._restore_plugin_prompt_sections(forged, profile_line), ())
        (self.root / "MASTER.md").write_text("EDITED_MASTER")
        self.assertIn(self.master, agent._build_system_prompt())
        self.assertEqual(self.reader.call_count, 1)
        db.close()
        reopened = SessionDB(db_path=db_path)
        self.addCleanup(reopened.close)
        resumed = self.agent(session_id="restore-master", session_db=reopened)
        _restore_or_build_system_prompt(resumed, None, [{"role": "user", "content": "prior turn"}])
        self.assertEqual(resumed._cached_system_prompt, original)
        self.assertEqual(self.reader.call_count, 1)
        self.assertEqual(resumed._plugin_system_prompt_sections_snapshot[0].content, self.master)
        self.system_prompt.invalidate_system_prompt(resumed)
        rebuilt = resumed._build_system_prompt()
        self.assertEqual(rebuilt.count("EDITED_MASTER"), 1)
        self.assertNotIn(self.master, rebuilt)
        self.assertEqual(self.reader.call_count, 2)

    def test_file_warnings_do_not_expose_content_or_errors(self):
        path = self.root / "MASTER.md"
        for failure in ("missing", "empty", "unreadable", "invalid-utf8"):
            with self.subTest(failure=failure):
                path.unlink(missing_ok=True)
                if failure == "empty":
                    path.write_text(" \n")
                elif failure == "unreadable":
                    path.mkdir()
                elif failure == "invalid-utf8":
                    path.write_bytes(b"\xffPRIVATE_SENTINEL")
                with self.assertLogs(self.plugin.logger, level="WARNING") as logs:
                    self.assertEqual(self.manager.render_system_prompt_sections({}), [])
                self.assertIn("instructions omitted", " ".join(logs.output))
                self.assertNotIn("PRIVATE_SENTINEL", " ".join(logs.output))
                if path.is_dir():
                    path.rmdir()

    def test_ordinary_default_and_native_overflow_remain_bounded(self):
        ctx = self.plugins.PluginContext(self.plugins.PluginManifest(
            name="ordinary", key="ordinary", source="user"), self.manager)
        ctx.register_system_prompt_section("ordinary.rules", "o" * 4001)
        with self.assertLogs("hermes_cli.plugins", level="WARNING"):
            sections = self.manager.render_system_prompt_sections({})
        self.assertEqual([s.id for s in sections], ["shared-masterv2.master"])
        (self.root / "MASTER.md").write_text("s" * 20001)
        with self.assertLogs("hermes_cli.plugins", level="WARNING") as logs:
            self.assertEqual(self.manager.render_system_prompt_sections({}), [])
        self.assertIn("20001 > 20000", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
