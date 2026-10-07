"""Capability denial tests run without Docker, profiles, stores or network access."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


variant = load('variant_test', HERE / 'spartan_browser_variant.py')
patcher = load('variant_patcher', HERE / 'patches/patch_camofox_only_variant.py')


class FakeInstallError(RuntimeError):
    pass


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.marker = self.root / 'mode'
        self.addCleanup(patch.stopall)
        patch.object(variant, 'MARKER_PATH', self.marker).start()
        real_lstat = Path.lstat
        def rooted_lstat(path):
            result = real_lstat(path)
            if path == self.marker:
                fields = list(result)
                fields[4] = 0
                return os.stat_result(fields)
            return result
        patch.object(Path, 'lstat', rooted_lstat).start()
        self.packages = {'chromium': (), 'agent-browser': ('chromium',), 'wrapper': ('agent-browser',), 'ffmpeg': (), 'ripgrep': (), 'python': ()}
        def walk(names):
            result = {}
            def visit(name):
                for dep in self.packages[name]:
                    visit(dep)
                result[name] = types.SimpleNamespace(name=name)
            for name in names:
                visit(name)
            return list(result.values())
        self.registry = types.SimpleNamespace(walk=walk)
        self.pm = types.ModuleType('pm')
        self.pm.__path__ = []
        patch.dict(sys.modules, {'pm': self.pm, 'pm.registry': self.registry,
                   'pm.package': types.SimpleNamespace(InstallError=FakeInstallError),
                   'pm.spartan_browser_variant': variant}).start()

    def enable(self):
        self.marker.write_text('camofox-only\n')
        self.marker.chmod(0o644)

    def test_marker_is_baked_exact_and_cannot_be_selected_with_environment(self):
        with patch.dict(os.environ, {'SPARTAN_HERMES_BROWSER_MODE': 'camofox-only'}):
            self.assertFalse(variant.camofox_only())
        self.enable()
        self.assertTrue(variant.camofox_only())
        for value in ('', 'full', ' camofox-only', 'camofox-only\n\n', 'camofox-only' * 4):
            self.marker.write_text(value)
            with self.assertRaises(RuntimeError):
                variant.camofox_only()
        self.marker.unlink()
        self.marker.symlink_to(self.root / 'missing')
        with self.assertRaises(RuntimeError):
            variant.camofox_only()

    def test_marker_permissions_fail_closed(self):
        self.enable()
        self.marker.chmod(0o666)
        with self.assertRaises(RuntimeError):
            variant.camofox_only()

    def test_transitive_closures_denied_full_mode_preserved(self):
        variant.require_tools(['chromium'])
        self.enable()
        for name in ('chromium', 'agent-browser', 'wrapper'):
            with self.assertRaises(FakeInstallError):
                variant.require_tools([name])
        for name in ('ffmpeg', 'ripgrep', 'python', 'unknown-old-lock-pin'):
            self.assertTrue(variant.supported([name]))

    def test_every_admission_guard_precedes_facts_network_and_worker_dispatch(self):
        self.enable()
        signatures = {'ensure': 'name, **kwargs', 'stage_only': 'name, target=None, **kwargs',
                      '_install': 'package, **kwargs', 'prepare_tools': 'names, **kwargs',
                      'stage_tools': 'names, **kwargs', 'verified_tools': 'names, **kwargs'}
        for filename, functions in patcher.GUARDS.items():
            for function, guard in functions.items():
                if function not in signatures:
                    continue
                source = f'def {function}({signatures[function]}):\n    """upstream entry"""\n    side_effect()\n'
                result = patcher.insert_guard(source, function, guard)
                self.assertEqual(result, patcher.insert_guard(result, function, guard))
                namespace = {'side_effect': lambda: self.fail('facts/network/worker touched before denial')}
                exec(result, namespace)
                args = (["wrapper"] if function in ('prepare_tools', 'stage_tools', 'verified_tools') else
                        types.SimpleNamespace(name='wrapper') if function == '_install' else 'wrapper')
                with self.subTest(filename=filename, function=function):
                    with self.assertRaises(FakeInstallError):
                        namespace[function](args)

    def test_stale_installed_facts_cannot_heal_or_serve_native_browser(self):
        self.enable()
        source = 'def _installed_location(package, *args, **kwargs):\n    facts[package.name] = "healed-exec-bit"\n    return "installed-old-generation"\n'
        source = patcher.insert_guard(source, '_installed_location', patcher.GUARDS['pm/install.py']['_installed_location'])
        facts = {'chromium': 'stale-installed', 'agent-browser': 'stale-installed'}
        namespace = {'facts': facts}
        exec(source, namespace)
        for name in facts:
            self.assertIsNone(namespace['_installed_location'](types.SimpleNamespace(name=name)))
        self.assertEqual(facts, {'chromium': 'stale-installed', 'agent-browser': 'stale-installed'})

    def test_local_discovery_overrides_and_install_hints_disabled(self):
        self.enable()
        for filename, functions in patcher.GUARDS.items():
            if filename.startswith('pm/') or filename == 'tools/browser_tool.py':
                continue
            for function, guard in functions.items():
                source = f'def {function}(*args, **kwargs):\n    raise AssertionError("native PATH/executable/install accessed")\n'
                namespace = {}
                exec(patcher.insert_guard(source, function, guard), namespace)
                if function == '_find_agent_browser':
                    with self.assertRaises(FileNotFoundError):
                        namespace[function]()
                else:
                    result = namespace[function]()
                    if function == '_browser_install_hint':
                        self.assertNotIn('install chromium', result)

    def test_screenshot_install_hint_is_variant_only(self):
        source = '''def _capture_vision_screenshot():
    return f"a missing Chromium install ('agent-browser install'), "
'''
        result = patcher.patch_source('tools/browser_tool.py', source)
        namespace = {}
        exec(result, namespace)
        self.assertIn('agent-browser install', namespace['_capture_vision_screenshot']())
        self.enable()
        self.assertNotIn('agent-browser install', namespace['_capture_vision_screenshot']())

    def test_source_overlay_and_helper_shadow_fail_closed(self):
        import hashlib
        self.enable()
        (self.root / 'pm').mkdir()
        helper = (HERE / 'spartan_browser_variant.py').read_bytes()
        (self.root / 'spartan_browser_variant.py').write_bytes(helper)
        (self.root / 'pm/spartan_browser_variant.py').write_bytes(helper)
        guarded = self.root / 'pm/client.py'
        guarded.write_text('guarded source')
        manifest = {'schema': 1, 'files': {'pm/client.py': hashlib.sha256(guarded.read_bytes()).hexdigest()}}
        (self.root / 'pm/spartan-browser-variant.json').write_text(json.dumps(manifest))
        with patch.object(variant, '__file__', str(self.root / 'spartan_browser_variant.py')):
            variant.validate_runtime(self.root)
            guarded.write_text('old overlay without guard')
            with self.assertRaises(RuntimeError):
                variant.validate_runtime(self.root)
            guarded.write_text('guarded source')
        with patch.object(variant, '__file__', str(self.root / 'shadow/spartan_browser_variant.py')):
            with self.assertRaises(RuntimeError):
                variant.validate_runtime(self.root)

    def test_startup_rejects_unmanaged_before_publication(self):
        policy = load('variant_policy_test', HERE / 'spartan_browser_policy.py')
        self.enable()
        documents = {'mode.json': json.dumps({'mode': 'unmanaged'}).encode()}
        environment = {'SPARTAN_BROWSER_POLICY_ENABLED': '1', 'HERMES_MANAGED_DIR': str(policy.MANAGED_DIR),
                       'SPARTAN_HERMES_RUN_UID': '1000', 'SPARTAN_HERMES_RUN_GID': '1000'}
        with patch.dict(sys.modules, {'spartan_browser_variant': variant}), patch.dict(os.environ, environment), \
                patch.object(policy.os, 'geteuid', return_value=0), patch.object(policy.os.path, 'lexists', return_value=True), \
                patch.object(variant, 'validate_runtime'), patch.object(policy, 'validate_code'), \
                patch.object(policy, 'prepare_documents', return_value=documents), patch.object(policy, 'publish_policy') as publish:
            with self.assertRaises(ValueError):
                policy.main()
            publish.assert_not_called()

    def test_managed_policy_is_required_for_meet(self):
        self.enable()
        policy = types.SimpleNamespace(camofox_policy_enabled=lambda: False)
        with patch.dict(sys.modules, {'spartan_browser_policy': policy}):
            with self.assertRaises(RuntimeError):
                variant.require_managed_camofox()
            policy.camofox_policy_enabled = lambda: True
            self.assertTrue(variant.require_managed_camofox())
        for function in ('_cmd_install', '_cmd_auth'):
            source = f'def {function}(*args, **kwargs):\n    raise AssertionError("native Meet branch reached")\n'
            namespace = {}
            exec(patcher.insert_guard(source, function, patcher.MEET_GUARDS['plugins/google_meet/cli.py'][function]), namespace)
            with patch('builtins.print'):
                self.assertEqual(namespace[function](), 2)

    def test_batch_preflight_failure_does_not_partially_patch(self):
        for name, functions in patcher.GUARDS.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            # Last missing target forces failure after earlier transformations.
            path.write_text('\n'.join(f'def {function}():\n    pass\n' for function in functions))
        original = (self.root / 'pm/client.py').read_bytes()
        with self.assertRaises(ValueError):
            patcher.patch_tree(self.root, helper=HERE / 'spartan_browser_variant.py')
        self.assertEqual((self.root / 'pm/client.py').read_bytes(), original)

    def test_marker_tampering_in_patched_function_fails(self):
        source = 'def ensure(name):\n    pass\n'
        patched = patcher.insert_guard(source, 'ensure', 'require_tools([name])')
        with self.assertRaises(ValueError):
            patcher.insert_guard(patched.replace('require_tools([name])', 'pass'), 'ensure', 'require_tools([name])')


@unittest.skipUnless(os.environ.get('HERMES_UPSTREAM_SOURCE'), 'set HERMES_UPSTREAM_SOURCE to pinned program source')
class PinnedSourceIntegrationTests(unittest.TestCase):
    def setUp(self):
        import shutil
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'image'
        source = Path(os.environ['HERMES_UPSTREAM_SOURCE'])
        shutil.copytree(source / 'pm', self.root / 'pm', ignore=shutil.ignore_patterns('__pycache__'))
        for name in patcher.GUARDS:
            if name.startswith('pm/'):
                continue
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
        self.marker = self.root / 'variant-marker'
        self.marker.write_text('camofox-only\n')
        self.marker.chmod(0o644)
        patcher.patch_tree(self.root, helper=HERE / 'spartan_browser_variant.py')
        helper = self.root / 'pm/spartan_browser_variant.py'
        # Substitute only immutable OS inputs in this disposable image fixture.
        text = helper.read_text().replace("Path('/etc/spartan/hermes-browser-mode')", f'Path({str(self.marker)!r})')
        text = text.replace('metadata.st_uid != 0', f'metadata.st_uid != {os.geteuid()}')
        helper.write_text(text)

    def run_python(self, code, *, input=None):
        import subprocess
        environment = {'HOME': str(self.root), 'HERMES_HOME': str(self.root / 'profile'), 'PATH': os.environ.get('PATH', '')}
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', code],
                                input=input, text=True, capture_output=True, env=environment, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_real_pm_facade_direct_stage_and_prepare_reject_before_state(self):
        code = f'''import sys
sys.path.insert(0, {str(self.root)!r})
import pm, pm.client, pm.install, pm.build_operations
from pathlib import Path
from unittest.mock import patch
from pm.package import InstallError
for method, args, kwargs in [
 (pm.ensure, ('agent-browser',), {{'explicit': True}}),
 (pm.client.ensure, ('chromium',), {{}}),
 (pm.install.ensure, ('agent-browser',), {{'explicit': True}}),
 (pm.client.stage_only, ('chromium', 'linux-x64'), {{}}),
 (pm.install.stage_only, ('agent-browser', 'linux-x64'), {{}}),
 (pm.client.prepare_tools, (['agent-browser'],), {{'out': Path('unused'), 'target': 'linux-x64'}}),
 (pm.build_operations.stage_tools, (['chromium'],), {{'source_store': Path('unused-source'), 'out': Path('unused'), 'target': 'linux-x64'}}),
]:
 with patch('pm.client._request', side_effect=AssertionError('worker dispatched')), patch('pm.install._facts', side_effect=AssertionError('facts read')), patch('pm.install._store', side_effect=AssertionError('store opened')):
  try: method(*args, **kwargs)
  except InstallError: pass
  else: raise AssertionError('native browser admitted')
print('native admission denied')
'''
        self.assertIn('native admission denied', self.run_python(code))
        self.assertFalse((self.root / 'profile').exists())

    def test_worker_uses_image_pm_with_existing_old_dependency_generation(self):
        old = self.root / 'old-generation-site'
        (old / 'pm').mkdir(parents=True)
        (old / 'pm/__init__.py').write_text("raise AssertionError('stale PM shadow imported')\n")
        (old / 'truststore.py').write_text('def inject_into_ssl(): pass\n')
        worker = self.root / 'pm/worker.py'
        request = {'id': 'variant-test', 'operation': 'ensure', 'arguments': {'name': 'chromium', 'explicit': True},
                   'packages': [], 'context': {'repo': str(self.root), 'lockfile': str(self.root / 'pm/lock.json')},
                   'callbacks': []}
        code = f'import sys,runpy; sys.path.insert(0,{str(old)!r}); runpy.run_path({str(worker)!r},run_name="__main__")'
        output = self.run_python(code, input=json.dumps(request) + '\n')
        response = json.loads(output.strip().splitlines()[-1])
        self.assertEqual(response['error']['type'], 'InstallError')
        self.assertIn('managed Camofox only', response['error']['message'])
        self.assertFalse((self.root / 'profile').exists())

    def test_final_meet_adaptation_revalidates_core_and_support_hashes(self):
        source = Path(os.environ['HERMES_UPSTREAM_SOURCE'])
        meet = load('variant_meet_patcher', HERE / 'patches/patch_google_meet_cdp.py')
        for name, transform in [('plugins/google_meet/meet_bot.py', meet.patch_meet_bot_source),
                                ('plugins/google_meet/cli.py', meet.patch_cli_source)]:
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(transform((source / name).read_text()))
        (self.root / 'bootstrap').mkdir()
        for name in ('spartan_browser_policy.py', 'spartan_browser_variant.py'):
            data = (HERE / name).read_bytes()
            (self.root / name).write_bytes(data)
            (self.root / 'bootstrap' / name).write_bytes(data)
        patcher.patch_tree(self.root, helper=HERE / 'spartan_browser_variant.py', meet_only=True)
        manifest = (self.root / 'pm/spartan-browser-variant.json').read_bytes()
        patcher.patch_tree(self.root, helper=HERE / 'spartan_browser_variant.py', meet_only=True)
        self.assertEqual(manifest, (self.root / 'pm/spartan-browser-variant.json').read_bytes())
        self.assertIn('bootstrap/spartan_browser_policy.py', json.loads(manifest)['files'])

    def test_source_patcher_is_idempotent_against_pinned_functions(self):
        patcher.patch_tree(self.root, helper=HERE / 'spartan_browser_variant.py')
        before = {name: (self.root / name).read_bytes() for name in patcher.GUARDS}
        patcher.patch_tree(self.root, helper=HERE / 'spartan_browser_variant.py')
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in patcher.GUARDS})


if __name__ == '__main__':
    unittest.main()
