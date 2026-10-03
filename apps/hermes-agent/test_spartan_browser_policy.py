import importlib.util
import ast
import codecs
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys
import tempfile
import unittest
import types
import re
from unittest.mock import patch

import yaml
from dotenv import dotenv_values


HERE = Path(__file__).parent
SOURCE = os.environ.get('HERMES_UPSTREAM_SOURCE')


def load_policy():
    spec = importlib.util.spec_from_file_location('spartan_browser_policy_test', HERE / 'spartan_browser_policy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(SOURCE, 'set HERMES_UPSTREAM_SOURCE for native dotenv contract tests')
class BrowserPolicyTests(unittest.TestCase):
    def setUp(self):
        self.module = load_policy()
        # Execute the real installed tokenizer without importing the rest of
        # Hermes or hydrating any profile secrets.
        tree = ast.parse((Path(SOURCE) / 'agent/secret_scope.py').read_text())
        names = {'_decode_env_bytes', '_strip_inline_comment', '_parse_env_value', '_parse_env_text'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual(len(nodes), len(names))
        self.native = types.ModuleType('agent.secret_scope')
        ns = self.native.__dict__
        ns.update({'codecs': codecs, 're': re})
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), '<native-Hermes-tokenizer>', 'exec'), ns)
        modules = patch.dict(sys.modules, {'agent': types.ModuleType('agent'), 'agent.secret_scope': self.native})
        modules.start()
        self.addCleanup(modules.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.destination = self.root / 'managed'
        self.env = {'CAMOFOX_URL': 'http://camofox:9377', 'CAMOFOX_API_KEY': 'synthetic-key',
                    'CAMOFOX_ACCESS_KEY': 'synthetic-key'}
        self.owner, self.gid = os.geteuid(), os.getgid()

    def publish(self, env=None):
        files = self.module.prepare_documents(self.env if env is None else env, self.source)
        self.module.publish_policy(files, self.destination, self.owner, self.gid)
        return files

    def test_managed_endpoint_and_auth_override_profiles_without_sharing_identity(self):
        (self.source / 'config.yaml').write_text('model:\n  default: test/model\nbrowser:\n  camofox:\n    managed_persistence: true\n')
        (self.source / '.env').write_text('OTHER_API_KEY="synthetic-other"\n')
        self.env.update({'CAMOFOX_USER_ID': 'must-not-copy', 'CAMOFOX_SESSION_KEY': 'must-not-copy'})
        self.publish()
        values = dotenv_values(self.destination / '.env')
        self.assertEqual(values['CAMOFOX_API_KEY'], 'synthetic-key')
        self.assertEqual(values['OTHER_API_KEY'], 'synthetic-other')
        self.assertNotIn('CAMOFOX_USER_ID', values)
        self.assertNotIn('CAMOFOX_SESSION_KEY', values)
        config = yaml.safe_load((self.destination / 'config.yaml').read_text())
        self.assertEqual(config['model']['default'], 'test/model')
        self.assertEqual(config['browser']['cloud_provider'], 'camofox')
        self.assertEqual(config['browser']['backend'], 'off')
        self.assertTrue(config['browser']['camofox']['managed_persistence'])
        for name in ('.env', 'config.yaml', 'mode.json'):
            self.assertEqual(stat.S_IMODE((self.destination / name).stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o750)

    def test_unmanaged_mode_preserves_existing_configuration(self):
        (self.source / 'config.yaml').write_text('browser:\n  cloud_provider: local\n')
        self.publish({})
        self.assertEqual(self.module.validate_policy(self.destination, self.owner, self.gid)['mode'], 'unmanaged')
        self.assertEqual(yaml.safe_load((self.destination / 'config.yaml').read_text())['browser']['cloud_provider'], 'local')
        self.assertEqual(dict(dotenv_values(self.destination / '.env')), {})

    def test_invalid_inputs_fail_before_any_publication(self):
        for values in [dict(self.env, CAMOFOX_API_KEY=''), dict(self.env, CAMOFOX_URL='http://host:99999'),
                       dict(self.env, CAMOFOX_URL='http://user:password@host'),
                       dict(self.env, CAMOFOX_URL=' http://host'), dict(self.env, CAMOFOX_URL='http://host/path'),
                       dict(self.env, CAMOFOX_API_KEY='key\r\nInjected=value'),
                       dict(self.env, CAMOFOX_ACCESS_KEY='different')]:
            with self.subTest(values=list(values)):
                with self.assertRaises(ValueError):
                    self.publish(values)
                self.assertFalse(self.destination.exists())

    def test_invalid_managed_sources_are_rejected(self):
        for text in ['[', '[]', 'false', 'browser: []']:
            (self.source / 'config.yaml').write_text(text)
            with self.subTest(text=text), self.assertRaises((ValueError, yaml.YAMLError)):
                self.publish()
            self.assertFalse(self.destination.exists())
        (self.source / 'config.yaml').unlink()
        (self.source / '.env').write_text('TOKEN="unterminated\n')
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse(self.destination.exists())

    def test_repeat_boot_is_idempotent_and_changed_inputs_leave_previous_policy_intact(self):
        files = self.publish()
        inodes = {name: (self.destination / name).stat().st_ino for name in files}
        self.publish()
        self.assertEqual(inodes, {name: (self.destination / name).stat().st_ino for name in files})
        with self.assertRaises(ValueError):
            self.publish(dict(self.env, CAMOFOX_API_KEY='rotated', CAMOFOX_ACCESS_KEY='rotated'))
        self.assertEqual(files, {name: (self.destination / name).read_bytes() for name in files})

    def test_file_tampering_and_permission_changes_fail_closed(self):
        self.publish()
        path = self.destination / 'config.yaml'
        path.write_text('browser:\n  cloud_provider: local\n')
        with self.assertRaises(RuntimeError):
            self.module.validate_policy(self.destination, self.owner, self.gid)
        path.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError, 'permissions'):
            self.module.validate_policy(self.destination, self.owner, self.gid)

    def test_policy_rejects_boolean_schema_and_unknown_mode(self):
        self.publish()
        path = self.destination / 'mode.json'
        document = json.loads(path.read_text())
        for key, value in [('schema', True), ('mode', 'chromium')]:
            altered = {**document, key: value}
            path.write_text(json.dumps(altered))
            with self.assertRaises(RuntimeError):
                self.module.validate_policy(self.destination, self.owner, self.gid)

    def test_runtime_missing_policy_or_wrong_directory_raises_instead_of_fallback(self):
        with patch.object(self.module, 'MANAGED_DIR', self.destination):
            with patch.dict(os.environ, {'SPARTAN_BROWSER_POLICY_ENABLED': '1',
                                         'HERMES_MANAGED_DIR': str(self.destination),
                                         'SPARTAN_HERMES_RUN_GID': '1000'}):
                with self.assertRaises(RuntimeError):
                    self.module.camofox_policy_enabled()
            with patch.dict(os.environ, {'SPARTAN_BROWSER_POLICY_ENABLED': '1', 'HERMES_MANAGED_DIR': '/wrong'}):
                with self.assertRaises(RuntimeError):
                    self.module.camofox_policy_enabled()

    def test_existing_policy_cannot_be_disabled_by_empty_feature_flag(self):
        self.publish()
        with patch.object(self.module, 'MANAGED_DIR', self.destination), patch.dict(os.environ, {'SPARTAN_BROWSER_POLICY_ENABLED': ''}):
            with self.assertRaises(RuntimeError):
                self.module.camofox_policy_enabled()

    def test_symlink_managed_sources_are_rejected(self):
        real = self.root / 'other.yaml'
        real.write_text('{}')
        (self.source / 'config.yaml').symlink_to(real)
        with self.assertRaises(ValueError):
            self.publish()

    def test_owner_or_gid_changes_are_rejected(self):
        self.publish()
        with self.assertRaises(RuntimeError):
            self.module.validate_policy(self.destination, self.owner + 1, self.gid)
        with self.assertRaises(RuntimeError):
            self.module.validate_policy(self.destination, self.owner, self.gid + 1)

    def test_init_runner_validation_rejects_unrelated_shell_and_executable(self):
        native_script = '/package/admin/s6-overlay/etc/s6-rc/scripts/cont-init'
        shell = self.root / 'shell'
        shell.touch()
        script = self.root / 'cont-init'
        script.touch()
        process = self.root / 'proc/123'
        process.mkdir(parents=True)
        (process / 'exe').symlink_to(shell)
        mapped = {'/proc': self.root / 'proc', '/bin/sh': shell, native_script: script}
        def mapped_path(raw):
            return mapped.get(str(raw), Path(raw))
        with patch.object(self.module, 'Path', side_effect=mapped_path):
            (process / 'cmdline').write_bytes(b'/bin/sh\0-e\0' + native_script.encode() + b'\0')
            self.module.validate_init_runner(123)
            (process / 'cmdline').write_bytes(b'/bin/sh\0-e\0operator.sh\0')
            with self.assertRaises((RuntimeError, OSError)):
                self.module.validate_init_runner(123)
            (process / 'cmdline').write_bytes(b'/bin/sh\0-e\0' + native_script.encode() + b'\0')
            other = self.root / 'other-executable'
            other.touch()
            (process / 'exe').unlink()
            (process / 'exe').symlink_to(other)
            with self.assertRaises(RuntimeError):
                self.module.validate_init_runner(123)

    def test_code_preflight_rejects_inverted_guard_without_manifest(self):
        spec = importlib.util.spec_from_file_location('policy_patcher', HERE / 'patches/patch_camofox_managed_policy.py')
        patcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patcher)
        root = self.root / 'installed'
        (root / 'tools').mkdir(parents=True)
        for filename, (name, _) in patcher.TARGETS.items():
            signature = 'section' if name == 'read_selection' else ''
            source = f'def {name}({signature}):\n    """Selector."""\n    return None\n'
            (root / 'tools' / filename).write_text(patcher.patch_source(filename, source))
        self.module.validate_code(root, None)
        path = root / 'tools/browser_camofox.py'
        path.write_text(path.read_text().replace('if _spartan_camofox_policy():', 'if not _spartan_camofox_policy():'))
        with self.assertRaises(ValueError):
            self.module.validate_code(root, None)

    def test_native_env_values_roundtrip_tabs_backslashes_quotes_and_unicode(self):
        values = {'LITERAL_TAB': 'a\tb', 'BACKSLASH_N': r'a\nb', 'QUOTE': 'a"b',
                  'UNICODE': 'españa', 'REFERENCE': '${PROFILE_VAR}'}
        text = ''.join(k + '="' + v.replace('\\', '\\\\').replace('"', '\\"') + '"\n' for k, v in values.items())
        (self.source / '.env').write_text(text)
        self.publish()
        got = self.native._parse_env_text((self.destination / '.env').read_text())
        for key, value in values.items():
            self.assertEqual(got[key], value)

    def test_native_env_values_preserve_bom_latin1_exports_and_comments(self):
        for raw in [codecs.BOM_UTF8 + b'export TOKEN="value # literal" # comment\n',
                    b'TOKEN="caf\xe9"\n', b'TOKEN=value#literal # comment\n',
                    b'TOKEN=  " spaced "  \n', b'TOKEN=first\nTOKEN=last\n']:
            with self.subTest(raw=raw):
                (self.source / '.env').write_bytes(raw)
                expected = self.native._parse_env_text(self.native._decode_env_bytes(raw))
                documents = self.module.prepare_documents({}, self.source)
                self.assertEqual(self.module.native_env_values(documents['.env']), expected)

    def test_multiline_managed_env_is_rejected_without_reinterpreting_native_values(self):
        (self.source / '.env').write_text('TOKEN="first\nsecond"\n')
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse(self.destination.exists())


