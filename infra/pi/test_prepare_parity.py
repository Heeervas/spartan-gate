"""Native candidate behavior, not live-home installation tests."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location('parity', ROOT / 'prepare_parity.py')
parity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(parity)


class ParityTests(unittest.TestCase):
    def setUp(self):
        self.baseline = parity.object_file(ROOT / 'common-baseline.json')
        self.bindings = {k: '/isolated/' + k for k in parity.BINDINGS}

    def test_preserves_foreign_fields_disables_and_is_idempotent(self):
        before = {'theme': 'custom', 'packages': ['old', {'source': 'npm:pi-subagents-lite', 'extensions': []}],
                  'extensions': ['-foreign.ts'], 'subagents': {'agentExcludeDirs': ['/private/plugin'], 'custom': 4}}
        config = {'waitTool': {'enabled': False}, 'scheduledRuns': {'maxPending': 2}}
        result, native = parity.materialize(self.baseline, before, config, self.bindings, 'old')
        self.assertEqual(result['packages'], before['packages'][1:])
        self.assertEqual(result['theme'], 'custom')
        self.assertIn('-foreign.ts', result['extensions'])
        self.assertIn('/private/plugin', result['subagents']['agentExcludeDirs'])
        self.assertEqual(result['subagents']['custom'], 4)
        self.assertFalse(native['waitTool']['enabled'])
        self.assertEqual(native['scheduledRuns']['maxPending'], 2)
        self.assertEqual((result, native), parity.materialize(self.baseline, result, native, self.bindings, 'old'))
        self.assertNotIn('skills', before)

    def test_preserves_existing_disabled_features_with_baseline_and_idempotence(self):
        config = {'disabledFeatures': ['gates', 'watchdog']}
        settings, native = parity.materialize(self.baseline, {}, config, self.bindings)
        self.assertEqual(native['disabledFeatures'], ['gates', 'watchdog'] + [
            feature for feature in self.baseline['config']['disabledFeatures'] if feature != 'watchdog'])
        self.assertEqual((settings, native), parity.materialize(self.baseline, settings, native, self.bindings))
        self.assertEqual(config['disabledFeatures'], ['gates', 'watchdog'])

    def test_explicit_mcp_opt_in_survives_without_enabling_other_builtins(self):
        result, native = parity.materialize(self.baseline, {'extensions': ['builtin:mcp']}, {}, self.bindings)
        self.assertIn('builtin:mcp', result['extensions'])
        self.assertNotIn('-builtin:mcp', result['extensions'])
        for builtin in ('llama.cpp', 'codemode', 'tool-search'):
            self.assertIn('-builtin:' + builtin, result['extensions'])
        default, _ = parity.materialize(self.baseline, {}, {}, self.bindings)
        self.assertIn('-builtin:mcp', default['extensions'])
        self.assertEqual((result, native), parity.materialize(self.baseline, result, native, self.bindings))

    def test_required_native_disable_is_not_silently_overridden(self):
        disabled = {'extensions': ['-/isolated/routeRoot/spartan-route.ts']}
        with self.assertRaisesRegex(ValueError, 'natively disabled'):
            parity.materialize(self.baseline, disabled, {}, self.bindings)

    def test_bad_baseline_refuses_before_any_output(self):
        nested = copy.deepcopy(self.baseline['config'])
        nested['scheduledRuns']['credentialsFile'] = 'not-allowed'
        for change in ({'credentialsFile': 'not-allowed'}, {'version': 2}, {'config': nested}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                baseline = copy.deepcopy(self.baseline)
                baseline.update(change)
                for name, value in [('baseline', baseline), ('bindings', self.bindings), ('settings', {'foreign': True})]:
                    (root / name).write_text(json.dumps(value))
                command = [sys.executable, str(ROOT / 'prepare_parity.py'), '--baseline', str(root / 'baseline'),
                           '--bindings', str(root / 'bindings'), '--settings', str(root / 'settings'),
                           '--output', str(root / 'candidate')]
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 1)
                self.assertIn('refused', result.stderr)
                self.assertFalse((root / 'candidate').exists())
                self.assertEqual(json.loads((root / 'settings').read_text()), {'foreign': True})

    def test_prepare_check_and_occupied_output_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('bindings', self.bindings), ('settings', {'theme': 'custom'})]:
                (root / name).write_text(json.dumps(value))
            command = [sys.executable, str(ROOT / 'prepare_parity.py'), '--bindings', str(root / 'bindings'),
                       '--settings', str(root / 'settings'), '--output', str(root / 'candidate')]
            self.assertEqual(subprocess.run(command, capture_output=True, check=False).returncode, 0)
            self.assertEqual(subprocess.run(command + ['--check'], capture_output=True, check=False).returncode, 0)
            self.assertEqual(subprocess.run(command, capture_output=True, check=False).returncode, 1)
            (root / 'candidate/settings.json').write_text('{}')
            self.assertEqual(subprocess.run(command + ['--check'], capture_output=True, check=False).returncode, 1)
            hidden = root / '.pi/candidate'
            self.assertEqual(subprocess.run(command + ['--output', str(hidden)], capture_output=True, check=False).returncode, 1)
            self.assertFalse(hidden.exists())


if __name__ == '__main__':
    unittest.main()
