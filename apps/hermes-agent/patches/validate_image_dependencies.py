#!/usr/bin/env python3
"""Read-only admission for explicitly booting the image's baked dependencies."""
from __future__ import annotations

import importlib.metadata as metadata
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

CODE_FILES = ('hermes_cli/venv_sync.py', 'patches/validate_image_dependencies.py')
CODE_ANCHORS = ('pm/install.py', 'pm/environments.py', 'hermes_cli/main.py', 'gateway/run.py')
CODE_MANIFEST = '/run/spartan-image-dependencies-code/manifest.json'


def verify_code_manifest(root: Path, manifest: Path, image: str, expected_hash: str) -> None:
    import re
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 1024 * 1024:
        raise RuntimeError('Invalid dependency code manifest')
    raw = manifest.read_bytes()
    data = json.loads(raw)
    if (not re.fullmatch(r'sha256:[0-9a-f]{64}', image)
            or hashlib.sha256(raw).hexdigest() != expected_hash
            or set(data) != {'schema', 'image', 'files', 'anchors', 'originals'}
            or type(data['schema']) is not int or data['schema'] != 1 or data['image'] != image):
        raise RuntimeError('Dependency code manifest identity changed')
    for field, names in [('files', CODE_FILES), ('anchors', CODE_ANCHORS), ('originals', CODE_FILES)]:
        checksums = data[field]
        if (not isinstance(checksums, dict) or set(checksums) != set(names)
                or any(not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v) for v in checksums.values())):
            raise RuntimeError('Dependency code fingerprints are incomplete')
        if field != 'originals':
            for name, checksum in checksums.items():
                if hashlib.sha256((root / name).read_bytes()).hexdigest() != checksum:
                    raise RuntimeError('Dependency code fingerprint changed')


def _verify_overlay(root: Path) -> None:
    values = [os.environ.get(key, '') for key in (
        'SPARTAN_HERMES_DEPENDENCY_CODE_MANIFEST', 'SPARTAN_HERMES_DEPENDENCY_CODE_IMAGE',
        'SPARTAN_HERMES_DEPENDENCY_CODE_SHA256')]
    if not any(values):
        return
    if values[0] != CODE_MANIFEST:
        raise RuntimeError('Invalid dependency code manifest location')
    verify_code_manifest(root, Path(values[0]), values[1], values[2])


def _protected_validator(root: Path, validator: Path) -> None:
    for path in (root, root / 'patches', validator):
        info = path.lstat()
        readonly_code_mount = path == validator and bool(os.statvfs(path).f_flag & os.ST_RDONLY)
        if path.is_symlink() or (info.st_uid != 0 and not readonly_code_mount) or info.st_mode & 0o022:
            raise RuntimeError('Image dependency validator must be root-controlled')
    if not stat.S_ISREG(validator.stat().st_mode):
        raise RuntimeError('Image dependency validator must be a regular file')


def image_only_startup(root: Path) -> bool:
    """Replace the generation verdict only after fresh, isolated image admission.

    SystemExit deliberately escapes the native callers' best-effort Exception
    handlers. No facts are adopted or stamped, and PM still checks its tools.
    """
    mode = os.environ.get('SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY', '0')
    if mode == '0':
        return False
    try:
        if mode != '1':
            raise RuntimeError('Invalid image dependency mode')
        root = Path(root).resolve()
        validator = Path(__file__).resolve()
        if root != validator.parents[1] or (root / '.git').exists():
            raise RuntimeError('Image dependency mode requires its sealed installation')
        _protected_validator(root, validator)
        _verify_overlay(root)
        from hermes_cli.steward import read_install_stamp
        from pm.environments import selected_venv

        stamp = read_install_stamp(root)
        if stamp.get('distribution') != 'docker' or stamp.get('updateMechanism') != 'external':
            raise RuntimeError('Image dependency mode requires externally managed Docker')
        baked = (root / '.venv').resolve()
        if (Path(sys.prefix).resolve() != baked or sys.prefix == sys.base_prefix
                or selected_venv(root).resolve() != baked):
            raise RuntimeError('Image dependency mode requires the executing baked interpreter')
        result = subprocess.run(
            [str(baked / 'bin/python'), '-I', '-B', str(validator)],
            cwd=root, capture_output=True, timeout=90, check=False,
        )
        if result.returncode:
            raise RuntimeError('Image dependency admission failed')
    except Exception as exc:
        # Child output/config paths can contain private data. Report type only.
        print(f'FATAL: image-only dependency admission rejected ({type(exc).__name__})', file=sys.stderr)
        raise SystemExit(78) from None
    import logging
    logging.getLogger(__name__).info(
        'Image dependencies validated; PM generation fingerprint not applied in image-only mode'
    )
    return True


def dependency_inputs(root: Path, directories) -> tuple:
    """Fingerprint only dependency declarations, selection and profile config."""
    from pm.plugins_state import dependency_homes
    from pm.plugin_declarations import read_python_declaration
    from pm.environments import runtime_facts_path, selected_venv

    homes = sorted(dependency_homes())
    inputs = {root / 'enabled-features.json', root / 'uv.lock', root / 'pyproject.toml',
              runtime_facts_path(root), *(home / 'config.yaml' for home in homes)}
    for directory in directories:
        inputs.update(read_python_declaration(directory).files)
    rows = []
    for path in sorted(inputs):
        if path.exists():
            info = path.stat()
            rows.append((str(path), str(path.resolve()), info.st_dev, info.st_ino,
                         info.st_mtime_ns, hashlib.sha256(path.read_bytes()).digest()))
        else:
            rows.append((str(path), None))
    return (tuple(map(str, homes)), tuple(map(str, directories)),
            str(selected_venv(root).resolve()), tuple(rows))


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
    if not specs:
        return
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
    _verify_overlay(root)
    venv = root / '.venv'
    if Path(sys.prefix).resolve() != venv.resolve() or sys.prefix == sys.base_prefix:
        raise RuntimeError('Image-only validation must run in the baked image virtual environment')
    from pm.environments import runtime_facts_path, selected_venv
    from pm.features import installed_extras
    from pm.recovery import validate_environment
    from pm.plugin_declarations import read_python_declaration
    from pm.workspace import enabled_plugin_dirs

    if selected_venv(root).resolve() != venv.resolve():
        raise RuntimeError('Image-only validation refuses a selected dependency generation')
    directories = tuple(sorted(enabled_plugin_dirs(skip_invalid_secondary=False)))
    inputs = dependency_inputs(root, directories)
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
    for directory in directories:
        declaration = read_python_declaration(directory)
        if declaration.external:
            continue
        if declaration.python_error('.'.join(map(str, sys.version_info[:3]))):
            raise RuntimeError('Plugin requires an incompatible image Python version')
        require_installed(declaration.requirements)
    # No state writes, no fallback publication, no generation collection.
    after_directories = tuple(sorted(enabled_plugin_dirs(skip_invalid_secondary=False)))
    if read() != before or dependency_inputs(root, after_directories) != inputs:
        raise RuntimeError('Dependency inputs changed during image-only validation')
    print('[stage2] dependency environment: validated baked image (automatic sync and collection disabled)')


if __name__ == '__main__':
    try:
        project = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(project))
        validate(project)
    except Exception as exc:
        print(f'[stage2] FATAL: image-only dependency validation failed: {exc}', file=sys.stderr)
        raise SystemExit(1) from None
