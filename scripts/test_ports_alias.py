import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PortsAliasTests(unittest.TestCase):
    def test_ports_passes_valid_three_condition_filter_without_compose_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stub = root / 'ss'
            stub.write_text('''#!/usr/bin/env python3
import sys
if sys.argv[1:3] != ['-H', '-ltn'] or len(sys.argv) != 4:
    raise SystemExit(2)
tokens = sys.argv[3].split()
if tokens[:1] != ['('] or tokens[-1:] != [')']:
    raise SystemExit(2)
terms = tokens[1:-1]
ports = []
while terms:
    if len(terms) < 3 or terms[:2] != ['sport', '=']:
        raise SystemExit(2)
    ports.append(terms[2])
    terms = terms[3:]
    if terms:
        if terms.pop(0) != 'or' or not terms:
            raise SystemExit(2)
if ports != [':18789', ':18790', ':9119']:
    raise SystemExit(2)
print('LISTEN fixture-selected-ports')
''')
            stub.chmod(0o755)
            env = dict(os.environ, PATH=f'{root}:{os.environ["PATH"]}')
            result = subprocess.run(
                ['bash', '--noprofile', '--norc', '-c',
                 'source "$1"; sg_compose() { printf "unexpected compose fallback\\n"; return 91; }; sg_ports',
                 'ports-test', str(ROOT / 'scripts/aliases.sh')],
                cwd=ROOT, env=env, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout, 'LISTEN fixture-selected-ports\n')
