#!/usr/bin/env python3
"""Prepare a separate, pinned upstream context; never download or build images."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

REVISION = 'e8c97320ac8691d4de92af49f98459f9ef9ddb08'
UPSTREAM_IMAGE = 'docker.io/nousresearch/hermes-agent:main@sha256:5edbc9c277d22f1b74f2707b23b77ebdd7f412d34511363a20df880004a42fde'
DOCKERFILE_SHA256 = '1aa1d2ab18688b340ec1290b684982e4e6eddf0b8686fa317bbc072f2ac23068'
APP = Path(__file__).resolve().parents[1]
EARLY_ANCHOR = 'COPY scripts/bundles/payload.py scripts/bundles/payload.py\n'
EARLY = '''COPY _spartan_browser_variant.py /opt/hermes/spartan_browser_variant.py
COPY _spartan_patch_camofox_only_variant.py /opt/hermes/patch_camofox_only_variant.py
RUN mkdir -p /etc/spartan && printf 'camofox-only\\n' > /etc/spartan/hermes-browser-mode && \\
    chmod 0444 /etc/spartan/hermes-browser-mode && \\
    python3 /opt/hermes/patch_camofox_only_variant.py /opt/hermes --helper /opt/hermes/spartan_browser_variant.py --pm-only
'''
ENSURE = '("uv", "chromium", "npm", "ffmpeg", "ripgrep")'
PROBE = '''    node --version; npm --version; \\
    browser_bin="$(python3 -c 'from pm import installed_package; print(installed_package("chromium").binary)')"; \\
    test -n "$browser_bin"; \\
    "$browser_bin" --version; \\
    mkdir -p /etc/hermes; \\
    printf '%s' "$browser_bin" > /etc/hermes/agent-browser-executable-path'''
COPY_SOURCE = 'COPY --link --chmod=a+rX,go-w . .\n'
LATE = 'RUN python3 /opt/hermes/patch_camofox_only_variant.py /opt/hermes --helper /opt/hermes/spartan_browser_variant.py\n'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def transformed(original):
    if digest(original.encode()) != DOCKERFILE_SHA256:
        restored = original.replace(EARLY_ANCHOR + EARLY, EARLY_ANCHOR)
        restored = restored.replace('("uv", "npm", "ffmpeg", "ripgrep")', ENSURE)
        restored = restored.replace('    node --version; npm --version\n', PROBE + '\n')
        restored = restored.replace(COPY_SOURCE + LATE, COPY_SOURCE)
        restored = restored.replace('ARG HERMES_BOT_DESKTOP=0\nRUN test "$HERMES_BOT_DESKTOP" = "0"\n', 'ARG HERMES_BOT_DESKTOP=0\n')
        if digest(restored.encode()) == DOCKERFILE_SHA256 and transformed(restored) == original:
            return original
        raise ValueError('unexpected upstream Dockerfile; refusing source drift')
    for anchor in (EARLY_ANCHOR, ENSURE, PROBE, COPY_SOURCE):
        if original.count(anchor) != 1:
            raise ValueError('upstream build anchor missing or ambiguous')
    source = original.replace(EARLY_ANCHOR, EARLY_ANCHOR + EARLY)
    source = source.replace(ENSURE, '("uv", "npm", "ffmpeg", "ripgrep")')
    source = source.replace(PROBE, '    node --version; npm --version')
    source = source.replace(COPY_SOURCE, COPY_SOURCE + LATE)
    # The optional desktop build would reintroduce distro Chromium.
    source = source.replace('ARG HERMES_BOT_DESKTOP=0\n',
        'ARG HERMES_BOT_DESKTOP=0\nRUN test "$HERMES_BOT_DESKTOP" = "0"\n')
    return source


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args])


def prepare(source, output):
    source = source.resolve()
    output = output.resolve()
    if output == source or source in output.parents:
        raise ValueError('output must be outside the upstream source checkout')
    if git(source, 'rev-parse', 'HEAD').decode().strip() != REVISION:
        raise ValueError('source checkout is not the pinned upstream revision')
    if git(source, 'status', '--porcelain', '--untracked-files=normal').strip():
        raise ValueError('source checkout must be clean')
    original = git(source, 'show', 'HEAD:Dockerfile').decode()
    dockerfile = transformed(original).encode()
    injected = {
        '_spartan_browser_variant.py': (APP / 'spartan_browser_variant.py').read_bytes(),
        '_spartan_patch_camofox_only_variant.py': (APP / 'patches/patch_camofox_only_variant.py').read_bytes(),
    }
    manifest = {
        'schema': 1, 'browser_mode': 'camofox-only', 'upstream_revision': REVISION,
        'upstream_image': UPSTREAM_IMAGE, 'upstream_dockerfile_sha256': DOCKERFILE_SHA256,
        'dockerfile_sha256': digest(dockerfile),
        'transformer_sha256': digest(Path(__file__).read_bytes()),
        'injected_sha256': {name: digest(data) for name, data in injected.items()},
    }
    expected = json.dumps(manifest, indent=2, sort_keys=True) + '\n'
    if output.exists():
        # An existing context is accepted only if every byte still matches.
        with tempfile.TemporaryDirectory(dir=output.parent) as check:
            candidate = Path(check) / 'context'
            prepare(source, candidate)
            def inventory(root):
                return {str(p.relative_to(root)): (p.lstat().st_mode,
                            ('link', str(p.readlink())) if p.is_symlink()
                            else ('file', digest(p.read_bytes())))
                        for p in root.rglob('*') if p.is_file() or p.is_symlink()}
            if inventory(output) != inventory(candidate):
                raise ValueError('existing prepared context differs; choose a new output')
        return manifest
    output.parent.mkdir(parents=True, exist_ok=True)
    archive = git(source, 'archive', '--format=tar', REVISION)
    with tempfile.TemporaryDirectory(prefix='.hermes-context-', dir=output.parent) as temp:
        target = Path(temp) / 'context'
        target.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(target, filter='data')
        # No writes to the original checkout; publication is one rename.
        (target / 'Dockerfile').write_bytes(dockerfile)
        for name, data in injected.items():
            (target / name).write_bytes(data)
        (target / 'spartan-variant-build.json').write_text(expected)
        (target / 'install-stamp.json').write_text(json.dumps({
            'schemaVersion': 2, 'commit': REVISION, 'distribution': 'docker',
            'source': 'spartan-camofox-only', 'updateMechanism': 'external',
        }, sort_keys=True) + '\n')
        target.rename(output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = prepare(args.source.resolve(), args.output.absolute())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'prepare failed: {exc}\n')
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
