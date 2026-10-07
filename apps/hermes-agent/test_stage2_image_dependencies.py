import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / 'patches' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

patcher = load('patch_stage2_image_dependencies')
validator = load('validate_image_dependencies')


class ImageDependencyTests(unittest.TestCase):
    def test_deterministic_patch_and_original_refresh_retained(self):
        original = 'set -eu\n' + patcher.OLD
        result = patcher.patch_text(original)
        self.assertEqual(result, patcher.patch_text(result))
        self.assertEqual(result.count(patcher.OLD), 1)
        self.assertIn('SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY:-0', result)
        self.assertIn('validate_init_runner(parent)', result)
        self.assertIn('exec /command/s6-pause', result)
        self.assertEqual(subprocess.run(['sh', '-n'], input=result, text=True, capture_output=True).returncode, 0)
        with self.assertRaises(ValueError):
            patcher.patch_text(result.replace('validate_image_dependencies.py', 'skip.py'))
        with self.assertRaises(ValueError):
            patcher.patch_text(original.replace('refresh_dependencies(root)', 'changed(root)'))

    def test_flag_runs_validation_only_and_default_runs_original_refresh(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / '.venv/bin').mkdir(parents=True)
            python = root / '.venv/bin/python'
            python.write_text('#!/bin/sh\ncase "$1" in -c) echo refresh-and-collection;; -B) echo validation-only;; *) exit 3;; esac\n')
            python.chmod(0o755)
            source = 'set -eu\nas_hermes() { "$@"; }\nINSTALL_DIR=' + str(root) + '\n' + patcher.patch_text(patcher.OLD)
            for flag, expected in [('0', 'refresh-and-collection'), ('1', 'validation-only')]:
                result = subprocess.run(['sh', '-c', source], env={'SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY': flag}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_selected_or_malformed_facts_are_refused(self):
        good = {'schema': 1, 'packages': {'venv': {'stamp': 'original-old-stamp', 'extras': ['web']}}}
        self.assertEqual(validator.recorded_extras(good), ['web'])
        for key in ('environment', 'resolved_lock'):
            for value in (None, '/old/generation'):
                bad = json.loads(json.dumps(good))
                bad['packages']['venv'][key] = value
                with self.assertRaises(RuntimeError):
                    validator.recorded_extras(bad)
        for bad in ({}, {'schema': 1, 'packages': []}, {'schema': 1, 'packages': {'venv': {'extras': []}}}):
            with self.assertRaises(RuntimeError):
                validator.recorded_extras(bad)

    def test_validator_checks_baked_inventory_and_never_publishes_state(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            facts = root / 'facts.json'
            original = {'schema': 1, 'packages': {'venv': {'stamp': 'old-unmodified', 'extras': ['web']}}}
            facts.write_text(json.dumps(original))
            (root / 'enabled-features.json').write_text(json.dumps({'schema': 1, 'extras': ['web', 'cron']}))
            probe = Mock()
            inventory = Mock(return_value=['cron', 'web'])
            modules = {
                'pm': SimpleNamespace(),
                'pm.environments': SimpleNamespace(runtime_facts_path=lambda _: facts),
                'pm.features': SimpleNamespace(installed_extras=inventory),
                'pm.recovery': SimpleNamespace(validate_environment=probe),
                'pm.plugin_declarations': SimpleNamespace(read_python_declaration=Mock()),
                'pm.workspace': SimpleNamespace(enabled_plugin_dirs=Mock(return_value=[])),
            }
            with patch.dict(sys.modules, modules), patch.object(sys, 'prefix', str(root / '.venv')):
                validator.validate(root)
                self.assertEqual(json.loads(facts.read_text()), original)
                probe.assert_called_once()
                self.assertEqual(probe.call_args.args[0], root / '.venv/bin/python')
                inventory.assert_called_once_with(root, root / '.venv', python_exe=root / '.venv/bin/python')
                original['packages']['venv']['extras'] = ['not-shipped']
                facts.write_text(json.dumps(original))
                with self.assertRaises(RuntimeError):
                    validator.validate(root)
                original['packages']['venv']['environment'] = '/selected'
                facts.write_text(json.dumps(original))
                probe.reset_mock()
                with self.assertRaises(RuntimeError):
                    validator.validate(root)
                probe.assert_not_called()
            self.assertEqual(json.loads(facts.read_text()), original)

    def test_requires_baked_interpreter(self):
        with self.assertRaises(RuntimeError):
            validator.validate(Path('/not-current-image'))

    def test_plugin_requirements_versions_markers_extras_and_transitive_closure(self):
        try:
            import packaging
        except ImportError:
            self.skipTest('packaging required for native requirement parser tests')
        packages = {'parent': SimpleNamespace(version='2.0', requires=['child>=3; extra == "feature"']),
                    'child': SimpleNamespace(version='3.1', requires=[])}
        def distribution(name):
            if name not in packages:
                raise importlib.metadata.PackageNotFoundError(name)
            return packages[name]
        validator.require_installed(['parent[feature]>=2', 'absent; python_version < "2"'], distribution=distribution)
        for requirements in (['parent>=9'], ['absent'], ['pkg @ https://example.invalid/pkg.whl']):
            with self.assertRaises(RuntimeError):
                validator.require_installed(requirements, distribution=distribution)
        del packages['child']
        with self.assertRaises(RuntimeError):
            validator.require_installed(['parent[feature]'], distribution=distribution)

    def test_dockerfile_applies_startup_patch(self):
        source = (HERE / 'Dockerfile').read_text()
        self.assertIn('python3 /opt/hermes/patches/patch_stage2_image_dependencies.py', source)
        self.assertLess(source.index('patch_stage2_image_dependencies.py'), source.index('freeze_image_features.py'))


if __name__ == '__main__':
    unittest.main()
