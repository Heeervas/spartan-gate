#!/usr/bin/env python3
"""Opt-in disposable SDK/REST/files acceptance. Never uses production networks/data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

HUB = 'samanhappy/mcphub:1.1.14@sha256:700013d712b7e74750bb2ce05492688833f62a39472db5e88753557002735dab'


def docker(*args, check=True):
    return subprocess.run(['docker', *args], text=True, capture_output=True, check=check, timeout=90)


def mount(source, target, readonly=True):
    return ['--mount', f'type=bind,source={source},target={target}' + (',readonly' if readonly else '')]


def acceptance(tool_image):
    # Complete image/source preflight before allocating any Docker resource.
    repo = Path(__file__).resolve().parents[2]
    images = [docker('image', 'inspect', image, '--format', '{{.Id}}').stdout.strip() for image in (tool_image, HUB)]
    if shutil.disk_usage(repo).free < 5 * 1024**3:
        raise RuntimeError('5GB disk floor')
    root = Path(tempfile.mkdtemp(prefix='spartan-browser-acceptance-'))
    incoming, outgoing = root / 'incoming', root / 'outgoing'
    incoming.mkdir(mode=0o700)
    outgoing.mkdir(mode=0o700)
    policy = root / 'policy.json'
    import hashlib
    policy.write_text(json.dumps({'version': 1, 'clients': [{'principal': 'fixture', 'browserUser': 'fixture-browser',
        'tokenSha256': hashlib.sha256(('a' * 64).encode()).hexdigest(), 'capabilities': ['browser', 'upload']}]}))
    policy.chmod(0o600)
    name = 'spartan-browser-check-' + uuid.uuid4().hex[:12]
    names = [name + '-rest', name + '-sdk']
    network_created = False
    clean = True
    try:
        docker('network', 'create', '--internal', '--label', 'spartan.browser.fixture=' + name, name)
        network_created = True
        common = ['--pull', 'never', '--network', name, '--read-only', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges', '--security-opt', 'label=disable',
                  '--user', f'{os.getuid()}:{os.getgid()}', '--pids-limit', '64',
                  '--tmpfs', '/tmp:rw,nosuid,nodev,size=32m', '--label', 'spartan.browser.fixture=' + name]
        settings = {'POLICY_FILE': '/policy/policy.json', 'ALLOWED_HOSTS': 'tool-edge:18792',
                    'SEARCH_URL': 'http://fixture:18081', 'READER_URL': 'http://fixture:18081',
                    'CAMOFOX_URL': 'http://fixture:18081', 'CAMOFOX_ACCESS_KEY': 'synthetic-browser-key',
                    'TRANSFER_INCOMING_DIR': '/incoming', 'TRANSFER_UPLOAD_DIR': '/outgoing',
                    'CAMOFOX_UPLOAD_ROOT': '/browser/uploads', 'PYTHONDONTWRITEBYTECODE': '1'}
        environment = [part for key, value in settings.items() for part in ('--env', key + '=' + value)]
        docker('run', '-d', '--name', names[0], '--network-alias', 'tool-access', *common, '--memory', '128m',
               *environment, *mount(repo / 'infra/tool-access', '/app'), *mount(root, '/policy'),
               *mount(incoming, '/incoming'), *mount(outgoing, '/outgoing', False), images[0])
        result = docker('run', '--name', names[1], '--network-alias', 'fixture', *common, '--memory', '192m',
                        *mount(repo / 'infra/mcphub', '/fixture'), *mount(incoming, '/incoming', False),
                        *mount(outgoing, '/browser/uploads'), '--entrypoint', 'node', images[1], '/fixture/browser-acceptance.mjs', check=False)
        if result.returncode:
            rest_log = docker('logs', names[0], check=False)
            raise RuntimeError('fixture failed: ' + result.stdout[-4000:] + result.stderr[-2000:]
                               + '\nREST fixture: ' + rest_log.stderr[-3000:])
        print(result.stdout.strip())
    finally:
        for container in names:
            exists = docker('container', 'inspect', container, check=False)
            if exists.returncode == 0:
                clean = docker('rm', '-f', container, check=False).returncode == 0 and clean
        if network_created:
            clean = docker('network', 'rm', name, check=False).returncode == 0 and clean
        if clean:
            shutil.rmtree(root)
        else:
            raise RuntimeError(f'fixture cessation/cleanup incomplete; retain {root} and resource prefix {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool-image', required=True, help='Existing tool-access image with aiohttp; never pull/build')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--resource-approved', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print('Preflight only: two disposable containers, one internal network, synthetic files/keys; no browser or provider traffic.')
        return
    if not args.resource_approved:
        parser.error('--execute requires --resource-approved')
    acceptance(args.tool_image)


if __name__ == '__main__':
    main()
