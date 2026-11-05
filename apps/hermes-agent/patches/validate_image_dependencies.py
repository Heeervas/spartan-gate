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

CODE_FILES = ('hermes_cli/venv_sync.py', 'patches/validate_image_dependencies.py', 'docker/stage2-hook.sh')
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
    verify_manifest_files(root, data)


def verify_manifest_files(root: Path, data: dict) -> None:
    import re
    names = CODE_FILES if 'docker/stage2-hook.sh' in data['files'] else CODE_FILES[:2]
    for field, names in [('files', names), ('anchors', CODE_ANCHORS), ('originals', names)]:
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
    if (os.environ.get('SPARTAN_HERMES_MANAGED_DEPENDENCIES_VALIDATE_ONLY', '0') != '0'
            and set(json.loads(Path(values[0]).read_bytes())['files']) != set(CODE_FILES)):
        raise RuntimeError('Managed admission requires complete protected code')


def _protected_validator(root: Path, validator: Path) -> None:
    """Require a root-controlled admission file within the protected installation."""
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


def dependency_inputs(root: Path, directories, lock: Path | None = None) -> tuple:
    """Fingerprint only dependency declarations, selection and profile config."""
    from pm.plugins_state import dependency_homes
    from pm.plugin_declarations import read_python_declaration
    from pm.environments import runtime_facts_path, selected_venv

    homes = sorted(dependency_homes())
    inputs = {root / 'enabled-features.json', root / 'uv.lock', root / 'pyproject.toml',
              runtime_facts_path(root), *(home / 'config.yaml' for home in homes)}
    if lock is not None:
        inputs.add(lock)
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


def require_installed(specs, *, distribution=metadata.distribution, overrides=None) -> None:
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
        requirement = overridden_requirement(requirement, overrides)
        if requirement is None:
            return
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


def overridden_requirement(requirement, overrides):
    """Replace an active edge, retaining the extras requested by that edge."""
    from packaging.utils import canonicalize_name
    override = (overrides or {}).get(canonicalize_name(requirement.name))
    if override is None:
        return requirement
    # A false override marker removes the edge; it does not restore the old bound.
    if override.marker and not override.marker.evaluate():
        return None
    from copy import copy
    replacement = copy(override)
    replacement.extras = set(requirement.extras)
    return replacement


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


def startup_mode() -> str:
    image = os.environ.get('SPARTAN_HERMES_IMAGE_DEPENDENCIES_ONLY', '0')
    managed = os.environ.get('SPARTAN_HERMES_MANAGED_DEPENDENCIES_VALIDATE_ONLY', '0')
    if image not in ('0', '1') or managed not in ('0', '1') or (image == managed == '1'):
        raise RuntimeError('Invalid or conflicting dependency admission modes')
    return 'image' if image == '1' else 'managed' if managed == '1' else 'native'


def management_command(argv) -> bool:
    from hermes_cli._parser import command_argv
    return command_argv(argv)[:1] == ['pm']


def startup_admission(root: Path, argv=()) -> bool:
    """Fatal boundary shared by pre-recovery launch preparation and diagnostics."""
    try:
        mode = startup_mode()
        if mode == 'native':
            return False
        if mode == 'image':
            return image_only_startup(root)
        if management_command(argv):
            return False  # Native explicit management owns its repair/install policy.
        root = Path(root).resolve()
        validator = Path(__file__).resolve()
        if root != validator.parents[1] or (root / '.git').exists():
            raise RuntimeError('Managed admission requires its sealed installation')
        _protected_validator(root, validator)
        _verify_overlay(root)
        from hermes_cli.steward import read_install_stamp
        stamp = read_install_stamp(root)
        if stamp.get('distribution') != 'docker' or stamp.get('updateMechanism') != 'external':
            raise RuntimeError('Managed admission requires externally managed Docker')
        result = subprocess.run([str(root / '.venv/bin/python'), '-I', '-B', str(validator)],
                                cwd=root, capture_output=True, timeout=90, check=False)
        if result.returncode:
            raise RuntimeError('Managed dependency admission failed')
        return True
    except Exception as exc:
        print(f'FATAL: dependency admission rejected ({type(exc).__name__})', file=sys.stderr)
        raise SystemExit(78) from None