class LauncherPolicyImportTests(unittest.TestCase):
    def test_project_only_launcher_import_requires_canonical_helper(self):
        # Upstream's hermes launcher discards ambient PYTHONPATH. Bootstrap-only
        # placement must fail under that path, then the protected root copy works.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / 'hermes'
            root.mkdir()
            (root / 'bootstrap').mkdir()
            shutil.copy2(HERE / 'spartan_browser_policy.py', root / 'bootstrap/spartan_browser_policy.py')
            (root / 'selector.py').write_text(
                'def selected():\n'
                ' from spartan_browser_policy import camofox_policy_enabled\n'
                ' return camofox_policy_enabled()\n')
            env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(root)}
            command = [sys.executable, '-P', '-B', '-c', 'from selector import selected; assert selected() is False']
            before = subprocess.run(command, cwd=raw, env=env, capture_output=True)
            self.assertNotEqual(before.returncode, 0)
            self.assertIn(b"No module named 'spartan_browser_policy'", before.stderr)
            shutil.copy2(root / 'bootstrap/spartan_browser_policy.py', root / 'spartan_browser_policy.py')
            after = subprocess.run(command, cwd=raw, env=env, capture_output=True)
            self.assertEqual(after.returncode, 0, after.stderr.decode())

    def test_all_compose_modes_supply_protected_canonical_helper(self):
        repo = HERE.parents[1]
        for path in [repo / 'infra/compose/compose.yml', *[repo / f'infra/compose/tiers/compose.l{i}.yml' for i in range(3)]]:
            with self.subTest(path=path.name):
                service = yaml.safe_load(path.read_text())['services']['hermes']
                mounts = {mount.get('target'): mount for mount in service['volumes'] if isinstance(mount, dict)}
                canonical = mounts['/opt/hermes/spartan_browser_policy.py']
                self.assertTrue(canonical['read_only'])
                self.assertFalse(canonical['bind']['create_host_path'])
                self.assertEqual(canonical['source'], mounts['/opt/hermes/bootstrap/spartan_browser_policy.py']['source'])
        self.assertIn('COPY --chown=0:0 spartan_browser_policy.py /opt/hermes/spartan_browser_policy.py',
                      (HERE / 'Dockerfile').read_text())

    def test_cold_preflight_uses_launcher_path_and_safe_path_mode(self):
        source = (HERE / 'spartan_browser_policy.py').read_text()
        tree = ast.parse(source)
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'cold_import_preflight')
        source = ast.get_source_segment(source, fn)
        self.assertIn("'PYTHONPATH': '/opt/hermes'", source)
        self.assertNotIn("'/opt/hermes/bootstrap:/opt/hermes'", source)
        self.assertIn("sys.executable, '-P', '-B', '-c', code", source)


if __name__ == '__main__':
    unittest.main()
