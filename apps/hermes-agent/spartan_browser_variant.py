"""Baked Camofox-only capability boundary; independent of workload environment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat

MARKER_PATH = Path('/etc/spartan/hermes-browser-mode')
DENIED = frozenset({'chromium', 'agent-browser'})
ERROR = 'This Hermes image supports managed Camofox only; local Chromium is unavailable'


def camofox_only() -> bool:
    try:
        metadata = MARKER_PATH.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 32
            or metadata.st_uid != 0 or metadata.st_mode & 0o022
            or MARKER_PATH.read_bytes() not in (b'camofox-only', b'camofox-only\n')):
        raise RuntimeError('Invalid baked Hermes browser variant marker')
    return True


def supported(names) -> bool:
    if not camofox_only():
        return True
    names = list(names)
    if DENIED.intersection(names):
        return False
    from pm.registry import walk
    try:
        return not any(package.name in DENIED for package in walk(names))
    except KeyError:
        # Existing readers skip unknown lock names during mixed-version updates.
        return True


def require_tools(names) -> None:
    if not supported(names):
        from pm.package import InstallError
        raise InstallError('browser', ERROR, 'Use the managed Camofox service')


def require_managed_camofox() -> bool:
    if not camofox_only():
        return False
    from spartan_browser_policy import camofox_policy_enabled
    if not camofox_policy_enabled():
        raise RuntimeError('Camofox-only image requires valid managed Camofox configuration')
    return True


def validate_runtime(root: Path) -> None:
    """Reject code overlays that replace a baked guard, including helper shadows."""
    if not camofox_only():
        return
    manifest = json.loads((root / 'pm/spartan-browser-variant.json').read_text())
    if (not isinstance(manifest, dict) or manifest.get('schema') != 1
            or not isinstance(manifest.get('files'), dict)):
        raise RuntimeError('Missing Camofox-only source guard manifest')
    for name, expected in manifest['files'].items():
        path = root / name
        if (not path.is_relative_to(root) or path.is_symlink()
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise RuntimeError('Camofox-only source guard was replaced by a code overlay')
    helper = root / 'spartan_browser_variant.py'
    if Path(__file__).resolve() not in (helper, root / 'bootstrap/spartan_browser_variant.py'):
        raise RuntimeError('Camofox-only variant helper resolved outside the image')
    if (helper.read_bytes() != (root / 'pm/spartan_browser_variant.py').read_bytes()
            or Path(__file__).read_bytes() != helper.read_bytes()):
        raise RuntimeError('Camofox-only variant helper differs from PM worker helper')


if __name__ == '__main__':
    try:
        validate_runtime(Path('/opt/hermes'))
        require_managed_camofox()
    except Exception as exc:
        print(f'[browser-variant] FATAL: variant initialization rejected ({type(exc).__name__})', file=__import__('sys').stderr)
        raise SystemExit(1) from None
