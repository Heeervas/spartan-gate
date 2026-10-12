import importlib.util
import contextlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("hermes-env.py")


def load_module():
    spec = importlib.util.spec_from_file_location("hermes_env", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HermesEnvUpdateTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o700)
        self.path = self.directory / "shared.env"
        self.original = "# retained administrator settings\nHTTP_PROXY=http://proxy:8888\nTOOL_API_KEY=\"old\"\nKEEP=unchanged\n"
        self.path.write_text(self.original, encoding="utf-8")
        self.path.chmod(0o600)

    def validator(self, key, text, secret):
        return secret is None or f"{key}={self.helper.encode_secret(secret)}" in text

    def snapshot(self):
        return {path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                for path in self.directory.iterdir() if path.is_file()}

    def test_update_preserves_other_settings_and_private_original_backup(self):
        validate = mock.Mock(side_effect=self.validator)
        self.assertTrue(self.helper.update_secret(self.path, "TOOL_API_KEY", "new-secret", validate))
        result = self.path.read_text()
        self.assertIn("HTTP_PROXY=http://proxy:8888\n", result)
        self.assertIn("# retained administrator settings\n", result)
        self.assertIn("KEEP=unchanged\n", result)
        self.assertNotIn("'old'", result)
        backups = [path for path in self.directory.iterdir() if path != self.path]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.original)
        for path in (self.path, backups[0]):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(validate.call_count, 3)

    def test_native_rejection_finishes_preflight_before_any_write(self):
        before = self.snapshot()
        validate = mock.Mock(side_effect=ValueError("native policy rejected"))
        with self.assertRaises(ValueError):
            self.helper.update_secret(self.path, "TOOL_API_KEY", "new-secret", validate)
        self.assertEqual(self.snapshot(), before)

    def test_roundtrip_mismatch_or_late_native_error_does_not_write(self):
        for responses in ([True, False], [True, True, ValueError("native unavailable")]):
            with self.subTest(responses=responses):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.helper.update_secret(self.path, "TOOL_API_KEY", "new-secret",
                                              mock.Mock(side_effect=responses))
                self.assertEqual(self.snapshot(), before)

    def test_invalid_values_leave_original_and_directory_unchanged(self):
        before = self.snapshot()
        for value in ("", "line\nbreak", "carriage\rreturn", "nul\x00byte"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    self.helper.update_secret(self.path, "TOOL_API_KEY", value, mock.Mock())
                self.assertEqual(self.snapshot(), before)

    def test_symlink_target_is_rejected_without_touching_referent(self):
        self.path.unlink()
        actual = self.directory / "actual.env"
        actual.write_text(self.original)
        actual.chmod(0o600)
        self.path.symlink_to(actual)
        with self.assertRaises(ValueError):
            self.helper.update_secret(self.path, "TOOL_API_KEY", "new-secret", self.validator)
        self.assertEqual(actual.read_text(), self.original)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()), ["actual.env", "shared.env"])

    def test_unchanged_value_does_not_replace_file_or_make_backup(self):
        inode = self.path.stat().st_ino
        before = self.snapshot()
        changed = self.helper.update_secret(self.path, "TOOL_API_KEY", "old", self.validator)
        self.assertFalse(changed)
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(self.snapshot(), before)

    def test_atomic_replace_failure_keeps_original_and_cleans_temporary(self):
        with mock.patch.object(self.helper.os, "replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaises(OSError):
                self.helper.update_secret(self.path, "TOOL_API_KEY", "new-secret", self.validator)
        self.assertEqual(self.path.read_text(), self.original)
        self.assertFalse(any(path.name.startswith(".") for path in self.directory.iterdir()))


class HermesEnvKeyTests(unittest.TestCase):
    def test_native_validation_pipes_secret_without_putting_it_in_arguments(self):
        helper = load_module()
        config = {"services": {"hermes": {"environment": {
            "SPARTAN_HERMES_RUN_UID": "1000", "SPARTAN_HERMES_RUN_GID": "1000"}}}}
        calls = []
        def compose(*args, **kwargs):
            calls.append((args, kwargs))
            return json.dumps(config) if args[0] == "config" else "ok\n"
        secret = "synthetic-private-secret"
        with mock.patch.object(helper, "compose", side_effect=compose):
            self.assertTrue(helper.native_check("TOOL_API_KEY", "TOOL_API_KEY=value", secret))
        args, kwargs = calls[-1]
        self.assertEqual(args[:7], ("exec", "-T", "--user", "1000:1000", "-w", "/opt/hermes", "hermes"))
        self.assertEqual(args[7:10], ("/opt/hermes/.venv/bin/python", "-I", "-c"))
        native_program = args[10]
        self.assertIn("os.environ.clear()", native_program)
        self.assertIn("HOME=isolated", native_program)
        self.assertIn("HERMES_HOME=isolated", native_program)
        self.assertNotIn(secret, " ".join(args))
        self.assertEqual(json.loads(kwargs["input_text"])["secret"], secret)

    def test_rejects_structural_keys_and_existing_service_environment(self):
        helper = load_module()
        for key in ("bad-key", "bad=value", "", "PATH", "PYTHONPATH",
                    "HERMES_TELEGRAM_BOT_TOKEN", "CAMOFOX_URL", "HTTP_PROXY"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    helper.validate_key(key, {"HTTP_PROXY": "http://proxy:8888"})
        helper.validate_key("TOOL_API_KEY", {})


class HermesEnvSourceTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_module()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "managed"
        self.directory.mkdir(mode=0o700)
        self.path = self.directory / "shared.env"
        self.path.write_text("KEEP=value\n")
        self.path.chmod(0o600)

    def config(self, volumes=None):
        return {"services": {"hermes": {"environment": {}, "volumes": volumes if volumes is not None else [
            {"type": "bind", "source": str(self.path), "target": "/etc/hermes/.env", "read_only": True}]}}}

    def test_discovers_only_existing_mounted_source(self):
        self.assertEqual(self.helper.discover_source(self.config(), self.root), self.path)

    def test_missing_or_ambiguous_mount_fails_without_writes(self):
        config = self.config()
        volume = config["services"]["hermes"]["volumes"][0]
        for volumes in ([], [volume, dict(volume)], [{**volume, "type": "volume"}]):
            with self.subTest(volumes=volumes):
                with self.assertRaises(ValueError):
                    self.helper.discover_source(self.config(volumes), self.root)
                self.assertEqual(self.path.read_text(), "KEEP=value\n")
                self.assertEqual(list(self.directory.iterdir()), [self.path])

    def test_insecure_modes_and_symlink_components_fail_without_repair(self):
        for file_mode, directory_mode in ((0o644, 0o700), (0o600, 0o755)):
            with self.subTest(file_mode=file_mode, directory_mode=directory_mode):
                self.path.chmod(file_mode)
                self.directory.chmod(directory_mode)
                with self.assertRaises(ValueError):
                    self.helper.discover_source(self.config(), self.root)
                self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), file_mode)
                self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), directory_mode)
        self.path.chmod(0o600)
        self.directory.chmod(0o700)
        link = self.root / "linked"
        link.symlink_to(self.directory, target_is_directory=True)
        config = self.config()
        config["services"]["hermes"]["volumes"][0]["source"] = str(link / "shared.env")
        with self.assertRaises(ValueError):
            self.helper.discover_source(config, self.root)
        self.assertEqual(self.path.read_text(), "KEEP=value\n")

    def test_compose_env_file_is_never_promoted_to_shared_source(self):
        self.path.rename(self.directory / "local.env")
        config = self.config()
        config["services"]["hermes"]["volumes"][0]["source"] = str(self.directory / "local.env")
        with self.assertRaises(ValueError):
            self.helper.discover_source(config, self.root)

    def test_literal_value_encoding_and_unsupported_interpolation(self):
        value = "dollar$ quote' double\" slash\\ hash# equals= café"
        encoded = self.helper.encode_secret(value)
        self.assertEqual(encoded, r'''"dollar$ quote' double\" slash\\ hash# equals= café"''')
        for value in ("${TOKEN}", "a\nb", "a\rb", "a\x00b", "x" * 4097):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    self.helper.encode_secret(value)