def managed_selection(root: Path):
    """Require genuine native facts and a generation-local lock; never fallback."""
    from pm.environments import install_state_dir, runtime_facts_path, selected_venv
    from pm.lock import Facts
    state = install_state_dir(root)
    markers = (state / 'publication.json', state / '.repair-incomplete',
               state / 'source-completion-pending', root / '.update-incomplete',
               root / '.lazy-refresh-incomplete')
    if any(path.exists() or path.is_symlink() for path in markers):
        raise RuntimeError('Pending dependency transaction requires explicit management')
    path = runtime_facts_path(root)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Managed admission requires recorded facts')
    fact = Facts(path, strict=True).get('venv')
    if not isinstance(fact, dict):
        raise RuntimeError('Missing managed selection')
    extras = managed_record_extras(fact)
    selected = selected_venv(root).resolve()  # Native ownership and pyvenv checks.
    if selected != Path(fact['environment']).resolve():
        raise RuntimeError('Managed selection fell back')
    lock = managed_lock(state, selected, fact['resolved_lock'])
    return selected, lock, extras


def managed_record_extras(fact: dict) -> list[str]:
    # Reuse strict facts shape, but require (rather than prohibit) native generation metadata.
    extras = recorded_extras({'schema': 1, 'packages': {'venv': {
        key: value for key, value in fact.items() if key not in ('environment', 'resolved_lock')}}})
    for key in ('environment', 'resolved_lock'):
        if not isinstance(fact.get(key), str) or not Path(fact[key]).is_absolute():
            raise RuntimeError('Incomplete managed selection')
    return extras


def managed_lock(state: Path, selected: Path, recorded: str) -> Path:
    lock = Path(recorded)
    generation = selected.parent
    if (generation.parent != (state / 'environments').resolve() or selected.name != 'venv'
            or lock.is_symlink() or not lock.is_file()
            or lock.resolve() != generation / 'workspace/uv.lock'):
        raise RuntimeError('Managed selection requires a generation-local lock')
    import tomllib
    document = tomllib.loads(lock.read_text(encoding='utf-8-sig'))
    if type(document.get('version')) is not int or not isinstance(document.get('package'), list):
        raise RuntimeError('Invalid recorded dependency lock')
    return lock


def python_abi() -> tuple:
    import sysconfig
    return (sys.implementation.name, *sys.version_info[:2], sysconfig.get_config_var('SOABI'))


def core_requirements(root: Path, extras) -> list[str]:
    import tomllib
    from packaging.specifiers import SpecifierSet
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8-sig'))['project']
    if not SpecifierSet(project['requires-python']).contains('.'.join(map(str, sys.version_info[:3])), prereleases=True):
        raise RuntimeError('Core requires incompatible Python')
    optional = project.get('optional-dependencies', {})
    if len(set(extras)) != len(extras) or any(extra not in optional for extra in extras):
        raise RuntimeError('Unknown or duplicate recorded extra')
    groups = [project.get('dependencies', []), *(optional[extra] for extra in extras)]
    if any(not isinstance(group, list) or any(not isinstance(spec, str) for spec in group) for group in groups):
        raise RuntimeError('Malformed core dependency declaration')
    return [spec for group in groups for spec in group]


def core_distribution(root: Path):
    """The running checkout, not stale editable metadata, owns Hermes extras."""
    import tomllib
    from packaging.requirements import Requirement
    from types import SimpleNamespace
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8-sig'))['project']
    requirements = list(project.get('dependencies', []))
    for extra, specs in project.get('optional-dependencies', {}).items():
        for spec in specs:
            req = Requirement(spec)
            from packaging.markers import Marker
            marker = f'({req.marker}) and ' if req.marker else ''
            req.marker = Marker(marker + f'extra == "{extra}"')
            requirements.append(str(req))
    def distribution(name):
        from packaging.utils import canonicalize_name
        if canonicalize_name(name) == 'hermes-agent':
            return SimpleNamespace(version=project['version'], requires=requirements)
        return metadata.distribution(name)
    return distribution


