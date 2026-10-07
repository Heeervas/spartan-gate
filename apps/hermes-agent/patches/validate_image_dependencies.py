#!/usr/bin/env python3
"""Read-only admission for explicitly booting the image's baked dependencies."""
from __future__ import annotations

import importlib.metadata as metadata
import json
import os
from pathlib import Path
import sys


def recorded_extras(document: dict) -> list[str]:
    if (not isinstance(document, dict) or type(document.get('schema')) is not int
            or document['schema'] != 1 or not isinstance(document.get('packages'), dict)):
        raise RuntimeError('Invalid dependency facts')
    fact = document['packages'].get('venv')
    if 'venv' not in document['packages']:
        return []
    if (not isinstance(fact, dict) or not isinstance(fact.get('stamp'), str) or not fact['stamp']
            or not isinstance(fact.get('extras'), list)
            or any(not isinstance(extra, str) or not extra for extra in fact['extras'])):
        raise RuntimeError('Invalid recorded dependency selection')
    if 'environment' in fact or 'resolved_lock' in fact:
        raise RuntimeError('Image-only startup requires native fallback metadata with no selected generation')
    return fact['extras']


def require_installed(specs, *, distribution=metadata.distribution) -> None:
    """Check marker/specifier/extras closures without resolving or installing."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    seen = set()
    def check(spec, parent_extras=('',)):
        requirement = Requirement(spec)
        if requirement.marker and not any(requirement.marker.evaluate({'extra': extra}) for extra in parent_extras):
            return
        if requirement.url:
            raise RuntimeError('Image-only startup cannot verify a plugin direct-URL dependency')
        try:
            installed = distribution(requirement.name)
        except metadata.PackageNotFoundError as exc:
            raise RuntimeError('Plugin dependency is absent from the baked image') from exc
        if not requirement.specifier.contains(installed.version, prereleases=True):
            raise RuntimeError('Plugin dependency version is not satisfied by the baked image')
        key = canonicalize_name(requirement.name), tuple(sorted(requirement.extras))
        if key in seen:
            return
        seen.add(key)
        for dependency in installed.requires or ():
            check(dependency, ('', *requirement.extras))
    for spec in specs:
        check(spec)


def validate(root: Path) -> None:
    root = root.resolve()
    venv = root / '.venv'
    if Path(sys.prefix).resolve() != venv.resolve() or sys.prefix == sys.base_prefix:
        raise RuntimeError('Image-only validation must run in the baked image virtual environment')
    from pm.environments import runtime_facts_path
    from pm.features import installed_extras
    from pm.recovery import validate_environment
    from pm.plugin_declarations import read_python_declaration
    from pm.workspace import enabled_plugin_dirs

    facts = runtime_facts_path(root)
    def read():
        if facts.is_symlink():
            raise RuntimeError('Dependency facts must not be a symlink')
        if not facts.exists():
            return {'schema': 1, 'packages': {}}
        return json.loads(facts.read_text(encoding='utf-8-sig'))
    before = read()
    recorded = recorded_extras(before)
    frozen = json.loads((root / 'enabled-features.json').read_text())
    if (not isinstance(frozen, dict) or set(frozen) != {'schema', 'extras'}
            or type(frozen['schema']) is not int or frozen['schema'] != 1
            or not isinstance(frozen['extras'], list)
            or any(not isinstance(extra, str) or not extra for extra in frozen['extras'])):
        raise RuntimeError('Invalid measured image feature inventory')
    # Both probes use the baked interpreter explicitly, never a selected generation.
    validate_environment(venv / 'bin/python', env=dict(os.environ), cwd=root)
    measured = installed_extras(root, venv, python_exe=venv / 'bin/python')
    if set(measured) != set(frozen['extras']) or not set(recorded).issubset(measured):
        raise RuntimeError('Recorded extras are not satisfied by the measured baked image')
    for directory in enabled_plugin_dirs(skip_invalid_secondary=False):
        declaration = read_python_declaration(directory)
        if declaration.external:
            continue
        if declaration.python_error('.'.join(map(str, sys.version_info[:3]))):
            raise RuntimeError('Plugin requires an incompatible image Python version')
        require_installed(declaration.requirements)
    # No state writes, no fallback publication, no generation collection.
    if read() != before:
        raise RuntimeError('Dependency selection changed during image-only validation')
    print('[stage2] dependency environment: validated baked image (automatic sync and collection disabled)')


if __name__ == '__main__':
    try:
        project = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(project))
        validate(project)
    except Exception as exc:
        print(f'[stage2] FATAL: image-only dependency validation failed: {exc}', file=sys.stderr)
        raise SystemExit(1) from None
