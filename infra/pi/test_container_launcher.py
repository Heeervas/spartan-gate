"""Pi-only direct HTTPS opt-in; fake Node and disposable owner state, no network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LAUNCHER = Path(__file__).parent / 'container/pi'


class ContainerLauncherTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.home = Path(self.scratch.name)
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        node = self.bin / 'node'
        node.write_text(f'#!{sys.executable}\nimport json, os, sys\nprint(json.dumps({{"argv": sys.argv[1:], "env": dict(os.environ)}}))\n')
        node.chmod(0o755)
        self.route = self.home / 'pi/spartan-route'
        self.route.mkdir(parents=True)
        self.config = self.home / 'override-config.json'
        self.config.write_text(json.dumps({'baseUrl': 'https://route.example.invalid:18790/v1'}))
        self.env = {
            'PATH': f'{self.bin}:{os.defpath}',
            'HERMES_HOME': str(self.home),
            'PI_SPARTAN_ROUTE_CONFIG': str(self.config),
            'HTTPS_PROXY': 'http://proxy.invalid:8888',
            'https_proxy': 'http://lower-proxy.invalid:8888',
            'HTTP_PROXY': 'http://http-proxy.invalid:8888',
            'http_proxy': 'http://lower-http-proxy.invalid:8888',
        }

    def invoke(self, env):
        return subprocess.run([str(LAUNCHER), '--print', 'two words', '@task.md'], env=env, capture_output=True, text=True, check=False)

    def test_no_opt_in_leaves_proxy_lists_unchanged_without_python_or_config(self):
        python = self.bin / 'python3'
        python.write_text('#!/bin/sh\nexit 99\n')
        python.chmod(0o755)
        self.config.unlink()
        for lists in ({}, {'NO_PROXY': 'upper.invalid', 'no_proxy': 'lower.invalid'}):
            with self.subTest(lists=lists):
                result = self.invoke(self.env | lists)
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = json.loads(result.stdout)['env']
                for key in ('NO_PROXY', 'no_proxy'):
                    self.assertEqual(actual.get(key), lists.get(key))

    def test_matching_origin_appends_each_list_preserving_proxy_values_and_argv(self):
        (self.route / 'direct-https-origin').write_text('https://route.example.invalid:18790/\n')
        cases = [
            ({'NO_PROXY': 'upper.invalid', 'no_proxy': 'lower.invalid'}, 'upper.invalid', 'lower.invalid'),
            ({'NO_PROXY': 'upper.invalid'}, 'upper.invalid', 'upper.invalid'),
            ({'no_proxy': 'lower.invalid'}, 'lower.invalid', 'lower.invalid'),
            ({}, '', ''),
        ]
        for lists, upper, lower in cases:
            with self.subTest(lists=lists):
                result = self.invoke(self.env | lists)
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = json.loads(result.stdout)
                self.assertEqual(actual['argv'], ['/opt/pi-package/dist/bundle/cli.js', '--print', 'two words', '@task.md'])
                for key, baseline in (('NO_PROXY', upper), ('no_proxy', lower)):
                    expected = (baseline + ',' if baseline else '') + 'route.example.invalid:18790'
                    self.assertEqual(actual['env'][key], expected)
                for key, value in self.env.items():
                    self.assertEqual(actual['env'][key], value)
                self.assertEqual(actual['env']['PI_CODING_AGENT_DIR'], str(self.home / 'pi/agent'))
                self.assertEqual(actual['env']['PI_CODING_AGENT_SESSION_DIR'], str(self.home / 'pi/sessions'))
                self.assertEqual(actual['env']['PI_SUBAGENTS_PACKAGE_ROOT'], '/opt/pi-subagents-nicobailon')
                self.assertEqual(actual['env']['PI_SUBAGENTS_PI_CODING_AGENT_PACKAGE_ROOT'], '/opt/pi-package')
        self.config.write_text(json.dumps({'baseUrl': 'https://route.example.invalid/v1'}))
        (self.route / 'direct-https-origin').write_text('https://route.example.invalid:443')
        overrides = {'PI_CODING_AGENT_DIR': str(self.home / 'custom-agent'), 'PI_CODING_AGENT_SESSION_DIR': str(self.home / 'custom-sessions'),
                     'PI_SUBAGENTS_PACKAGE_ROOT': '/selected/extension', 'PI_SUBAGENTS_PI_CODING_AGENT_PACKAGE_ROOT': '/selected/pi'}
        result = self.invoke(self.env | overrides)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)['env']
        self.assertEqual(actual['NO_PROXY'], 'route.example.invalid:443')
        for key, value in overrides.items():
            self.assertEqual(actual[key], value)

    def test_invalid_or_mismatched_binding_refuses_before_node_without_private_output(self):
        for binding in ('https://other.example.invalid:18790', 'https://route.example.invalid:18791', 'http://route.example.invalid:18790', 'https://*.example.invalid:18790', 'https://user:synthetic-secret@route.example.invalid:18790', 'https://route.example.invalid:18790/v1', 'https://route.example.invalid:18790?synthetic-secret', 'https://127.0.0.1:18790', 'https://route.example.invalid:18790 extra'):
            with self.subTest(binding=binding):
                (self.route / 'direct-https-origin').write_text(binding)
                result = self.invoke(self.env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, 'Pi direct HTTPS binding invalid or unavailable.\n')
        (self.route / 'direct-https-origin').write_text('https://route.example.invalid:18790')
        self.config.write_text('{synthetic-secret:invalid-json')
        result = self.invoke(self.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, 'Pi direct HTTPS binding invalid or unavailable.\n')


if __name__ == '__main__':
    unittest.main()
