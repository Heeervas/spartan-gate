import importlib.util
import json
import os
import shutil
import venv
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
        source = ('def prepare_launch(project_root, argv):\n' + patcher.EARLY_ANCHOR + '\n    return None\n'
                  'def check_runtime(project_root):\n    problems = pm.activate()\n    return problems\n')
        transformed = patcher.patch_runtime_source(source)
        self.assertEqual(transformed, patcher.patch_runtime_source(transformed))
        for image_mode in (False, True):
            activate = Mock(return_value=['git: not installed or outdated'])
            self.validator.write_text(f'def startup_admission(root, argv):\n    return {image_mode}\n')
            namespace = {'pm': SimpleNamespace(activate=activate), 'Path': Path, 'os': os}
            exec(transformed, namespace)
            with patch.dict(os.environ, {'SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY': '1' if image_mode else '0'}):
                self.assertEqual(namespace['check_runtime'](self.root), ['git: not installed or outdated'])
            self.assertEqual(activate.call_args.kwargs, {'allow_incomplete': True} if image_mode else {})
        with self.assertRaises(ValueError):
            patcher.patch_runtime_source(source.replace('pm.activate()', 'pm.changed()'))

    def test_runtime_patch_rejects_missing_broken_or_failing_helper(self):
        source = ('def prepare_launch(project_root, argv):\n' + patcher.EARLY_ANCHOR + '\n    return None\n'
                  'def check_runtime(project_root):\n    problems = pm.activate()\n    return problems\n')
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


