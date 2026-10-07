#!/usr/bin/env python3
"""Restore image dependencies using native PM fallback; dry-run by default.

Run with the image's own Python, as the data owner. No resolver, installer,
generation deletion, or site-packages mixing is involved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


REQUIRED = frozenset({'cron', 'discord', 'mcp', 'telegram', 'web'})


def validate_selection(data: bytes, inventory: list[str]) -> tuple[str, list[str]]:
    document = json.loads(data)
    if not isinstance(document, dict) or document.get('schema') != 1:
        raise ValueError('invalid native PM facts')
    packages = document.get('packages')
    if not isinstance(packages, dict):
        raise ValueError('invalid native PM package records')
    fact = packages.get('venv')
    if (not isinstance(fact, dict) or fact.get('extras') != []
            or not isinstance(fact.get('stamp'), str) or not fact['stamp']):
        raise ValueError('recovery only accepts the diagnosed empty-feature generation')
    # Native record_state fallback omits generation pointers; committed_venv
    # still validates any selected generation before this recovery runs.
    for key in ('environment', 'resolved_lock'):
        if key in fact and (not isinstance(fact[key], str) or not fact[key].strip()):
            raise ValueError('invalid native PM generation pointer')
    if (not isinstance(inventory, list) or any(not isinstance(x, str) or not x.strip() for x in inventory)
            or not REQUIRED.issubset(inventory)):
        raise ValueError('image does not contain the required optional features')
    return fact['stamp'], sorted(set(inventory))


def _owned_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError('expected regular native PM facts')
    if path.stat().st_uid != os.geteuid():
        raise ValueError('run recovery as the native PM data owner')
    return path.read_bytes()


def probe_base(root: Path) -> None:
    script = ('import sys,pathlib,yaml,discord,telegram; '
              'assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[1]).resolve(); '
              'assert tuple(sys.version_info[:2])==tuple(map(int,sys.argv[2].split(".")))')
    result = subprocess.run([str(root / '.venv/bin/python'), '-I', '-B', '-c', script,
                             str(root / '.venv'), f'{sys.version_info.major}.{sys.version_info.minor}'],
                            capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('isolated base dependency/ABI probe failed')


def recover(root: Path, expected_hash: str, backup: Path | None = None) -> int:
    from pm.environments import committed_venv, runtime_facts_path
    from pm.features import installed_extras
    from pm.lock import Facts
    from pm.paths import repo_root
    from hermes_cli.runtime_state import runtime_lock

    root = root.resolve()
    if root != repo_root().resolve() or Path(sys.prefix).resolve() != root / '.venv':
        raise ValueError('recovery requires this installation and its original image interpreter')
    path = runtime_facts_path(root)
    original = _owned_file(path)
    if hashlib.sha256(original).hexdigest() != expected_hash:
        raise ValueError('native PM facts changed since diagnosis')
    committed_venv(root)  # Native validation rejects a missing/foreign generation.
    probe_base(root)
    extras = installed_extras(root, root / '.venv', python_exe=root / '.venv/bin/python')
    stamp, extras = validate_selection(original, extras)
    if backup is None:
        return len(extras)
    if (backup.is_symlink() or not backup.is_dir() or backup.stat().st_uid != os.geteuid()
            or stat.S_IMODE(backup.stat().st_mode) & 0o077 or any(backup.iterdir())):
        raise ValueError('backup directory must be empty, real, owner-only and owned by the data owner')
    with runtime_lock(root, timeout=None):
        if _owned_file(path) != original:
            raise ValueError('native PM selection changed during preflight')
        # Back up the entire native record, including other packages, before publication.
        saved = backup / 'facts.original.json'
        with saved.open('xb') as stream:
            os.chmod(saved, 0o600)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        facts = Facts(path, strict=True)
        # Same native fallback as pm.recovery, with the lost shipped features restored.
        facts.record_state('venv', stamp, extras)
    return len(extras)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/hermes'))
    parser.add_argument('--expected-facts-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    if args.apply and args.backup_dir is None:
        parser.error('--apply requires --backup-dir')
    sys.path.insert(0, str(args.root))
    count = recover(args.root, args.expected_facts_sha256, args.backup_dir if args.apply else None)
    print(f'{"Restored" if args.apply else "Validated"} native image fallback with {count} optional features; no packages installed')


if __name__ == '__main__':
    main()
