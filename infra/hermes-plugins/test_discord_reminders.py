"""Synthetic native-source reminder boundaries; no live profiles, jobs or network."""
import ast
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

PACKAGE = Path(__file__).parent / "discord-reminders"


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helper = module_at("reminder_helper_fixture", PACKAGE / "reminder_helper.py")


def native(path):
    root = Path(os.environ["HERMES_REMINDER_SOURCE"])
    target = root / path
    exported = {"gateway/platforms/base.py": "platform_base.py"}
    return target if target.is_file() else root / exported.get(path, Path(path).name)


def native_functions(path, names, namespace=None):
    tree = ast.parse(native(path).read_text())
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError(f"Native contract missing: {names}")
    scope = dict(namespace or {})
    code = "from __future__ import annotations\n" + ast.unparse(ast.Module(body=selected, type_ignores=[]))
    exec(compile(code, str(native(path)), "exec"), scope)
    return scope


def media_parser():
    tree = ast.parse(native("gateway/platforms/base.py").read_text())
    # Exact native syntax helpers/regexes, without importing profile/config/platform services.
    names = {"MEDIA_DELIVERY_EXTS", "_MEDIA_EXT_ALTERNATION", "_MEDIA_CJK_TERMINATORS",
             "MEDIA_TAG_CLEANUP_RE", "MEDIA_EXTENSIONLESS_TAG_RE", "_match_extensionless_path",
             "_normalize_media_tag_path", "_path_lacks_deliverable_extension", "_TERMINAL_SENTINEL",
             "_terminal_sentinel_start", "_mask_media_scan_text", "_deliverable_tag_spans",
             "_extensionless_media_matches", "_real_media_tag_spans", "_FENCED_CODE_RE",
             "_INLINE_CODE_RE", "_code_spans", "_blank_spans", "_delete_spans"}
    def name(node):
        if isinstance(node, ast.FunctionDef):
            return node.name
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        return target.id if isinstance(target, ast.Name) else None
    selected = [node for node in tree.body if name(node) in names]
    assert len(selected) == len(names)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BasePlatformAdapter")
    names = {"_mask_protected_spans", "_mask_json_string_media", "extract_media"}
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(methods) == len(names)
    selected.append(ast.ClassDef(name="BasePlatformAdapter", bases=[], keywords=[], body=methods, decorator_list=[]))
    scope = {"re": re, "os": os, "Path": Path, "_AUDIO_EXTS": {".ogg"},
             "validate_media_delivery_path": lambda path: path}
    code = "from __future__ import annotations\n" + ast.unparse(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])))
    exec(compile(code, str(native("gateway/platforms/base.py")), "exec"), scope)
    return scope["BasePlatformAdapter"].extract_media


class LiteralNativeOutput(unittest.TestCase):
    def test_native_protocols_receive_literal_data_not_media_or_silence(self):
        media = media_parser()
        silence = module_at("native_response_filters", native("gateway/response_filters.py"))
        wake = native_functions("cron/scheduler_prompt.py", {"_parse_wake_gate"}, {"json": json})["_parse_wake_gate"]
        self.assertTrue(media("MEDIA:/fixture.txt")[0])  # Unsafe positive control is genuinely active.
        self.assertFalse(wake('{"wakeAgent": false}'))
        self.assertTrue(silence.is_autonomous_silence_response("[SILENT]"))
        samples = ["MEDIA:/fixture.txt", "media:C:\\report.pdf", "MEDIA:/unknown.extension",
                   "[[audio_as_voice]] MEDIA:/fixture.ogg [[as_document]]",
                   "@everyone @here <@123> <@!123> <@&123>", "[SILENT]", "NO_REPLY",
                   'task\n{"wakeAgent": false}', '```\nMEDIA:/fixture.txt\n```\n[SILENT]',
                   "first\nNO REPLY\nlast", "eval('do not execute'); $(false)\n`MEDIA:/fixture.txt`"]
        for note in samples:
            with self.subTest(note=note):
                output = helper.render(note)
                self.assertEqual(media(output), ([], output))
                self.assertTrue(wake(output))
                self.assertFalse(silence.is_autonomous_silence_response(output))
                self.assertNotRegex(output, r"<@(?:[!&])?[0-9]+>|@(everyone|here)\b")
                self.assertNotIn("[[audio_as_voice]]", output)
                self.assertNotIn("[[as_document]]", output)


