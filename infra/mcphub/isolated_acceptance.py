#!/usr/bin/env python3
"""Explicit disposable pinned-image compatibility check; no production inputs.

Requires installed MCPHub, Caddy and Tinyproxy program images. No pulls, builds,
external retrieval or real credentials. Named fixture resources are removed in
finally. SELinux labeling is disabled only on disposable canaries (no canonical
source relabel); production mounts retain their ordinary shared labels.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 'samanhappy/mcphub:1.1.14@sha256:700013d712b7e74750bb2ce05492688833f62a39472db5e88753557002735dab'
SPEC = importlib.util.spec_from_file_location('provision', ROOT / 'scripts/configure_mcphub.py')
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SESSIONS = {}


def docker(*args):
    result = subprocess.run(['docker', *map(str, args)], capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Docker operation failed: ' + result.stderr[:2000])
    return (result.stdout + (result.stderr if args[0] == 'logs' else '')).strip()


def request(url, token=None, body=None):
    headers = {'Accept': 'application/json, text/event-stream'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if (url, token) in SESSIONS:
        headers['Mcp-Session-Id'] = SESSIONS[url, token]
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode()
    try:
        with HTTP.open(urllib.request.Request(url, data=data, headers=headers), timeout=8) as response:
            session = response.headers.get('Mcp-Session-Id')
            if session:
                SESSIONS[url, token] = session
            return response.status, response.read(262144).decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read(262144).decode()


def rpc(url, token, method, params=None):
    if (url, token) not in SESSIONS:
        status, raw = request(url, token, {'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
            'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                       'clientInfo': {'name': 'isolated-check', 'version': '1'}}})
        assert status == 200, ('initialize', status, raw[:300])
        request(url, token, {'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    status, raw = request(url, token, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
    assert status == 200, (method, status, raw[:300])
    if raw.startswith('event:') or raw.startswith('data:'):
        raw = next(line[6:] for line in raw.splitlines() if line.startswith('data: '))
    return json.loads(raw)


def wait_ready(url):
    for _ in range(50):
        try:
            if request(url)[0] == 200:
                return
        except (OSError, TimeoutError):
            pass
        time.sleep(0.2)
    raise RuntimeError('Disposable hub did not become healthy')


def check_mcp(base, token):
    url = base + '/mcp/agents'
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
    assert request(url, body=body)[0] == 401
    assert request(url, 'invalid-synthetic-key', body)[0] == 401
    assert request(base + '/api/settings', token)[0] in (401, 403)
    assert request(base + '/mcp', token, body)[0] in (401, 403, 404)
    return url


class Fixture:
    def __init__(self, root):
        self.root = root
        self.prefix = 'sg-mcphub-test-' + uuid.uuid4().hex[:10]
        self.containers = []
        self.networks = []
        self.internal, self.publishing = self.prefix + '-internal', self.prefix + '-publishing'
        self.owner = f'{os.getuid()}:{os.getgid()}'
        self.state = root / 'state'
        self.state.mkdir(mode=0o700)
        for name, data in provision.initial_state().items():
            (self.state / name).write_bytes(data)
            (self.state / name).chmod(0o600)
        self.token = json.loads((self.state / 'clients.json').read_text())['host-pi']
        self.config = self.state / 'mcp_settings.json'

    def start(self, role, image, network, options, command):
        name = self.prefix + '-' + role
        self.containers.append(name)
        docker('run', '-d', '--name', name, '--network', network, '--read-only',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
               '--security-opt', 'label=disable', '--user', self.owner,
               '--memory', '512m', '--pids-limit', '128', '--tmpfs', '/tmp:rw,size=67108864',
               *options, image, *command)
        return name

    def start_upstream(self, proxy_image):
        for name in (self.internal, self.publishing):
            docker('network', 'create', *(['--internal'] if name == self.internal else []), name)
            self.networks.append(name)
        self.backend = self.start('backend', IMAGE, self.publishing,
            ['--entrypoint', 'node', '-v', f'{ROOT}/infra/mcphub/fixture-backend.mjs:/fixture.mjs:ro'], ['/fixture.mjs'])
        self.backend_ip = json.loads(docker('inspect', self.backend))[0]['NetworkSettings']['Networks'][self.publishing]['IPAddress']
        (self.root / 'filter').write_text('^allowed\\.fixture$\n')
        (self.root / 'tinyproxy.conf').write_text(
            'Port 8888\nListen 0.0.0.0\nTimeout 15\nMaxClients 16\n'
            'LogFile "/tmp/tinyproxy.log"\nPidFile "/tmp/tinyproxy.pid"\n'
            'Filter "/fixture/filter"\nFilterDefaultDeny Yes\nConnectPort 8080\n')
        proxy = self.start('proxy', proxy_image, self.internal,
            ['--entrypoint', '/usr/bin/tinyproxy', '--network-alias', 'proxy',
             '--add-host', f'allowed.fixture:{self.backend_ip}', '--add-host', f'denied.fixture:{self.backend_ip}',
             '-v', f'{self.root}:/fixture:ro'], ['-d', '-c', '/fixture/tinyproxy.conf'])
        docker('network', 'connect', self.publishing, proxy)

    def start_hub(self, caddy_image):
        self.hub = self.start('hub', IMAGE, self.internal,
            ['--network-alias', 'mcphub', '--entrypoint', '/bin/sh', '-v', f'{self.state}:/state',
             '-v', f'{ROOT}/infra/mcphub/entrypoint.sh:/entrypoint.sh:ro',
             '-e', 'HOME=/state/home', '-e', 'MCPHUB_SETTING_PATH=/state/mcp_settings.json',
             '-e', 'USE_DB=false', '-e', 'NODE_ENV=production',
             '-e', 'HTTP_PROXY=http://proxy:8888', '-e', 'HTTPS_PROXY=http://proxy:8888',
             '-e', 'NO_PROXY=localhost,127.0.0.1,::1', '--log-driver', 'none'], ['/entrypoint.sh'])
        (self.root / 'Caddyfile').write_text(' {\n admin off\n auto_https off\n log default {\n output discard\n }\n}\n'
            + (ROOT / 'infra/caddy/route.d/mcphub.caddy').read_text())
        edge = self.start('edge', caddy_image, self.publishing,
            ['--cap-add', 'NET_BIND_SERVICE', '--tmpfs', '/config/caddy', '--tmpfs', '/data/caddy',
             '-p', '127.0.0.1::18793', '-v', f'{self.root}/Caddyfile:/etc/caddy/Caddyfile:ro'],
            ['caddy', 'run', '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
        docker('network', 'connect', self.internal, edge)
        port = docker('port', edge, '18793/tcp').split(':')[-1]
        self.base = 'http://127.0.0.1:' + port
        wait_ready(self.base + '/health')

    def save_restart(self, settings):
        self.config.write_bytes(provision.encode(settings))
        docker('restart', self.hub)
        SESSIONS.clear()
        wait_ready(self.base + '/health')

    def cleanup(self):
        for name in reversed(self.containers):
            docker('rm', '-f', name)
        for name in reversed(self.networks):
            docker('network', 'rm', name)
        print('CLEANUP owned containers/networks removed; temporary synthetic state removed on exit')


def check_proxy(fixture):
    # The image's actual fetch wrapper, then raw fetch without proxy. Target is on
    # another Docker bridge: same target must work through proxy and fail directly.
    probe = """import {createFetchWithProxy} from '/app/dist/services/proxy.js';
