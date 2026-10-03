#!/usr/bin/env python3
"""Prepare/check an image-pinned, read-only Hermes browser policy overlay."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'private/compose.hermes-browser-policy.yml'
ANCHORS = ('hermes_cli/config.py', 'agent/secret_scope.py', 'hermes_constants.py',
           'tools/browser_tool.py', 'pyproject.toml')
SUPPORT = {'/opt/hermes/bootstrap/spartan_browser_policy.py': 'spartan_browser_policy.py',
           '/opt/hermes/spartan_browser_policy.py': 'spartan_browser_policy.py',
           '/etc/cont-init.d/0195-spartan-browser-policy': 'spartan-browser-policy-init.sh'}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(args: list[str]) -> bytes:
    result = subprocess.run(args, capture_output=True, check=False)
    if result.returncode:
        # Captured output can include private Compose values; never print it.
        raise RuntimeError(f'{args[0]} preflight failed')
    return result.stdout


def patcher():
    path = ROOT / 'apps/hermes-agent/patches/patch_camofox_managed_policy.py'
    spec = importlib.util.spec_from_file_location('spartan_browser_patcher', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError('Compose overlay must be a mapping')
    return data


def expected_manifest(config: dict) -> tuple[str, Path]:
    service = config['services']['hermes']
    image = service['image']
    env = service['environment']
    if env.get('SPARTAN_HERMES_CODE_IMAGE') != image or not isinstance(image, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Browser overlay image pin is invalid')
    if env.get('SPARTAN_HERMES_CODE_MANIFEST') != '/run/spartan-browser-code/manifest.json':
        raise ValueError('Browser overlay manifest environment is invalid')
    if any(m.get('type') != 'bind' for m in service['volumes']):
        raise ValueError('Browser overlay code mounts must use bind mounts')
    targets = [m['target'] for m in service['volumes']]
    if len(targets) != len(set(targets)):
        raise ValueError('Browser overlay contains duplicate code mounts')
    matches = [m for m in service['volumes'] if m['target'] == '/run/spartan-browser-code/manifest.json']
    if len(matches) != 1 or not matches[0]['read_only'] or matches[0]['bind'].get('create_host_path') is not False:
        raise ValueError('Browser overlay manifest mount is invalid')
    return image, Path(matches[0]['source'])


def check(image_ref: str) -> None:
    if not OVERLAY.exists():
        return
    config = read_yaml(OVERLAY)
    image, path = expected_manifest(config)
    actual = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_ref]).decode().strip()
    if actual != image:
        raise ValueError('Hermes image changed: regenerate/remove the browser overlay before starting or rebuilding Hermes')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Browser overlay manifest must be a bounded regular file')
    manifest = json.loads(path.read_text())
    files = {'tools/' + name for name in patcher().TARGETS}
    required = {'files': files, 'originals': files,
                'anchors': set(ANCHORS), 'support': set(SUPPORT)}
    if (not isinstance(manifest, dict) or type(manifest.get('schema')) is not int
            or manifest['schema'] != 1 or manifest.get('image') != image):
        raise ValueError('Browser overlay manifest does not match its schema or Compose pin')
    for field, names in required.items():
        values = manifest.get(field)
        if (not isinstance(values, dict) or set(values) != names
                or any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in values.values())):
            raise ValueError('Browser overlay manifest lacks valid required fingerprints')
    expected_targets = {f'/opt/hermes/{name}': checksum for name, checksum in manifest['files'].items()}
    expected_targets.update(manifest['support'])
    mounted = {m['target']: m for m in config['services']['hermes']['volumes']}
    for target, checksum in expected_targets.items():
        mount = mounted.get(target)
        if not mount or not mount['read_only'] or mount['bind'].get('create_host_path') is not False:
            raise ValueError('Browser overlay lacks a required read-only code mount')
        source = Path(mount['source'])
        if source.is_symlink() or not source.is_file() or sha(source.read_bytes()) != checksum:
            raise ValueError('Browser overlay code fingerprint changed; regenerate it before starting Hermes')


def prepare(container: str) -> None:
    if shutil.disk_usage(ROOT).free < 5 * 1024 ** 3:
        raise ValueError('At least 5 GiB free disk space is required')
    module = patcher()
    image = run(['docker', 'inspect', '--format', '{{.Image}}', container]).decode().strip()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Running Hermes image is not an immutable image ID')
    original, patched = {}, {}
    for name in module.TARGETS:
        original[name] = run(['docker', 'exec', container, 'cat', '/opt/hermes/tools/' + name])
        patched[name] = module.patch_source(name, original[name].decode()).encode()
    anchors = {name: sha(run(['docker', 'exec', container, 'cat', '/opt/hermes/' + name])) for name in ANCHORS}
    supports = {target: ROOT / 'apps/hermes-agent' / name for target, name in SUPPORT.items()}
    manifest = {'schema': 1, 'image': image,
                'files': {'tools/' + name: sha(value) for name, value in patched.items()},
                'originals': {'tools/' + name: sha(value) for name, value in original.items()},
                'anchors': anchors, 'support': {name: sha(path.read_bytes()) for name, path in supports.items()}}
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    directory = ROOT / 'private/runtime/hermes-browser-policy' / (image[7:19] + '-' + sha(manifest_bytes)[:12])
    volumes = []
    for name in module.TARGETS:
        volumes.append({'type': 'bind', 'source': str(directory / name), 'target': '/opt/hermes/tools/' + name,
                        'read_only': True, 'bind': {'create_host_path': False}})
    for name, path in supports.items():
        volumes.append({'type': 'bind', 'source': str(path), 'target': name,
                        'read_only': True, 'bind': {'create_host_path': False}})
    volumes.append({'type': 'bind', 'source': str(directory / 'manifest.json'),
                    'target': '/run/spartan-browser-code/manifest.json', 'read_only': True,
                    'bind': {'create_host_path': False}})
    config = {'services': {'hermes': {'image': image, 'environment': {
        'SPARTAN_HERMES_CODE_IMAGE': image, 'SPARTAN_HERMES_CODE_MANIFEST': '/run/spartan-browser-code/manifest.json'},
        'volumes': volumes}}}
    overlay_bytes = yaml.safe_dump(config, sort_keys=False).encode()
    if OVERLAY.exists():
        expected_manifest(read_yaml(OVERLAY))  # Never overwrite an unrelated overlay.
    if directory.exists():
        expected = {**patched, 'manifest.json': manifest_bytes}
        if directory.is_symlink() or any((directory / name).read_bytes() != value for name, value in expected.items()):
            raise ValueError('Existing browser code bundle is incompatible')

    # Rendering and all source transformations complete before publishing files.
    with tempfile.TemporaryDirectory(prefix='spartan-browser-overlay-') as raw:
        candidate = Path(raw) / 'compose.yml'
        candidate.write_bytes(overlay_bytes)
        args = ['docker', 'compose', '-f', str(ROOT / 'infra/compose/compose.yml')]
        local = ROOT / 'private/compose.local.yml'
        if local.exists():
            args += ['-f', str(local)]
        args += ['-f', str(candidate)]
        env_file = ROOT / 'private/env/local.env'
        if not env_file.exists():
            env_file = ROOT / '.env'
        if env_file.exists():
            args += ['--env-file', str(env_file)]
        run([*args, 'config', '--quiet'])
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not directory.exists():
        staged = Path(tempfile.mkdtemp(prefix='.bundle-', dir=directory.parent))
        try:
            (staged / 'original').mkdir(mode=0o700)
            for name, value in patched.items():
                (staged / name).write_bytes(value)
                (staged / name).chmod(0o444)
                (staged / 'original' / name).write_bytes(original[name])
                (staged / 'original' / name).chmod(0o400)
            (staged / 'manifest.json').write_bytes(manifest_bytes)
            (staged / 'manifest.json').chmod(0o444)
            os.rename(staged, directory)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
    fd, temporary = tempfile.mkstemp(prefix='.browser-overlay-', dir=OVERLAY.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(overlay_bytes)
        os.replace(temporary, OVERLAY)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('Prepared an image-pinned Hermes browser overlay; no services were started')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--container', default='spartan_gate_hermes')
    p = sub.add_parser('check')
    p.add_argument('--image-ref', default='spartan-gate-hermes:latest')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.container)
    else:
        check(args.image_ref)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Values from Compose/.env/source are deliberately omitted.
        detail = str(exc) if type(exc) in (ValueError, RuntimeError) else type(exc).__name__
        print(f'Hermes browser overlay rejected: {detail}', file=__import__('sys').stderr)
        raise SystemExit(1) from None
