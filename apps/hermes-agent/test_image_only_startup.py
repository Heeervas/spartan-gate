import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('image_admission', HERE / 'patches/validate_image_dependencies.py')
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)
spec = importlib.util.spec_from_file_location('image_patcher', HERE / 'patches/patch_stage2_image_dependencies.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class ImageStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'patches').mkdir()
        self.validator = self.root / 'patches/validate_image_dependencies.py'
        self.validator.write_text('# protected test validator\n')
        self.stamp = {'distribution': 'docker', 'updateMechanism': 'external'}
        self.selection = self.root / '.venv'
        modules = {
            'hermes_cli.steward': SimpleNamespace(read_install_stamp=lambda _: self.stamp),
            'pm.environments': SimpleNamespace(selected_venv=lambda _: self.selection),
        }
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, modules).start()
        patch.object(admission, '__file__', str(self.validator)).start()
        patch.object(sys, 'prefix', str(self.selection)).start()
        patch.object(admission, '_protected_validator').start()
        patch.dict(os.environ, {'SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY': '1'}).start()

    def test_only_successful_isolated_admission_selects_image_mode(self):
        with patch.object(admission.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(admission.image_only_startup(self.root))
        args = run.call_args.args[0]
        self.assertEqual(args, [str(self.selection / 'bin/python'), '-I', '-B', str(self.validator)])
        self.assertEqual(run.call_args.kwargs['cwd'], self.root)
        self.assertEqual(run.call_args.kwargs['timeout'], 90)
        self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_disabled_mode_never_probes_or_changes_native_behavior(self):
        with patch.dict(os.environ, {'SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY': '0'}), patch.object(admission.subprocess, 'run') as run:
            self.assertFalse(admission.image_only_startup(self.root))
        run.assert_not_called()

    def test_false_authority_and_foreign_interpreters_fail_closed(self):
        for stamp in ({}, {'distribution': 'docker', 'updateMechanism': 'self'},
                      {'distribution': 'source', 'updateMechanism': 'external'}):
            self.stamp = stamp
            with self.assertRaises(SystemExit) as result:
                admission.image_only_startup(self.root)
            self.assertNotEqual(result.exception.code, 0)
        self.stamp = {'distribution': 'docker', 'updateMechanism': 'external'}
        self.selection = self.root / 'foreign-generation'
        with self.assertRaises(SystemExit):
            admission.image_only_startup(self.root)
        self.selection = self.root / '.venv'
        with patch.object(sys, 'prefix', '/foreign-interpreter'), self.assertRaises(SystemExit):
            admission.image_only_startup(self.root)
        (self.root / '.git').mkdir()
        with self.assertRaises(SystemExit):
            admission.image_only_startup(self.root)

    def test_failure_timeout_and_malformed_mode_cannot_be_caught_as_exception(self):
        for outcome in (SimpleNamespace(returncode=1), subprocess.TimeoutExpired('python', 90), OSError('private detail')):
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with patch.object(admission.subprocess, 'run', **kwargs):
                try:
                    try:
                        admission.image_only_startup(self.root)
                    except Exception:
                        self.fail('native callers would swallow this failure')
                except SystemExit as result:
                    self.assertNotEqual(result.code, 0)
                else:
                    self.fail('startup admitted invalid dependencies')
        with patch.dict(os.environ, {'SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY': 'yes'}), self.assertRaises(SystemExit):
            admission.image_only_startup(self.root)

    def test_runtime_patch_preserves_tool_diagnostics_and_is_idempotent(self):
        source = 'def check_runtime(project_root):\n    problems = pm.activate()\n    return problems\n'
        transformed = patcher.patch_runtime_source(source)
        self.assertEqual(transformed, patcher.patch_runtime_source(transformed))
        for image_mode in (False, True):
            activate = Mock(return_value=['git: not installed or outdated'])
            self.validator.write_text(f'def image_only_startup(root):\n    return {image_mode}\n')
            namespace = {'pm': SimpleNamespace(activate=activate), 'Path': Path, 'os': os}
            exec(transformed, namespace)
            with patch.dict(os.environ, {'SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY': '1' if image_mode else '0'}):
                self.assertEqual(namespace['check_runtime'](self.root), ['git: not installed or outdated'])
            self.assertEqual(activate.call_args.kwargs, {'allow_incomplete': True} if image_mode else {})
        with self.assertRaises(ValueError):
            patcher.patch_runtime_source(source.replace('pm.activate()', 'pm.changed()'))

    def test_runtime_patch_rejects_missing_broken_or_failing_helper(self):
        source = 'def check_runtime(project_root):\n    problems = pm.activate()\n    return problems\n'
        for value in (None, 'invalid Python !', 'raise RuntimeError("private detail")'):
            if value is None:
                self.validator.unlink(missing_ok=True)
            else:
                self.validator.write_text(value)
            namespace = {'pm': SimpleNamespace(activate=Mock()), 'Path': Path, 'os': os}
            exec(patcher.patch_runtime_source(source), namespace)
            with self.assertRaises(SystemExit) as result:
                try:
                    namespace['check_runtime'](self.root)
                except Exception:
                    self.fail('native callers swallow admission errors')
            self.assertEqual(result.exception.code, 78)
            namespace['pm'].activate.assert_not_called()


class ProtectedValidatorTests(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, 'actual authority boundary requires container root')
    def test_actual_root_owned_and_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'patches').mkdir()
            validator = root / 'patches/validate_image_dependencies.py'
            validator.write_text('# fixture\n')
            admission._protected_validator(root, validator)
            validator.chmod(0o666)
            with self.assertRaises(RuntimeError):
                admission._protected_validator(root, validator)
            validator.chmod(0o644)
            os.chown(validator, 65534, 65534)
            with self.assertRaises(RuntimeError):
                admission._protected_validator(root, validator)
            os.chown(validator, 0, 0)
            (root / 'patches').chmod(0o777)
            with self.assertRaises(RuntimeError):
                admission._protected_validator(root, validator)


if __name__ == '__main__':
    unittest.main()