const f=createFetchWithProxy({httpProxy:process.env.HTTP_PROXY});
const a=await f('http://allowed.fixture:8080/'); if(await a.text()!=='proxy-fixture-ok')throw Error('allow');
let denied=false;try{const b=await f('http://denied.fixture:8080/');denied=b.status>=400}catch{denied=true}if(!denied)throw Error('deny');
let blocked=false;try{await fetch(process.argv[1],{signal:AbortSignal.timeout(1500)})}catch{blocked=true}if(!blocked)throw Error('direct');
console.log('PASS actual native fetch proxy allow/deny and direct rejection');"""
    print(docker('exec', fixture.hub, 'node', '--input-type=module', '-e', probe, f'http://{fixture.backend_ip}:8080/'))


def check_tools(fixture, url):
    settings = json.loads(fixture.config.read_text())
    server_id = 'aa8b6e81-5841-4c50-b8df-647cf7a69dce'
    settings['mcpServers']['fixture'] = {'id': server_id, 'owner': 'admin', 'enabled': True,
        'type': 'streamable-http', 'url': 'http://allowed.fixture:8080/mcp'}
    settings['groups'][0]['servers'] = ['fixture']
    fixture.save_restart(settings)
    assert rpc(url, fixture.token, 'tools/list')['result']['tools'] == []
    denied = rpc(url, fixture.token, 'tools/call', {'name': 'fixture-unlisted', 'arguments': {}})
    assert 'error' in denied or denied.get('result', {}).get('isError'), denied
    print('PASS onboarding closed policy denies execution after first backend is enabled')
    settings = json.loads(fixture.config.read_text())
    settings['systemConfig']['toolPolicies'].append({'id': 'fixture-grant', 'effect': 'allow',
        'groupId': provision.GROUP_ID, 'serverId': server_id, 'tool': 'allowed'})
    fixture.save_restart(settings)
    tools = rpc(url, fixture.token, 'tools/list')['result']['tools']
    assert [tool['name'] for tool in tools] == ['fixture-allowed'], tools
    allowed = rpc(url, fixture.token, 'tools/call', {'name': 'fixture-allowed', 'arguments': {}})
    assert allowed['result']['content'][0]['text'] == 'inert-allowed', allowed
    denied = rpc(url, fixture.token, 'tools/call', {'name': 'fixture-unlisted', 'arguments': {}})
    assert 'error' in denied or denied.get('result', {}).get('isError'), denied
    ledger = docker('exec', fixture.backend, 'node', '-e',
        "fetch('http://127.0.0.1:8080/executions').then(r=>r.text()).then(console.log)")
    assert json.loads(ledger) == ['allowed'], ledger
    print('PASS real HTTP MCP backend through proxy, exact tool grant, execution denial, restart persistence')
    settings = json.loads(fixture.config.read_text())
    next(k for k in settings['bearerKeys'] if k['name'] == 'host-pi')['enabled'] = False
    fixture.config.write_bytes(provision.encode(settings))
    assert request(url, fixture.token, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})[0] == 401
    print('PASS revoked key rejected')


def run(args):
    for image in (IMAGE, args.caddy_image, args.proxy_image):
        print('program image:', docker('image', 'inspect', image, '--format', '{{.Id}}'))
    if not args.execute:
        print('Preflight only; use --execute with explicit isolated-operation authority')
        return
    with tempfile.TemporaryDirectory(prefix='sg-mcphub-fixture-') as directory:
        fixture = Fixture(Path(directory))
        try:
            fixture.start_upstream(args.proxy_image)
            fixture.start_hub(args.caddy_image)
            url = check_mcp(fixture.base, fixture.token)
            assert rpc(url, fixture.token, 'tools/list')['result']['tools'] == []
            print('PASS constrained startup, unchanged bearer through Caddy, empty discovery, auth and management denial')
            check_proxy(fixture)
            check_tools(fixture, url)
        except Exception:
            for name in fixture.containers:
                if name.endswith(('-backend', '-proxy', '-edge')):
                    print('Synthetic fixture diagnostic:', name, docker('logs', name))
            raise
        finally:
            fixture.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--caddy-image', required=True)
    parser.add_argument('--proxy-image', required=True)
    parser.add_argument('--execute', action='store_true')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
