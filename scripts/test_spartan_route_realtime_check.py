import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

SOURCE = Path(__file__).with_name('spartan_route_realtime_check.py')
spec = importlib.util.spec_from_file_location('realtime_check', SOURCE)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class AcceptanceHarnessTests(unittest.TestCase):
    def test_default_and_missing_approval_do_not_start_staging(self):
        for args, expected in (([], 0), (['--stage', '--slot', '1'], 2), (['--check'], 2)):
            result = subprocess.run([sys.executable, '-I', str(SOURCE), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)

    def test_bundle_transports_source_only_not_credentials_or_environment(self):
        with patch.dict(os.environ, {'CLAWROUTE_TOKEN': 'never-copy-this-existing-secret'}), patch.object(check.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(check.stage_bundle(1), 0)
        bundle = run.call_args.kwargs['input']
        self.assertNotIn('never-copy-this-existing-secret', bundle)
        self.assertIn('spartan_route.realtime', bundle)
        self.assertIn('spartan_route_realtime_probe', bundle)
        self.assertEqual(run.call_args.args[0][-1], 'source scripts/aliases.sh\nsg exec -T spartan-route python3 -')