@unittest.skipUnless(os.environ.get('HERMES_UPSTREAM_SOURCE'), 'requires explicit native Hermes program input')
class ManagedNativeStartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'program'
        self.root.mkdir()
        native = Path(os.environ['HERMES_UPSTREAM_SOURCE'])
        for name in ('pm', 'hermes_cli'):
            shutil.copytree(native / name, self.root / name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('hermes_constants.py', 'hermes_constants_scratch.py', 'hermes_bootstrap.py', 'hermes_yaml.py', 'utils.py'):
            shutil.copy2(native / name, self.root / name)
        (self.root / 'hermes_cli/venv_sync.py').write_text(patcher.patch_runtime_source((native / 'hermes_cli/venv_sync.py').read_text()))
        (self.root / 'patches').mkdir()
        validator_source = (HERE / 'patches/validate_image_dependencies.py').read_text()
        # Both isolated probes need the same host-only OS-authority emulation as the loader below.
        (self.root / 'patches/validate_image_dependencies.py').write_text(validator_source.replace(
            "if __name__ == '__main__':", "if __name__ == '__main__':\n    _protected_validator = lambda *args: None"))
        (self.root / '.venv').symlink_to(sys.prefix, target_is_directory=True)
        (self.root / 'install-stamp.json').write_text(json.dumps({'distribution': 'docker', 'updateMechanism': 'external'}))
        (self.root / 'pyproject.toml').write_text('[project]\nname="hermes-agent"\nversion="1"\nrequires-python=">=3.11"\ndependencies=["admission-fixture>=1"]\n[project.optional-dependencies]\nweb=["packaging>=1"]\n')
        import hashlib
        key = hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:16]
        self.home = self.base / 'home'
        self.state = self.home / 'installs' / key
        self.selected = self.state / 'environments' / 'fixture' / 'venv'
        venv.EnvBuilder(with_pip=False).create(self.selected)
        self.site = self.selected / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
        # Reuse already provisioned program dependencies, never install or fetch.
        import sysconfig
        (self.site / 'fixture-inputs.pth').write_text(sysconfig.get_paths()['purelib'] + '\n')
        (self.site / 'admission_fixture.py').write_text('VALUE = "selected"\n')
        self.dist = self.site / 'admission_fixture-1.0.dist-info'
        self.dist.mkdir()
        (self.dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: admission-fixture\nVersion: 1.0\n')
        self.lock = self.selected.parent / 'workspace/uv.lock'
        self.lock.parent.mkdir()
        self.lock.write_text('version=1\npackage=[]\n')
        self.facts = self.state / 'facts.json'
        self.document = {'schema': 1, 'packages': {'venv': {'stamp': 'deliberately-stale', 'extras': ['web'],
                         'environment': str(self.selected), 'resolved_lock': str(self.lock)}}}
        self.save_facts()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'SPARTAN_'))}
        self.env.update(HOME=str(self.base), HERMES_HOME=str(self.home), PYTHONDONTWRITEBYTECODE='1',
                        SPARTAN_HERMES_MANAGED_DEPENDENCIES_VALIDATE_ONLY='1',
                        SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY='0', PYTHONPATH=str(self.root))
        self.driver = '''
import importlib.machinery, pathlib, sys
# Host fixture emulates only root-controlled protected mounts; no OS-authority claim.
original = importlib.machinery.SourceFileLoader.exec_module
def load(loader, module):
    original(loader, module)
    if loader.path.endswith('/patches/validate_image_dependencies.py'):
        module._protected_validator = lambda *args: None
importlib.machinery.SourceFileLoader.exec_module = load
import hermes_bootstrap
import admission_fixture
print('SELECTED', admission_fixture.__file__)
'''

    def save_facts(self):
        self.facts.write_text(json.dumps(self.document))

    def run_boot(self, argv=(), driver=None):
        return subprocess.run([sys.executable, '-B', '-c', driver or self.driver, *argv],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30)

    def test_stale_selection_native_bootstrap_origin_and_plugin_edits(self):
        before = self.facts.read_bytes(), self.lock.read_bytes()
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.site / 'admission_fixture.py'), result.stdout)
        plugin = self.home / 'plugins' / 'fixture'
        plugin.mkdir(parents=True)
        (self.home / 'config.yaml').write_text('plugins:\n  enabled: [fixture]\n')
        declaration = plugin / 'plugin.yaml'
        declaration.write_text('name: fixture\npip_dependencies: ["admission-fixture>=1"]\n')
        (plugin / '__init__.py').write_text('VALUE=1\n')
        self.assertEqual(self.run_boot().returncode, 0)
        (plugin / '__init__.py').write_text('VALUE=2\n')
        self.assertEqual(self.run_boot().returncode, 0)
        declaration.write_text('name: fixture\npip_dependencies: ["admission-fixture>=99"]\n')
        self.assertNotEqual(self.run_boot().returncode, 0)
        self.assertEqual((self.facts.read_bytes(), self.lock.read_bytes()), before)

    def test_selection_and_pending_state_fail_before_native_recovery(self):
        self.assertEqual(self.run_boot().returncode, 0)
        for path in (self.state / 'publication.json', self.state / '.repair-incomplete',
                     self.root / '.update-incomplete', self.root / '.lazy-refresh-incomplete'):
            path.write_text('pending')
            result = self.run_boot()
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(path.read_text(), 'pending')
            self.assertNotIn('repairing', result.stderr)
            path.unlink()
        for value in (None, '/foreign/venv', 42):
            self.document['packages']['venv']['environment'] = value
            self.save_facts()
            result = self.run_boot()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.state / '.repair-incomplete').exists())
        self.facts.write_text('{malformed')
        self.assertNotEqual(self.run_boot().returncode, 0)
        self.facts.unlink()
        self.assertNotEqual(self.run_boot().returncode, 0)

    def test_core_extra_transitive_python_and_abi_rejections(self):
        self.assertEqual(self.run_boot().returncode, 0)
        self.document['packages']['venv']['extras'] = ['unknown']
        self.save_facts()
        self.assertNotEqual(self.run_boot().returncode, 0)
        self.document['packages']['venv']['extras'] = ['web']
        self.save_facts()
        metadata = self.dist / 'METADATA'
        metadata.write_text(metadata.read_text() + 'Requires-Dist: absent-fixture>=1\n')
        self.assertNotEqual(self.run_boot().returncode, 0)
        metadata.write_text('Metadata-Version: 2.1\nName: admission-fixture\nVersion: 0.1\n')
        self.assertNotEqual(self.run_boot().returncode, 0)
        project = self.root / 'pyproject.toml'
        project.write_text(project.read_text().replace('>=3.11', '>=99'))
        self.assertNotEqual(self.run_boot().returncode, 0)
        probe = subprocess.run([str(self.selected / 'bin/python'), '-I', '-B', str(self.root / 'patches/validate_image_dependencies.py'),
                                '--managed-probe', '["other", 0, 0, "foreign"]'], env=self.env, capture_output=True)
        self.assertNotEqual(probe.returncode, 0)

    def security_override_fixture(self):
        project = self.root / 'pyproject.toml'
        original = project.read_text() + '\ndiscord = ["discord.py[voice]==2.7.1"]\n'
        project.write_text(original)
        self.document['packages']['venv']['extras'].append('discord')
        self.save_facts()
        for name, metadata in {
            'discord_py-2.7.1': 'Name: discord.py\nVersion: 2.7.1\nRequires-Dist: PyNaCl<1.6,>=1.5.0; extra == "voice"\n',
            'pynacl-1.6.2': 'Name: PyNaCl\nVersion: 1.6.2\n',
        }.items():
            directory = self.site / (name + '.dist-info')
            directory.mkdir()
            (directory / 'METADATA').write_text('Metadata-Version: 2.1\n' + metadata)
        return project, original, self.site / 'pynacl-1.6.2.dist-info/METADATA'

    def test_protected_core_override_admits_security_floor_not_raw_upper_bound(self):
        project, original, pynacl = self.security_override_fixture()
        before = self.facts.read_bytes(), self.lock.read_bytes()
        self.assertEqual(self.run_boot().returncode, 78)  # No override retains the upstream upper bound.
        project.write_text(original + '\n[tool.uv]\noverride-dependencies = ["pynacl>=1.6,<1.7"]\n')
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        for version in ('1.5.0', '1.7.0'):
            pynacl.write_text(f'Metadata-Version: 2.1\nName: PyNaCl\nVersion: {version}\n')
            self.assertEqual(self.run_boot().returncode, 78)
        pynacl.write_text('Metadata-Version: 2.1\nName: PyNaCl\nVersion: 1.6.2\n')
        self.assertEqual(self.run_boot().returncode, 0)
        self.dist.rename(self.base / 'missing-core.dist-info')  # Overrides cannot excuse a missing core dependency.
        self.assertEqual(self.run_boot().returncode, 78)
        self.assertEqual((self.facts.read_bytes(), self.lock.read_bytes()), before)

    def test_only_current_core_can_override_and_false_marker_removes_dependency(self):
        project, original, _pynacl = self.security_override_fixture()
        plugin = self.home / 'plugins/override-fixture'
        plugin.mkdir(parents=True)
        (self.home / 'config.yaml').write_text('plugins:\n  enabled: [override-fixture]\n')
        (plugin / 'plugin.yaml').write_text('name: override-fixture\n')
        (plugin / 'pyproject.toml').write_text('[project]\nname="override-fixture"\nversion="1"\ndependencies=[]\n'
                                             '[tool.uv]\noverride-dependencies=["pynacl>=1.6,<1.7"]\n')
        global_config = self.base / 'config/uv/uv.toml'
        global_config.parent.mkdir(parents=True)
        global_config.write_text('override-dependencies=["pynacl>=1.6,<1.7"]\n')
        self.env['XDG_CONFIG_HOME'] = str(self.base / 'config')
        self.assertEqual(self.run_boot().returncode, 78)  # Writable declarations are not authority.
        discord = self.site / 'discord_py-2.7.1.dist-info/METADATA'
        discord.write_text(discord.read_text() + 'Requires-Dist: spacy-curated-transformers>=0.3\n')
        core = original + '\n[tool.uv]\noverride-dependencies = '
        project.write_text(core + '["pynacl>=1.6,<1.7"]\n')
        self.assertEqual(self.run_boot().returncode, 78)  # The absent optional transitive package is required.
        project.write_text(core + '["pynacl>=1.6,<1.7", "spacy-curated-transformers; sys_platform == \'never\'"]\n')
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stderr)
        project.write_text(core + '"pynacl>=1.6,<1.7"\n')
        self.assertEqual(self.run_boot().returncode, 78)

    def test_explicit_pm_and_direct_gateway_fatal_boundary(self):
        self.facts.unlink()
        driver = self.driver[:self.driver.index('import hermes_bootstrap')] + '''
from hermes_cli.venv_sync import prepare_launch
assert prepare_launch(pathlib.Path.cwd(), ['pm', 'repair']) is None
print('MANAGEMENT REACHABLE')
'''
        result = self.run_boot(driver=driver)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Exercise the real gateway's best-effort PM block without assembling network services.
        source = (Path(os.environ['HERMES_UPSTREAM_SOURCE']) / 'gateway/run.py').read_text()
        start = source.index('    try:\n        from hermes_cli.venv_sync import check_runtime')
        end = source.index('    try:', start + 8)
        import textwrap
        block = textwrap.dedent(source[start:end])
        driver = self.driver[:self.driver.index('import hermes_bootstrap')] + '\n' + block
        result = self.run_boot(driver=driver)
        self.assertEqual(result.returncode, 78, result.stderr)


if __name__ == '__main__':
    unittest.main()