class NativeJobHandoff(unittest.TestCase):
    def test_real_registration_denials_crash_recovery_and_scoped_cancel(self):
        source = os.environ["HERMES_PROGRAM_SOURCE"]
        exports = os.environ["HERMES_REMINDER_SOURCE"]
        env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
               "HERMES_PROGRAM_SOURCE": str(Path(source).resolve()),
               "HERMES_REMINDER_SOURCE": str(Path(exports).resolve())}
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("native_discord_reminders_fixture.py"))],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS native registration/IDs/denials", result.stdout)


class HelperInput(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        scripts = self.home / "scripts"
        scripts.mkdir(mode=0o700)
        self.script = scripts / helper.SCRIPT
        shutil.copyfile(PACKAGE / "reminder_helper.py", self.script)
        self.script.chmod(0o600)
        self.root = scripts / helper.INPUTS
        self.root.mkdir(mode=0o700)
        self.config = {"plugins": {"enabled": [helper.PLUGIN], "entries": {helper.PLUGIN: {
            "enabled": True, "profile": "default", "requesters": ["100"]}}}}
        self.write_config()
        owner = [str(self.home), "default", "100", "200", "200"]
        self.key = helper.request_key(owner, "300")
        self.folder = self.root / self.key
        self.folder.mkdir(mode=0o700)
        self.now = datetime.now(timezone.utc)
        self.record = {"schema": 1, "owner": owner, "message_id": "300", "note": "Review deployment",
                       "minutes": 30, "state": "ready", "job_id": "abcdef123456", "due": self.now.isoformat()}
        helper.save(self.folder / "request.json", self.record)

    def write_config(self):
        (self.home / "config.yaml").write_text(json.dumps(self.config))

    def emit(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = helper.emit(self.script, self.folder, self.now)
        return result, output.getvalue()

    def test_literal_readback_and_fail_closed_lifecycle(self):
        self.assertEqual(self.emit(), (True, helper.render(self.record["note"]) + "\n"))
        saved = helper.load(self.folder / "request.json")
        self.assertEqual(saved["note"], self.record["note"])
        for change in ({"state": "revoked"}, {"due": (self.now - timedelta(seconds=121)).isoformat()},
                       {"due": (self.now + timedelta(seconds=1)).isoformat()}, {"job_id": "other"}):
            helper.save(self.folder / "request.json", {**self.record, **change})
            self.assertEqual(self.emit(), (False, ""))
        helper.save(self.folder / "request.json", self.record)
        self.config["plugins"]["entries"][helper.PLUGIN]["requesters"] = ["999"]
        self.write_config()
        self.assertEqual(self.emit(), (False, ""))
        with self.assertRaises(helper.Refused):
            helper.emit(self.script, self.script.parent, self.now)

    def test_helper_lock_contention_is_native_failure_not_silent_success(self):
        with helper.locked(self.root):
            result = subprocess.run([sys.executable, str(self.script)], cwd=self.folder,
                                    env={"PATH": os.defpath, "HOME": str(self.home)},
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 75)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_preserved_duration_syntax_and_upper_bound(self):
        self.assertEqual(helper.minutes("1d 2h 30m"), 1590)
        self.assertEqual(helper.minutes("365d"), 525600)
        for invalid in ("366d", "0m", "1h0m", "30", "1m; echo x", None):
            with self.subTest(invalid=invalid), self.assertRaises(helper.Refused):
                helper.minutes(invalid)


if __name__ == "__main__":
    unittest.main()