class HermesEnvFlowTests(unittest.TestCase):
    def setUp(self):
        self.helper = load_module()
        self.path = Path("/unused/shared.env")
        self.config = {"services": {"hermes": {"environment": {}}}}
        self.calls = []

    def run_main(self, arguments, *, menu="", changed=True, reject_native=False, apply_error=False, free=6 * 1024**3, prompt_error=None):
        terminal = mock.Mock()
        terminal.readline.side_effect = menu.splitlines(keepends=True) or [""]
        output = io.StringIO()
        errors = io.StringIO()

        def compose(*args, **kwargs):
            self.calls.append((args, kwargs))
            if args == ("config", "--format", "json"):
                return json.dumps(self.config)
            if args[0] == "up" and apply_error:
                raise ValueError("private-secret-value")
            return ""

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.helper, "compose", side_effect=compose))
            stack.enter_context(mock.patch.object(self.helper, "discover_source", return_value=self.path))
            native = stack.enter_context(mock.patch.object(self.helper, "native_check",
                side_effect=ValueError("private-secret-value") if reject_native else None))
            update = stack.enter_context(mock.patch.object(self.helper, "update_secret", return_value=changed))
            prompt = stack.enter_context(mock.patch.object(self.helper.getpass, "getpass", return_value="private-secret-value", side_effect=prompt_error))
            opened = stack.enter_context(mock.patch("builtins.open", mock.mock_open()))
            opened.return_value.__enter__.return_value = terminal
            stack.enter_context(mock.patch.object(self.helper.shutil, "disk_usage", return_value=mock.Mock(free=free)))
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(contextlib.redirect_stderr(errors))
            result = self.helper.main(arguments)
        self.assertNotIn("private-secret-value", output.getvalue() + errors.getvalue())
        return result, update, prompt, native

    def test_invalid_cli_arguments_never_echo_accidental_secrets_or_run_compose(self):
        secret = "synthetic-accidental-secret"
        for arguments in (["set", "EXAMPLE_API_KEY", secret], [secret]):
            with self.subTest(arguments=arguments):
                output = io.StringIO()
                errors = io.StringIO()
                with mock.patch.object(self.helper, "compose") as compose, \
                     mock.patch.object(self.helper, "update_secret") as update, \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    with self.assertRaises(SystemExit) as raised:
                        self.helper.main(arguments)
                self.assertEqual(raised.exception.code, 2)
                self.assertNotIn(secret, output.getvalue() + errors.getvalue())
                compose.assert_not_called()
                update.assert_not_called()

    def test_no_apply_saves_without_any_container_action(self):
        result, update, prompt, _ = self.run_main(["set", "TOOL_API_KEY", "--no-apply"])
        self.assertEqual(result, 0)
        update.assert_called_once_with(self.path, "TOOL_API_KEY", "private-secret-value")
        prompt.assert_called_once()
        self.assertEqual([args for args, _ in self.calls], [("config", "--format", "json")])

    def test_apply_revalidates_and_recreates_only_hermes_without_building(self):
        result, _, _, _ = self.run_main(["set", "TOOL_API_KEY", "--apply"])
        self.assertEqual(result, 0)
        self.assertEqual([args for args, _ in self.calls][-2:], [
            ("config", "--quiet"),
            ("up", "-d", "--no-build", "--no-deps", "--force-recreate", "hermes")])

    def test_menu_choices_save_or_apply(self):
        for menu, applies in (("1\n", True), ("2\n", False), ("\n", False)):
            with self.subTest(menu=menu):
                self.calls.clear()
                result, _, _, _ = self.run_main(["set", "TOOL_API_KEY"], menu=menu)
                self.assertEqual(result, 0)
                self.assertEqual(any(args[0] == "up" for args, _ in self.calls), applies)

    def test_unchanged_never_recreates_even_with_apply(self):
        result, _, _, _ = self.run_main(["set", "TOOL_API_KEY", "--apply"], changed=False)
        self.assertEqual(result, 0)
        self.assertFalse(any(args[0] == "up" for args, _ in self.calls))

    def test_native_denial_never_prompts_or_writes(self):
        result, update, prompt, _ = self.run_main(["set", "OPENAI_API_KEY", "--apply"], reject_native=True)
        self.assertEqual(result, 1)
        prompt.assert_not_called()
        update.assert_not_called()
        self.assertFalse(any(args[0] == "up" for args, _ in self.calls))

    def test_unavailable_hidden_prompt_does_not_save_or_apply(self):
        warning = self.helper.getpass.GetPassWarning("private-secret-value")
        result, update, _, _ = self.run_main(["set", "TOOL_API_KEY", "--apply"], prompt_error=warning)
        self.assertEqual(result, 1)
        update.assert_not_called()
        self.assertFalse(any(args[0] == "up" for args, _ in self.calls))

    def test_low_disk_keeps_save_without_recreation(self):
        result, update, _, _ = self.run_main(["set", "TOOL_API_KEY", "--apply"], free=1024)
        self.assertEqual(result, 1)
        update.assert_called_once()
        self.assertFalse(any(args[0] == "up" for args, _ in self.calls))

    def test_apply_failure_is_redacted_and_retains_completed_save(self):
        result, update, _, _ = self.run_main(["set", "TOOL_API_KEY", "--apply"], apply_error=True)
        self.assertEqual(result, 1)
        update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
