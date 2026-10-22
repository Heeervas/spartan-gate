import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AddPortTransactionTests(unittest.TestCase):
    def run_helper(self, marker=True, valid=True, listener='# preserved listener\n'):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            compose = root / 'compose.yml'
            listeners = root / 'caddy'
            listeners.mkdir()
            ports = listeners / 'ports.caddy'
            ports.write_text(listener)
            source = 'services:\n  caddy:\n    ports:\n'
            if marker:
                source += '      # scripts/add-port.sh inserts below this line.\n'
            compose.write_text(source)
            (root / 'local.env').write_text("CADDY_AUTH_HASH='$2a$14$UMuSzA3WdVqcAwE3yvFeCO.CesMjqqGDVrxpL1s87FsWM/NRrADi2'\nCADDY_SESSION_SERVICE_KEY=" + 'c' * 64 + '\n')
            docker = root / 'docker'
            docker.write_text('#!/bin/sh\nexit ' + ('0' if valid else '42') + '\n')
            docker.chmod(0o755)
            env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}',
                       SPARTAN_PRIVATE_COMPOSE=str(compose),
                       SPARTAN_CADDY_LOCAL_DIR=str(listeners),
                       SPARTAN_ENV_FILE=str(root / 'local.env'),
                       CADDY_BIN=os.environ.get('CADDY_BIN', 'caddy'))
            result = subprocess.run([str(ROOT / 'scripts/add-port.sh'), '8787'],
                                    env=env, capture_output=True, text=True)
            return result, source, compose.read_text(), ports.read_text()

    def test_existing_static_session_listener_requires_migration(self):
        listener = '# spartan-gate:add-port:8787\n:8787 {\n # spartan_edge_apps static\n}\n'
        result, before, after, ports = self.run_helper(listener=listener)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('signed server TTL required', result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(ports, listener)

    def test_missing_marker_preserves_both_files(self):
        result, before, after, ports = self.run_helper(marker=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, after)
        self.assertEqual(ports, '# preserved listener\n')

    def test_invalid_compose_preserves_both_files(self):
        result, before, after, ports = self.run_helper(valid=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, after)
        self.assertEqual(ports, '# preserved listener\n')

    def test_valid_candidate_publishes_mapping_and_listener_without_restart(self):
        result, before, after, ports = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('8787', after)
        self.assertIn('reverse_proxy http://hermes:8787', ports)
        self.assertIn('--force-recreate caddy', result.stdout)
        self.assertNotIn('Restarting', result.stdout)