def core_override_requirement(spec):
    """Bounded uv override subset: index constraints and target-platform markers."""
    import re
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    requirement = Requirement(spec)
    if requirement.url or requirement.extras or canonicalize_name(requirement.name) == 'hermes-agent':
        raise RuntimeError('Unsupported core dependency override')
    if requirement.marker and re.search(r'\b(extra|extras|dependency_groups)\b', str(requirement.marker)):
        raise RuntimeError('Extra-context core overrides are unsupported')
    if not requirement.specifier and (requirement.marker is None or requirement.marker.evaluate()):
        raise RuntimeError('Active unbounded core dependency override')
    return requirement


def core_overrides(root: Path):
    import tomllib
    from packaging.utils import canonicalize_name
    project = root / 'pyproject.toml'
    # Same root/regular-file/RO authority as the admission code, never a plugin or uv config.
    _protected_validator(root, project)
    document = tomllib.loads(project.read_text(encoding='utf-8-sig'))
    specs = document.get('tool', {}).get('uv', {}).get('override-dependencies', [])
    if not isinstance(specs, list) or any(not isinstance(spec, str) for spec in specs):
        raise RuntimeError('Malformed core dependency overrides')
    overrides = {}
    for spec in specs:
        requirement = core_override_requirement(spec)
        name = canonicalize_name(requirement.name)
        if name in overrides:
            raise RuntimeError('Ambiguous duplicate core dependency override')
        overrides[name] = requirement
    return overrides


def managed_probe(root: Path, launcher_abi) -> None:
    from pm.plugin_declarations import read_python_declaration
    from pm.workspace import enabled_plugin_dirs
    selected, lock, extras = managed_selection(root)
    if Path(sys.prefix).resolve() != selected or tuple(launcher_abi) != python_abi():
        raise RuntimeError('Selected dependency interpreter ABI differs from launcher')
    directories = tuple(sorted(enabled_plugin_dirs(skip_invalid_secondary=False)))
    before = dependency_inputs(root, directories, lock)
    distribution = core_distribution(root)
    overrides = core_overrides(root)
    require_installed(core_requirements(root, extras), distribution=distribution, overrides=overrides)
    for directory in directories:
        declaration = read_python_declaration(directory)
        if declaration.external:
            continue
        if declaration.python_error('.'.join(map(str, sys.version_info[:3]))):
            raise RuntimeError('Plugin requires incompatible Python')
        require_installed(declaration.requirements, distribution=distribution, overrides=overrides)
    if dependency_inputs(root, tuple(sorted(enabled_plugin_dirs(skip_invalid_secondary=False))), lock) != before:
        raise RuntimeError('Dependency inputs changed during managed admission')


def validate_managed(root: Path) -> None:
    from pm.recovery import validate_environment
    from pm.workspace import enabled_plugin_dirs
    selected, lock, _extras = managed_selection(root)
    before = dependency_inputs(root, tuple(sorted(enabled_plugin_dirs(skip_invalid_secondary=False))), lock)
    result = subprocess.run([str(selected / 'bin/python'), '-I', '-B', str(Path(__file__).resolve()),
                             '--managed-probe', json.dumps(python_abi())], cwd=root,
                            capture_output=True, timeout=60, check=False,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    if result.returncode:
        raise RuntimeError('Selected dependency closure or ABI validation failed')
    validate_environment(selected / 'bin/python', env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), cwd=root)
    managed_selection(root)  # Pending recovery/publication must still be absent.
    if dependency_inputs(root, tuple(sorted(enabled_plugin_dirs(skip_invalid_secondary=False))), lock) != before:
        raise RuntimeError('Dependency inputs changed during managed admission')
    print('[stage2] selected dependencies validated; no sync, fallback or collection')


if __name__ == '__main__':
    try:
        project = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(project))
        _verify_overlay(project)
        if sys.argv[1:2] == ['--managed-probe']:
            managed_probe(project, json.loads(sys.argv[2]))
        elif startup_mode() == 'managed':
            validate_managed(project)
        else:
            validate(project)
    except Exception as exc:
        print(f'[stage2] FATAL: dependency validation failed ({type(exc).__name__})', file=sys.stderr)
        raise SystemExit(1) from None
