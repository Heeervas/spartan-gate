#!/usr/bin/env python3
"""Prepare/check a read-only, image-pinned dependency admission adaptation."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'private/compose.hermes-image-dependencies.yml'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'apps/hermes-agent/patches' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load('validate_image_dependencies')
patcher = load('patch_stage2_image_dependencies')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(args):
    result = subprocess.run(args, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f'{args[0]} preflight failed (private output withheld)')
    return result.stdout


def overlay_data():
    service = yaml.safe_load(OVERLAY.read_text())['services']['hermes']
    image = service['image']
    env = service['environment']
    if (not re.fullmatch(r'sha256:[0-9a-f]{64}', image)
            or env.get('SPARTAN_HERMES_DEPENDENCY_CODE_IMAGE') != image
            or env.get('SPARTAN_HERMES_DEPENDENCY_CODE_MANIFEST') != validator.CODE_MANIFEST):
        raise ValueError('Invalid dependency overlay pin')
    mounts = service['volumes']
    expected = {validator.CODE_MANIFEST, *('/opt/hermes/' + n for n in validator.CODE_FILES)}
    if (len(mounts) != len(expected) or {m['target'] for m in mounts} != expected
            or any(m.get('type') != 'bind' or m.get('read_only') is not True
                   or m.get('bind', {}).get('create_host_path') is not False for m in mounts)):
        raise ValueError('Dependency overlay must contain exactly its read-only code mounts')
    return image, env, {m['target']: Path(m['source']) for m in mounts}


def compose_args():
    args = ['docker', 'compose', '-f', str(ROOT / 'infra/compose/compose.yml')]
    for name in ('compose.local.yml', 'compose.hermes-browser-policy.yml', 'compose.hermes-fast-mode.yml'):
        if (ROOT / 'private' / name).exists():
            args += ['-f', str(ROOT / 'private' / name)]
    for env_file in (ROOT / 'private/env/local.env', ROOT / '.env'):
        if env_file.exists():
            args += ['--env-file', str(env_file)]
            break
    return args


def check(image_ref=None):
    if not OVERLAY.exists():
        return
    if image_ref is None:
        # Render without this overlay so its immutable pin cannot mask a new
        # image selected by the operator's existing Compose overrides.
        image_ref = json.loads(run(compose_args() + ['config', '--format', 'json']))['services']['hermes']['image']
    image, env, mounts = overlay_data()
    actual = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_ref]).decode().strip()
    if actual != image:
        raise ValueError('Hermes image changed; remove/regenerate dependency overlay before starting')
    manifest = mounts[validator.CODE_MANIFEST]
    bundle = manifest.parent
    for name in validator.CODE_FILES:
        path = mounts['/opt/hermes/' + name]
        if path != bundle / name or path.is_symlink() or path.stat().st_mode & 0o022:
            raise ValueError('Invalid dependency code source')
    validator.verify_code_manifest(bundle, manifest, image, env['SPARTAN_HERMES_DEPENDENCY_CODE_SHA256'])
    data = json.loads(manifest.read_bytes())
    for name, expected in data['originals'].items():
        if digest((bundle / 'original' / name).read_bytes()) != expected:
            raise ValueError('Dependency rollback source changed')


def prepare(container):
    if shutil.disk_usage(ROOT).free < 5_000_000_000:
        raise ValueError('At least 5 GB free is required; no image will be built')
    identity_command = ['docker', 'inspect', '--format', '{{.Id}} {{.Image}} {{.State.StartedAt}}', container]
    identity = run(identity_command).decode().strip()
    container_id, image, _started = identity.split()
    previous_overlay = OVERLAY.read_bytes() if OVERLAY.exists() else None
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
        raise ValueError('Running Hermes image is not immutable')
    if OVERLAY.exists():
        check(image)
    originals = {name: run(['docker', 'exec', container_id, 'cat', '/opt/hermes/' + name])
                 for name in validator.CODE_FILES}
    anchors = {name: run(['docker', 'exec', container_id, 'cat', '/opt/hermes/' + name])
               for name in validator.CODE_ANCHORS}
    files = {
        validator.CODE_FILES[0]: patcher.patch_runtime_source(originals[validator.CODE_FILES[0]].decode()).encode(),
        validator.CODE_FILES[1]: (ROOT / 'apps/hermes-agent/patches/validate_image_dependencies.py').read_bytes(),
    }
    for name, value in files.items():
        compile(value, name, 'exec')
    manifest = {'schema': 1, 'image': image,
                'files': {n: digest(v) for n, v in files.items()},
                'anchors': {n: digest(v) for n, v in anchors.items()},
                'originals': {n: digest(v) for n, v in originals.items()}}
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    bundle = ROOT / 'private/runtime/hermes-image-dependencies' / (image[7:19] + '-' + digest(raw)[:12])
    env = {'SPARTAN_HERMES_DEPENDENCY_CODE_IMAGE': image,
           'SPARTAN_HERMES_DEPENDENCY_CODE_MANIFEST': validator.CODE_MANIFEST,
           'SPARTAN_HERMES_DEPENDENCY_CODE_SHA256': digest(raw)}
    config = {'services': {'hermes': {'image': image, 'environment': env, 'volumes': [
        {'type': 'bind', 'source': str(bundle / name), 'target': '/opt/hermes/' + name,
         'read_only': True, 'bind': {'create_host_path': False}} for name in files] + [
        {'type': 'bind', 'source': str(bundle / 'manifest.json'), 'target': validator.CODE_MANIFEST,
         'read_only': True, 'bind': {'create_host_path': False}}]}}}
    overlay = yaml.safe_dump(config, sort_keys=False).encode()
    values = {**files, **anchors, **{'original/' + n: v for n, v in originals.items()}, 'manifest.json': raw}
    # Preflight the complete bundle and Compose before publishing either.
    with tempfile.TemporaryDirectory(prefix='hermes-image-admission-') as temporary:
        staged = Path(temporary)
        for name, value in values.items():
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        validator.verify_code_manifest(staged, staged / 'manifest.json', image, digest(raw))
        candidate = staged / 'compose.yml'
        candidate.write_bytes(overlay)
        run(compose_args() + ['-f', str(candidate), 'config', '--quiet'])
        if run(identity_command).decode().strip() != identity:
            raise ValueError('Hermes container changed during dependency overlay preflight')
        for name, original in {**originals, **anchors}.items():
            if run(['docker', 'exec', container_id, 'cat', '/opt/hermes/' + name]) != original:
                raise ValueError('Hermes code changed during dependency overlay preflight')
        if bundle.exists():
            if any((bundle / n).is_symlink() or (bundle / n).read_bytes() != v for n, v in values.items()):
                raise ValueError('Existing dependency bundle differs')
        else:
            bundle.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            scratch = Path(tempfile.mkdtemp(prefix='.prepare-', dir=bundle.parent))
            try:
                for name, value in values.items():
                    path = scratch / name
                    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    path.write_bytes(value)
                    path.chmod(0o444)
                os.replace(scratch, bundle)
            finally:
                if scratch.exists():
                    shutil.rmtree(scratch)
        if OVERLAY.exists():
            backup = bundle / 'previous-compose.yml'
            if not backup.exists():
                backup.write_bytes(OVERLAY.read_bytes())
                backup.chmod(0o600)
        fd, temporary = tempfile.mkstemp(prefix='.dependency-overlay-', dir=OVERLAY.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(overlay)
                stream.flush()
                os.fsync(stream.fileno())
            if (run(identity_command).decode().strip() != identity
                    or (OVERLAY.read_bytes() if OVERLAY.exists() else None) != previous_overlay):
                raise ValueError('Dependency overlay or container changed before publication')
            os.replace(temporary, OVERLAY)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    check(image)
    print('Prepared image-pinned dependency admission overlay; runtime activation still required')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check'])
    parser.add_argument('--container', default='spartan_gate_hermes')
    parser.add_argument('--image-ref', help='explicit image reference; default: effective Compose image before this overlay')
    args = parser.parse_args()
    prepare(args.container) if args.action == 'prepare' else check(args.image_ref)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Hermes dependency admission overlay preflight failed; private output withheld') from None
