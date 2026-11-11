#!/usr/bin/env python3
"""Pinned hub + actual profile provisioner + stdio adapters + real REST/FileStore.

Synthetic files/identities only, inert Camofox peer. No installed profiles,
Firefox, upstream patches, pulls, builds, production settings or service restart.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from infra.mcphub.isolated_acceptance import Fixture, IMAGE, SESSIONS, docker, rpc, request
from scripts import configure_browser_profiles as provisioner
from scripts.browser_profile_hub import Hub
from scripts.configure_mcphub import encode


class ProfileFixture(Fixture):
    hub_memory_mib = 512

    def start(self, role, image, network, options, command):
        options = ['--pull', 'never', *options]
        if role == 'hub':
            # Role options follow the base fixture defaults; inspect the effective
            # Docker cap below rather than assuming duplicate-flag precedence.
            options = [*options, '--memory', f'{self.hub_memory_mib}m',
                       '-v', f'{ROOT}/infra/mcphub:/runtime:ro']
        return super().start(role, image, network, options, command)


def put(path, value):
    path.write_bytes(value); path.chmod(0o600)


class RecoveryHub(Hub):
    """Discard two real API replies; production code must reconcile, never recreate."""
    lost_create = False
    lost_reset = False
    job_path = None

    def request(self, method, path, body=None):
        value = super().request(method, path, body)
        if method == 'POST' and path == '/api/auth/keys' and not self.lost_create:
            self.lost_create = True
            raise provisioner.HubError('fixture_lost_create_reply')
        if method == 'POST' and path.endswith('/reset') and not self.lost_reset:
            self.lost_reset = True
            raise provisioner.HubError('fixture_lost_reset_reply')
        return value

    def prepare_key(self, name, group_id, saved, persist):
        value = super().prepare_key(name, group_id, saved, persist)
        if not saved.get('token'):
            row = next(row for row in self.keys() if row['id'] == value['id'])
            assert row['enabled'] is False and row['token'] != value['token']
            assert request(self.base + '/mcp/' + name, value['token'],
                {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})[0] == 401
            job = provisioner.read_json(str(self.job_path))
            assert any(item.get('key', {}).get('token') == value['token'] for item in job['profiles'].values())
        return value


def seed(fixture, hub, count):
    """API-owned legacy browser/common grants and explicitly selected native YAML."""
    docker('exec', '-d', fixture.hub, 'node', '/runtime/fixture-browser-profiles.mjs')
    docker('exec', '-d', fixture.hub, 'node', '/runtime/fixture-backend.mjs')
    legacy = fixture.state / 'legacy'; legacy.mkdir(mode=0o700)
    put(legacy / 'rest.json', encode({'baseUrl': 'http://tool-access:9081', 'host': 'tool-edge:18792',
        'token': 'a' * 64, 'statePath': '/state/legacy/journal.json'}))
    hub.request('POST', '/api/servers', {'name': 'legacy-browser', 'config': {
        'type': 'stdio', 'command': 'node', 'args': ['/runtime/browser-tools.mjs'], 'enabled': True,
        'perSessionClient': False, 'env': {'SPARTAN_BROWSER_TOOLS_BINDING': '/state/legacy/rest.json'}}})
    ready = hub.wait_server('legacy-browser')
    tools = [row['name'].removeprefix('legacy-browser-') for row in ready['tools']]
    hub.request('POST', '/api/servers', {'name': 'common', 'config': {
        'type': 'streamable-http', 'url': 'http://127.0.0.1:8080/mcp', 'enabled': True}})
    hub.wait_server('common')
    settings = hub.settings(); group = settings['groups'][0]
    servers = [{'name': 'legacy-browser', 'tools': ['browser_session_create'], 'prompts': [], 'resources': []},
               {'name': 'common', 'tools': ['allowed'], 'prompts': [], 'resources': []}]
    hub.request('PUT', '/api/groups/' + group['id'], {'servers': servers})
    grants = [{'id': 'fixture-' + name, 'effect': 'allow', 'groupId': group['id'],
               'serverId': settings['mcpServers'][name]['id'], 'tool': tool}
              for name, tool in [('legacy-browser', 'browser_session_create'), ('common', 'allowed')]]
    hub.request('PUT', '/api/system-config', {'toolPolicies': [*settings['systemConfig']['toolPolicies'], *grants]})
    runtime = fixture.state / 'runtime'; runtime.mkdir(mode=0o700)
    for name in provisioner.MODULES:
        put(runtime / name, (ROOT / 'infra/mcphub' / name).read_bytes())
    template = fixture.state / 'template.json'
    put(template, encode({'baseUrl': 'http://127.0.0.1:8082', 'token': 'synthetic-browser-key', 'uploadRoot': '/browser/uploads'}))
    policy = fixture.state / 'policy.json'
    put(policy, encode({'version': 1, 'clients': [{'principal': 'legacy', 'browserUser': 'legacy-scratch',
        'tokenSha256': provisioner.digest(('a' * 64).encode()), 'capabilities': ['browser', 'upload']}]}))
    manifest = {'hub': fixture.base, 'passwordFile': str(fixture.state / 'bootstrap-password'),
        'sourceGroup': 'agents', 'legacyBrowser': 'legacy-browser', 'policyFile': str(policy),
        'nativeTemplate': str(template), 'nativeTemplateSha256': provisioner.digest(template.read_bytes()),
        'runtimeHost': str(runtime), 'runtimeContainer': '/state/runtime',
        'runtimeSha256': {name: provisioner.digest((runtime / name).read_bytes()) for name in provisioner.MODULES},
        'bindingsHost': str(fixture.state / 'bindings'), 'bindingsContainer': '/state/bindings',
        'restBase': 'http://tool-access:9081', 'clientBase': fixture.base, 'tools': tools, 'profiles': []}
    for index in range(count):
        home = fixture.root / ('native-' + str(index)); home.mkdir(mode=0o700)
        config = home / 'config.yaml'
        auth = 'authorization' if index % 2 else 'Authorization'
        put(config, ('# preserve\nmcp_servers:\n  spartan-mcphub:\n    url: ' + fixture.base
                    + '/mcp/agents\n    headers:\n      ' + auth + ': Bearer old-synthetic-key\n').encode())
        config.chmod((0o600, 0o640, 0o644)[index % 3])
        manifest['profiles'].append({'name': 'fixture-' + str(index), 'slug': 'p' + str(index),
            'config': str(config), 'nativeUser': 'native-' + str(index), 'sha256': provisioner.digest(config.read_bytes())})
    return manifest


def start_rest(fixture, image):
    for directory in ('incoming', 'outgoing'):
        (fixture.state / directory).mkdir(mode=0o700)
    environment = {'POLICY_FILE': '/policy/policy.json', 'ALLOWED_HOSTS': 'tool-edge:18792',
        'SEARCH_URL': 'http://mcphub:8082', 'READER_URL': 'http://mcphub:8082',
        'CAMOFOX_URL': 'http://mcphub:8082', 'CAMOFOX_ACCESS_KEY': 'synthetic-browser-key',
        'TRANSFER_INCOMING_DIR': '/incoming', 'TRANSFER_UPLOAD_DIR': '/outgoing',
        'CAMOFOX_UPLOAD_ROOT': '/browser/uploads', 'PYTHONDONTWRITEBYTECODE': '1'}
    options = ['--network-alias', 'tool-access', '-v', f'{ROOT}/infra/tool-access:/app:ro',
        '-v', f'{fixture.state}:/policy:ro', '-v', f'{fixture.state}/incoming:/incoming:ro',
        '-v', f'{fixture.state}/outgoing:/outgoing:rw']
    options.extend(part for key, value in environment.items() for part in ('-e', key + '=' + value))
    fixture.start('rest', image, fixture.internal, options, ['python3', 'server.py'])


def call(url, key, name, arguments):
    result = rpc(url, key, 'tools/call', {'name': name, 'arguments': arguments})
    assert not result.get('error') and not result['result'].get('isError'), result
    return json.loads(result['result']['content'][0]['text'])


def check_files(fixture, clients, sessions):
    incoming = fixture.state / 'incoming'
    payload = b'Synthetic profile-bound file; no user document.\n'
    put(incoming / '.probe.tmp', payload)
    os.replace(incoming / '.probe.tmp', incoming / 'probe.txt')
    staged = [call(url, key, 'legacy-browser-browser_file_stage', {'operation_id': str(uuid.uuid4()),
        'session_id': session, 'path': 'probe.txt'}) for (url, key), session in zip(clients[:2], sessions[:2])]
    assert staged[0]['file_id'] != staged[1]['file_id']
    assert all(row['sha256'] == provisioner.digest(payload) for row in staged)
    foreign = rpc(*clients[1], 'tools/call', {'name': 'legacy-browser-browser_file_remove', 'arguments': {
        'operation_id': str(uuid.uuid4()), 'session_id': sessions[1], 'file_id': staged[0]['file_id']}})
    assert json.loads(foreign['result']['content'][0]['text'])['error']['code'] == 'forbidden'
    assert len(list((fixture.state / 'outgoing').iterdir())) == 2
    assert (incoming / 'probe.txt').read_bytes() == payload


def check_clients(fixture, manifest, job):
    clients = []
    for profile in manifest['profiles']:
        entry = provisioner.object_bytes(Path(profile['config']).read_bytes(), yaml=True)['mcp_servers']['spartan-mcphub']
        saved = job['profiles'][profile['slug']]
        assert entry['url'] == fixture.base + '/mcp/' + saved['name']
        auth = [value for key, value in entry['headers'].items() if key.lower() == 'authorization']
        assert auth == ['Bearer ' + saved['key']['token']]
        clients.append((entry['url'], auth[0][7:]))
    operations = [str(uuid.uuid4()) for _ in clients]
    sessions = []
    for index, (url, key) in enumerate(clients):
        tools = rpc(url, key, 'tools/list')['result']['tools']
        names = {row['name'] for row in tools}
        assert names == {'common-allowed', *('legacy-browser-' + tool for tool in manifest['tools'])}, names
        common = rpc(url, key, 'tools/call', {'name': 'common-allowed', 'arguments': {}})
        assert not common.get('error') and not common['result'].get('isError'), common
        sessions.append(call(url, key, 'legacy-browser-browser_session_create', {'operation_id': operations[index]})['session_id'])
    for index, (url, key) in enumerate(clients):
        other_index = (index + 1) % len(clients)
        other = list(job['profiles'].values())[other_index]
        result = rpc(url, key, 'tools/call', {'name': other['name'] + '-browser_session_create',
                     'arguments': {'operation_id': str(uuid.uuid4())}})
        assert 'error' in result or result.get('result', {}).get('isError'), result
        assert request(clients[other_index][0], key, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})[0] in (401, 403)
    check_files(fixture, clients, sessions)
    SESSIONS.pop(clients[0], None)
    assert call(*clients[0], 'legacy-browser-browser_session_create', {'operation_id': operations[0]})['session_id'] == sessions[0]
    ledger = json.loads(docker('exec', fixture.hub, 'node', '-e',
        "fetch('http://127.0.0.1:8082/executions').then(r=>r.text()).then(console.log)"))
    assert sum(row['path'] == '/health' for row in ledger) == len(clients), ledger
    for (url, key), session_id in zip(clients, sessions):
        call(url, key, 'legacy-browser-browser_session_close', {'operation_id': str(uuid.uuid4()), 'session_id': session_id})
    assert list((fixture.state / 'outgoing').iterdir()) == []
    return clients


def check(fixture, tool_image, count):
    hub = RecoveryHub(fixture.base); hub.login((fixture.state / 'bootstrap-password').read_text().strip())
    manifest = seed(fixture, hub, count)
    start_rest(fixture, tool_image)
    prepared = provisioner.preflight(manifest, hub)
    args = argparse.Namespace(job=str(fixture.root / 'protected-job'), rollback=False)
    hub.job_path = Path(args.job) / 'job.json'
    previous = None
    for expected in ('fixture_lost_create_reply', 'fixture_lost_reset_reply', None):
        try:
            provisioner._execute_job(args, manifest, hub, prepared, previous)
            assert expected is None
        except provisioner.HubError as error:
            assert str(error) == expected, str(error)
            previous = provisioner.read_json(str(hub.job_path))
            owned = [row for row in hub.keys() if row['name'] == previous['profiles']['p0']['name']]
            assert len(owned) == 1 and owned[0]['enabled'] is False
            prepared = provisioner.preflight(manifest, hub, previous)
    job = provisioner.read_json(str(Path(args.job) / 'job.json'))
    check_clients(fixture, manifest, job)
    manifest_path = fixture.root / 'manifest.json'; put(manifest_path, encode(manifest))
    command = [sys.executable, str(ROOT / 'scripts/configure_browser_profiles.py'), '--manifest',
               str(manifest_path), '--job', args.job, '--apply']
    outcome = subprocess.run(command, text=True, capture_output=True, timeout=60)
    assert outcome.returncode == 0, outcome.stderr
    assert json.loads(outcome.stdout)['configuration'] == 'APPLIED'
    resumed = provisioner.preflight(manifest, hub, job)
    fresh = provisioner.read_json(str(Path(args.job) / 'job.json'))
    assert fresh == job
    legacy_key = json.loads((fixture.state / 'clients.json').read_text())['hermes']
    assert {row['name'] for row in rpc(fixture.base + '/mcp/agents', legacy_key, 'tools/list')['result']['tools']} == {
        'common-allowed', 'legacy-browser-browser_session_create'}
    resources = docker('stats', '--no-stream', '--format', '{{.MemUsage}} / pids={{.PIDs}}', fixture.hub)
    peaks = json.loads(docker('exec', fixture.hub, 'node', '-e',
        "const f=require('fs');console.log(JSON.stringify(Object.fromEntries(['memory.peak','pids.peak'].map(n=>[n,Number(f.readFileSync('/sys/fs/cgroup/'+n,'utf8'))]))))"))
    events = json.loads(docker('exec', fixture.hub, 'node', '-e',
        "const f=require('fs');console.log(JSON.stringify(Object.fromEntries(f.readFileSync('/sys/fs/cgroup/memory.events','utf8').trim().split('\\n').map(l=>{const[k,v]=l.split(' ');return[k,Number(v)]}))))"))
    print(json.dumps({'resource_checkpoint': resources, 'peaks': peaks, 'memory_events': events}), flush=True)
    assert peaks['memory.peak'] < fixture.hub_memory_mib * 1024**2 and peaks['pids.peak'] < 128, peaks
    assert events.get('oom', 0) == 0 and events.get('oom_kill', 0) == 0, events
    assert len(hub.keys()) == 3 + count
    args.rollback = True
    provisioner._execute_job(args, manifest, hub, resumed, job)
    assert all(Path(row['config']).stat().st_mode & 0o777 == (0o600, 0o640, 0o644)[index % 3]
               for index, row in enumerate(manifest['profiles']))
    assert all(not next(key for key in hub.keys() if key['id'] == saved['key']['id'])['enabled']
               for saved in job['profiles'].values())
    assert len(json.loads(Path(manifest['policyFile']).read_bytes())['clients']) == 1 + count
    print(json.dumps({'result': 'PASS', 'boundary': 'native-hub-API-actual-provisioner-stdio-real-REST-files-inert-browser',
        'cross_profile_execution_denied': True, 'legacy_alias_and_common_grants_preserved': True,
        'new_MCP_session_same_journal_replay': True, 'fresh_provisioner_process_resume': True,
        'guarded_rollback_retains_authority_state': True,
        'real_staging_cross_profile_file_denial_and_cleanup': True,
        'profiles': count, 'hub_memory_mib': fixture.hub_memory_mib,
        'lost_create_and_reset_same_disabled_key': True,
        'resources': resources, 'peaks': peaks, 'memory_events': events, 'actual_Firefox_or_Hermes': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--caddy-image', required=True)
    parser.add_argument('--tool-image', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--profiles', type=int, choices=(2, 7), default=2)
    parser.add_argument('--hub-memory-mib', type=int, choices=(512, 1024), default=512,
                        help='Disposable hub only; requires authority for the selected envelope')
    args = parser.parse_args()
    images = {image: docker('image', 'inspect', image, '--format', '{{.Id}}')
              for image in (IMAGE, args.caddy_image, args.tool_image)}
    if shutil.disk_usage(ROOT).free < 5 * 1024**3:
        raise RuntimeError('5GB disk floor')
    if not args.execute:
        print('Preflight only; explicit isolated-operation authority required for --execute')
        return
    root = Path(tempfile.mkdtemp(prefix='browser-profiles-fixture-'))
    fixture = ProfileFixture(root)
    fixture.hub_memory_mib = args.hub_memory_mib
    try:
        for name in (fixture.internal, fixture.publishing):
            docker('network', 'create', *(['--internal'] if name == fixture.internal else []), name)
            fixture.networks.append(name)
        fixture.start_hub(images[args.caddy_image])
        effective = int(docker('inspect', fixture.hub, '--format', '{{.HostConfig.Memory}}'))
        assert effective == args.hub_memory_mib * 1024**2, effective
        check(fixture, images[args.tool_image], args.profiles)
    finally:
        # A cleanup exception must retain state for any still-owned live resource.
        fixture.cleanup()
        shutil.rmtree(root)


if __name__ == '__main__':
    main()
