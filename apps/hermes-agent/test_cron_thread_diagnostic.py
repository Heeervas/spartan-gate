import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
from typing import Optional


BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location('cron_diagnostic_patch', BASE / 'patches/patch_cron_thread_diagnostic.py')
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)
SOURCE = (BASE / 'fixtures/cron_thread_diagnostic.py').read_text()


class CronThreadDiagnosticTests(unittest.TestCase):
    def prepare(self, target, *, repaired=True, stale=False):
        logger = Mock()
        namespace = {'logger': logger, 'Optional': Optional, '_TargetDelivery': object,
                     '_resolve_origin': lambda job: job['origin'],
                     '_origin_delivery_thread': lambda origin: None if stale else origin['thread_id']}
        module = ModuleType('gateway.config')
        module.Platform = object
        with patch.dict(sys.modules, {'gateway.config': module}):
            exec(PATCH.patch_text(SOURCE) if repaired else SOURCE, namespace)
            job = {'id': 'synthetic-job', 'deliver': 'discord:explicit-target', 'origin': {'platform': 'discord', 'chat_id': 'parent', 'thread_id': 'origin-thread'}}
            namespace['_prepare_target_delivery'](job, target, adapters={}, loop=None, config=None, notify_delivery=True, mirror_enabled=False, mirror_text='', delivery_errors=[])
        return logger

    def test_native_warning_reproduces_for_intentional_explicit_destination(self):
        target = {'platform': 'discord', 'chat_id': 'explicit-target', '_resolved_from': 'explicit'}
        self.prepare(target, repaired=False).warning.assert_called_once()
        self.prepare(target).warning.assert_not_called()
        self.assertEqual(target, {'platform': 'discord', 'chat_id': 'explicit-target', '_resolved_from': 'explicit'})

    def test_lost_origin_thread_still_warns(self):
        self.prepare({'platform': 'discord', 'chat_id': 'parent', '_resolved_from': 'origin'}).warning.assert_called_once()

    def test_native_fallback_and_discord_thread_channel_do_not_warn(self):
        self.prepare({'platform': 'slack', 'chat_id': 'parent', '_resolved_from': 'origin'}, stale=True).warning.assert_not_called()
        self.prepare({'platform': 'discord', 'chat_id': 'origin-thread', '_resolved_from': 'origin'}).warning.assert_not_called()
        self.prepare({'platform': 'discord', 'chat_id': 'parent', 'thread_id': 'origin-thread', '_resolved_from': 'origin'}).warning.assert_not_called()

    def test_idempotence_and_drift_refusal(self):
        result = PATCH.patch_text(SOURCE)
        self.assertEqual(PATCH.patch_text(result), result)
        with self.assertRaises(ValueError):
            PATCH.patch_text('upstream drift')


if __name__ == '__main__':
    unittest.main()
