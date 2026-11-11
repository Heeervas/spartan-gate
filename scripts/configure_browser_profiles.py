#!/usr/bin/env python3
"""Provision explicitly selected Hermes browser-MCP profiles through native APIs.

Default is read-only preflight. An explicit protected manifest pins profiles,
source hashes, origins and paths; no home discovery, core patches or restarts.
Keep the protected job (including beforeimages and one-time secrets) outside
native discovery. Rollback restores clients, disables owned keys, and RETAINS
backend journals, scratch principals and staged files for reconciliation.
"""
import argparse
import base64
import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from urllib.parse import urlsplit
import uuid

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.browser_profile_hub import Hub, HubError
from scripts.configure_mcphub import CLOSED_SERVER_ID, NAME, encode, object_bytes, replace_private, safe_path
MODULES = ('browser-tools.mjs', 'browser-contracts.mjs', 'browser-state.mjs',
           'browser-http.mjs', 'browser-camofox.mjs', 'browser-debug.mjs')
MAX_PRIVATE = 4 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def selected_file(value, *, native=False):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path.resolve() != path:
        raise ValueError('unsafe_selected_path')
    _file_info(path.stat(), native)
    return path


def _file_info(info, native):
    modes = (0o600, 0o640, 0o644) if native else (0o600,)
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in modes
            or info.st_uid != os.geteuid() or info.st_size > MAX_PRIVATE):
        raise ValueError('unsafe_selected_file')


def selected_bytes(value, *, native=False):
    path = selected_file(str(value), native=native)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        _file_info(os.fstat(source.fileno()), native)
        raw = source.read(MAX_PRIVATE + 1)
    if len(raw) > MAX_PRIVATE:
        raise ValueError('selected_file_too_large')
    return raw


def read_json(path):
    return json.loads(selected_bytes(path))


def durable_write(path, raw, owner=None, mode=0o600):
    if len(raw) > MAX_PRIVATE:
        raise ValueError('private_write_too_large')
    owner = owner or (os.geteuid(), os.getegid())
    replace_private(Path(path), raw, owner)
    os.chmod(path, mode)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    sync_directory(Path(path).parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def checked_directory(value):
    path = safe_path(str(value), directory=True)
    if path.exists() and stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ValueError('private_directory_requires_0700')
    return path


def private_directory(value):
    path = checked_directory(value)
    if not path.exists():
        path.mkdir(mode=0o700)
        sync_directory(path.parent)
    return path


def validate_policy(value):
    spec = importlib.util.spec_from_file_location('_browser_profile_policy', ROOT / 'infra/tool-access/policy.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if len(encode(value)) > module.POLICY_LIMIT:
        raise ValueError('policy_capacity_exceeded')
    module.validate_policy(value)
    return value


def client_bytes(before, url, token):
    try:
        value = object_bytes(before, yaml=True)
        entry = value['mcp_servers'][NAME]
        if not isinstance(entry, dict) or not isinstance(entry.get('headers'), dict):
            raise ValueError('missing_selected_mcp')
        auth = [key for key in entry['headers'] if isinstance(key, str) and key.lower() == 'authorization']
        if len(auth) != 1:
            raise ValueError('ambiguous_native_authorization')
        entry['url'] = url
        entry['headers'][auth[0]] = 'Bearer ' + token
        stream = io.StringIO()
        YAML(typ='rt').dump(value, stream)
        return stream.getvalue().encode()
    except (KeyError, TypeError, YAMLError, UnicodeError):
        raise ValueError('invalid_selected_native_config') from None


def _manifest_profiles(profiles):
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= 32:
        raise ValueError('invalid_profile_count')
    for field in ('name', 'slug', 'config', 'nativeUser'):
        values = [row[field] for row in profiles]
        if len(values) != len(set(values)):
            raise ValueError('duplicate_profile_identity_or_path')
    for row in profiles:
        if (not re.fullmatch(r'[a-z0-9_-]{1,32}', row['slug'])
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', row['nativeUser'])
                or not re.fullmatch(r'[a-f0-9]{64}', row['sha256'])):
            raise ValueError('invalid_profile_scope')


def _manifest_tools(tools):
    if (not tools or len(tools) != len(set(tools)) or len(tools) > 64
            or any(not re.fullmatch(r'browser_[a-z_]+', name) for name in tools)):
        raise ValueError('invalid_exact_tools')


def _client_origin(value):
    origin = urlsplit(value)
    if (origin.scheme != 'http' or origin.hostname not in ('caddy', '127.0.0.1', 'localhost', '::1')
            or not origin.port or origin.path or origin.username or origin.password or origin.query or origin.fragment):
        raise ValueError('invalid_fixed_client_origin')


def _manifest_check(manifest):
    _manifest_profiles(manifest['profiles'])
    _manifest_tools(manifest['tools'])
    _client_origin(manifest['clientBase'])
    runtime = safe_path(manifest['runtimeHost'], directory=True)
    for name in MODULES:
        if digest(selected_bytes(runtime / name)) != manifest['runtimeSha256'][name]:
            raise ValueError('runtime_source_drift')
    for key in ('runtimeContainer', 'bindingsContainer'):
        value = Path(manifest[key])
        if not value.is_absolute() or '..' in value.parts:
            raise ValueError('invalid_container_path')
    root = checked_directory(manifest['bindingsHost'])
    if not root.parent.is_dir():
        raise ValueError('missing_binding_parent')


def _source_routing(routing):
    if (routing.get('skipAuth') or routing.get('enableBearerAuth') is not True
            or routing.get('enableGlobalRoute') is not False or routing.get('enableGroupNameRoute') is not True):
        raise ValueError('unsafe_hub_routing')


def _common_servers(group, legacy, settings):
    common = [copy.deepcopy(row) for row in group['servers'] if (row if isinstance(row, str) else row['name']) != legacy]
    old_entry = next((row for row in group['servers'] if (row if isinstance(row, str) else row['name']) == legacy), None)
    if old_entry is None or legacy not in settings['mcpServers']:
        raise ValueError('missing_legacy_browser_grant')
    if settings['mcpServers'][legacy].get('env', {}).get('SPARTAN_BROWSER_NATIVE_BINDING'):
        raise ValueError('legacy_native_consumers_require_reconciliation')
    alias = old_entry.get('alias', legacy) if isinstance(old_entry, dict) else legacy
    return common, alias, settings['mcpServers'][legacy]['id']


def _identity_policy_guard(policies):
    if any(row.get('username') or row.get('keyId') for row in policies):
        raise ValueError('identity_scoped_legacy_policy_requires_review')


def _scoped_policies(policies, group_id, old_id):
    _identity_policy_guard(policies)
    if not any(row.get('groupId') == group_id and row.get('effect') == 'allow'
               and row.get('serverId') == CLOSED_SERVER_ID for row in policies):
        raise ValueError('missing_deny_by_default_sentinel')
    scoped = [copy.deepcopy(row) for row in policies if row.get('groupId') == group_id]
    copied = [row for row in scoped if row.get('serverId') != old_id]
    denies = [row for row in scoped if row.get('serverId') == old_id and row['effect'] == 'deny']
    if any(row['effect'] == 'allow' and row.get('serverId') != CLOSED_SERVER_ID
           and (not row.get('serverId') or not row.get('tool')) for row in copied):
        raise ValueError('source_common_allow_not_exact')
    return copied, denies


def _source_contract(manifest, settings):
    _source_routing(settings['systemConfig']['routing'])
    group = next((row for row in settings['groups'] if row['name'] == manifest['sourceGroup']), None)
    if not group:
        raise ValueError('missing_source_group')
    if group.get('instructions') is not None and not isinstance(group['instructions'], str):
        raise ValueError('invalid_source_group_instructions')
    common, alias, old_id = _common_servers(group, manifest['legacyBrowser'], settings)
    copied, denies = _scoped_policies(settings['systemConfig']['toolPolicies'], group['id'], old_id)
    return {'sourceGroup': group, 'common': common, 'alias': alias, 'commonPolicies': copied, 'browserDenies': denies}


def _fixed_origin(value, service):
    origin = urlsplit(value)
    if (origin.scheme != 'http' or origin.netloc != service and origin.hostname != '127.0.0.1'
            or not origin.port or origin.username or origin.password or origin.path or origin.query or origin.fragment):
        raise ValueError('invalid_fixed_backend_origin')


def _template(manifest):
    raw = selected_bytes(manifest['nativeTemplate'])
    if digest(raw) != manifest['nativeTemplateSha256']:
        raise ValueError('native_template_drift')
    value = json.loads(raw)
    if (set(value) != {'baseUrl', 'token', 'uploadRoot'}
            or not re.fullmatch(r'[!-~]{16,512}', value['token'])):
        raise ValueError('invalid_native_template')
    root = Path(value['uploadRoot'])
    if (not root.is_absolute() or '..' in root.parts or str(root) != value['uploadRoot']
            or len(value['uploadRoot']) > 512 or value['uploadRoot'].endswith('/')
            or not re.fullmatch(r'/(?:[A-Za-z0-9_.-]+/?)+', value['uploadRoot'])):
        raise ValueError('invalid_upload_root')
    _fixed_origin(value['baseUrl'], 'camofox:9377')
    _fixed_origin(manifest['restBase'], 'tool-access:9081')
    return value


def binding_values(manifest, profile, saved, template):
    container = str(Path(manifest['bindingsContainer']) / saved['name'])
    return {'rest.json': {'baseUrl': manifest['restBase'], 'host': 'tool-edge:18792',
                         'token': saved['restToken'], 'statePath': container + '/journal.json'},
            'native.json': {**template, 'managedUserId': saved['restClient']['browserUser'],
                            'profiles': {'native': {'userId': profile['nativeUser'], 'groupAccess': 'profile'}}}}


def checked_client(profile, saved, reason):
    path = selected_file(profile['config'], native=True)
    raw = selected_bytes(path, native=True)
    current = digest(raw)
    if current not in {profile['sha256'], saved.get('afterSha256')}:
        raise ValueError(reason)
    expected_mode = 0o600 if current == saved.get('afterSha256') else saved['mode']
    info = path.stat()
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (*saved['owner'], expected_mode):
        raise ValueError('client_metadata_drift')
    return path, raw


def _profile_record(manifest, row, job):
    saved = job['profiles'].get(row['slug'])
    if saved:
        checked_client(row, saved, 'selected_config_drift')
        return saved
    path = selected_file(row['config'], native=True)
    raw, info = selected_bytes(path, native=True), path.stat()
    if digest(raw) != row['sha256']:
        raise ValueError('selected_config_drift')
    try:
        value = object_bytes(raw, yaml=True)['mcp_servers'][NAME]
        if (urlsplit(value['url']).path != '/mcp/' + manifest['sourceGroup']
                or value['url'].rsplit('/mcp/', 1)[0] != manifest['clientBase']
                or value.get('enabled', True) is not True):
            raise ValueError('selected_profile_not_opted_in')
        client_bytes(raw, value['url'], 'preflight-only')
    except (KeyError, TypeError, YAMLError, UnicodeError):
        raise ValueError('invalid_selected_native_config') from None
    return {'name': 'browser-' + job['id'][:8] + '-' + row['slug'],
            'before': base64.b64encode(raw).decode(), 'mode': stat.S_IMODE(info.st_mode),
            'owner': [info.st_uid, info.st_gid], 'restToken': secrets.token_hex(32)}


def _profile_preflight(manifest, row, job, policy, template, occupied):
    saved = _profile_record(manifest, row, job)
    job['profiles'][row['slug']] = saved
    for kind, marker in [('servers', 'serverIntent'), ('groups', 'groupIntent'), ('keys', 'key')]:
        if saved['name'] in occupied[kind] and not saved.get(marker):
            raise ValueError('occupied_profile_namespace')
    client = {'principal': 'browser-mcp-' + row['slug'], 'browserUser': 'mcp-scratch-' + row['slug'],
              'tokenSha256': digest(saved['restToken'].encode()), 'capabilities': ['browser', 'upload']}
    if client['browserUser'] == row['nativeUser']:
        raise ValueError('scratch_native_identity_collision')
    existing = next((item for item in policy['clients'] if item['principal'] == client['principal']), None)
    if existing and existing != client:
        raise ValueError('occupied_rest_principal')
    if not existing:
        policy['clients'].append(client)
    saved['restClient'] = client
    slot = checked_directory(Path(manifest['bindingsHost']) / saved['name'])
    for name, value in binding_values(manifest, row, saved, template).items():
        if len(encode(value)) > (4096 if name == 'rest.json' else 16384):
            raise ValueError('binding_too_large')
        path = safe_path(str(slot / name))
        if path.exists() and selected_bytes(path) != encode(value):
            raise ValueError('staged_binding_drift')
    return saved


def preflight(manifest, hub, previous=None):
    _manifest_check(manifest)
    fingerprint = digest(encode(manifest))
    if previous and previous['manifestSha256'] != fingerprint:
        raise ValueError('job_manifest_drift')
    settings = hub.settings()
    contract = _source_contract(manifest, settings)
    if previous and any(previous.get(key) != value for key, value in contract.items()):
        raise ValueError('source_grants_changed_since_preflight')
    job = copy.deepcopy(previous) if previous else {'id': str(uuid.uuid4()), 'manifestSha256': fingerprint,
                                                   'profiles': {}, **contract}
    policy = validate_policy(read_json(manifest['policyFile']))
    template = _template(manifest)
    occupied = {'keys': {row['name'] for row in hub.keys()}, 'servers': set(settings['mcpServers']),
                'groups': {row['name'] for row in settings['groups']}}
    for row in manifest['profiles']:
        _profile_preflight(manifest, row, job, policy, template, occupied)
        if len(encode(job)) > MAX_PRIVATE - 131072 - 2048 * len(manifest['profiles']):
            raise ValueError('protected_job_capacity_exceeded')
    validate_policy(policy)
    return job, policy, template


def _staged_policy(manifest, job, policy):
    raw = selected_bytes(manifest['policyFile'])
    current = validate_policy(json.loads(raw))
    additions = [saved['restClient'] for saved in job['profiles'].values()]
    base = [row for row in policy['clients'] if row not in additions]
    expected = {row['principal']: row for row in policy['clients']}
    if (any(expected.get(row['principal']) != row for row in current['clients'])
            or any(row not in current['clients'] for row in base)):
        raise ValueError('policy_changed_after_preflight')
    if job.get('policyBefore'):
        original = json.loads(base64.b64decode(job['policyBefore']))['clients']
        if any(row not in current['clients'] for row in original):
            raise ValueError('original_rest_authority_changed')
    merged = copy.deepcopy(current)
    merged['clients'].extend(row for row in additions if row not in merged['clients'])
    return raw, current, validate_policy(merged)


def stage(manifest, job, policy, template, save):
    policy_before, current, merged = _staged_policy(manifest, job, policy)
    root = private_directory(manifest['bindingsHost'])
    for profile in manifest['profiles']:
        saved = job['profiles'][profile['slug']]
        slot = root / saved['name']
        private_directory(slot)
        for name, value in binding_values(manifest, profile, saved, template).items():
            path, raw = safe_path(str(slot / name)), encode(value)
            if path.exists() and selected_bytes(path) != raw:
                raise ValueError('staged_binding_drift')
            if not path.exists():
                durable_write(path, raw)
    policy_path = selected_file(manifest['policyFile'])
    if selected_bytes(policy_path) != policy_before:
        raise ValueError('policy_changed_during_staging')
    if merged != current:
        job.setdefault('policyBefore', base64.b64encode(policy_before).decode())
        save()
        durable_write(policy_path, encode(merged), (policy_path.stat().st_uid, policy_path.stat().st_gid))


def _owned_server(manifest, hub, saved, save):
    name = saved['name']
    container = str(Path(manifest['bindingsContainer']) / name)
    config = {'type': 'stdio', 'command': 'node', 'args': [str(Path(manifest['runtimeContainer']) / 'browser-tools.mjs')],
              'enabled': True, 'perSessionClient': False, 'owner': 'admin', 'visibility': 'private', 'env': {
                  'SPARTAN_BROWSER_TOOLS_BINDING': container + '/rest.json',
                  'SPARTAN_BROWSER_NATIVE_BINDING': container + '/native.json'}}
    settings = hub.settings()
    existing = settings['mcpServers'].get(name)
    if not existing:
        if saved.get('serverIntent'):
            raise HubError('server_creation_unconfirmed')
        saved['serverIntent'] = True; save()
        hub.request('POST', '/api/servers', {'name': name, 'config': config})
        existing = hub.settings()['mcpServers'].get(name, {})
    # Native persistence omits false perSessionClient; missing means shared.
    actual = {**existing, 'perSessionClient': existing.get('perSessionClient', False)}
    if not saved.get('serverIntent') or any(actual.get(k) != v for k, v in config.items()):
        raise HubError('owned_server_drift')
    ready = hub.wait_server(name)
    separator = settings['systemConfig'].get('nameSeparator', '-')
    names = {row['name'].removeprefix(name + separator) for row in ready['tools']}
    if names != set(manifest['tools']):
        raise HubError('unexpected_backend_catalog')
    return existing['id']


def _owned_group(manifest, hub, saved, job, save):
    name = saved['name']
    servers = [*job['common'], {'name': name, 'alias': job['alias'], 'tools': manifest['tools'], 'prompts': [], 'resources': []}]
    description = 'Managed browser profile job ' + job['id']
    instructions = job['sourceGroup'].get('instructions') or ''
    groups = hub.settings()['groups']
    group = next((row for row in groups if row['name'] == name), None)
    if not group:
        if saved.get('groupIntent'):
            raise HubError('group_creation_unconfirmed')
        saved['groupIntent'] = True; save()
        group = hub.request('POST', '/api/groups', {'name': name, 'description': description,
            'visibility': 'private', 'instructions': instructions, 'servers': servers})
    if (not saved.get('groupIntent') or group.get('description') != description or group['servers'] != servers
            or group.get('owner') != 'admin' or group.get('visibility') != 'private' or group.get('sharedWithUsers')
            or (group.get('instructions') or '') != instructions):
        raise HubError('owned_group_drift')
    return group['id']


def _owned_grants(manifest, hub, saved, job):
    name, group_id, server_id = saved['name'], saved['groupId'], saved['serverId']
    desired = [{**rule, 'id': name + '-common-' + str(index), 'groupId': group_id}
               for index, rule in enumerate(job['commonPolicies'])]
    desired.extend({**rule, 'id': name + '-deny-' + str(index), 'groupId': group_id, 'serverId': server_id}
                   for index, rule in enumerate(job['browserDenies']))
    desired.extend({'id': name + '-tool-' + str(index), 'effect': 'allow', 'groupId': group_id,
                    'serverId': server_id, 'tool': tool} for index, tool in enumerate(manifest['tools']))
    current = hub.settings()['systemConfig']['toolPolicies']
    lookup = {rule['id']: rule for rule in current}
    if any(rule['id'] in lookup and lookup[rule['id']] != rule for rule in desired):
        raise HubError('owned_policy_drift')
    merged = [*current, *(rule for rule in desired if rule['id'] not in lookup)]
    if merged != current:
        hub.request('PUT', '/api/system-config', {'toolPolicies': merged})
    if hub.settings()['systemConfig']['toolPolicies'] != merged:
        raise HubError('policy_readback_conflict')


def owned_objects(manifest, hub, saved, job, save):
    saved['serverId'] = _owned_server(manifest, hub, saved, save)
    saved['groupId'] = _owned_group(manifest, hub, saved, job, save)
    save()
    _owned_grants(manifest, hub, saved, job)
    def persist(value):
        saved['key'] = value; save()
    return hub.prepare_key(saved['name'], saved['groupId'], saved.get('key', {}), persist)


def publish(manifest, hub, profile, saved, key, save):
    after = client_bytes(base64.b64decode(saved['before']), manifest['clientBase'] + '/mcp/' + saved['name'], key['token'])
    path, _ = checked_client(profile, saved, 'client_publication_conflict')
    saved['afterSha256'] = digest(after); save()
    row = next(row for row in hub.keys() if row['id'] == key['id'])
    hub._check_scope(row, saved['groupId'])
    if saved.get('published') and row['enabled'] is not True:
        raise HubError('published_key_disabled_externally')
    if row['enabled'] is not True:
        hub.request('PUT', '/api/auth/keys/' + key['id'], {'enabled': True})
    if next(row for row in hub.keys() if row['id'] == key['id'])['enabled'] is not True:
        raise HubError('key_enable_unconfirmed')
    path, current = checked_client(profile, saved, 'client_publication_conflict')
    if current != after:
        durable_write(path, after, tuple(saved['owner']))
    saved['published'] = True; save()


def _rollback_preflight(manifest, hub, job):
    keys = {row['id']: row for row in hub.keys()}
    for profile in manifest['profiles']:
        saved = job['profiles'][profile['slug']]
        checked_client(profile, saved, 'rollback_client_drift')
        if saved.get('key', {}).get('id'):
            row = keys.get(saved['key']['id'], {})
            if row.get('name') != saved['name']:
                raise HubError('rollback_key_ownership_drift')
            hub._check_scope(row, saved['groupId'])


def rollback(manifest, hub, job, save):
    _rollback_preflight(manifest, hub, job)
    for profile in manifest['profiles']:
        saved = job['profiles'][profile['slug']]
        path, _ = checked_client(profile, saved, 'rollback_client_drift')
        durable_write(path, base64.b64decode(saved['before']), tuple(saved['owner']), saved['mode'])
        if saved.get('key', {}).get('id'):
            row = next(row for row in hub.keys() if row['id'] == saved['key']['id'])
            hub._check_scope(row, saved['groupId'])
            hub.request('PUT', '/api/auth/keys/' + row['id'], {'enabled': False})
            if next(key for key in hub.keys() if key['id'] == row['id'])['enabled'] is not False:
                raise HubError('key_disable_unconfirmed')
        saved['published'] = False; save()
    job['rolledBack'] = True; save()


def _execute_job(args, manifest, hub, prepared, previous):
    job, policy, template = prepared
    directory = private_directory(args.job)
    job_path = directory / 'job.json'
    lock = os.open(directory / 'operator.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('unsafe_operator_lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        latest = read_json(str(job_path)) if job_path.exists() else None
        if latest != previous:
            raise ValueError('job_changed_during_preflight')
        def save():
            durable_write(job_path, encode(job))
        save()
        if args.rollback:
            rollback(manifest, hub, job, save)
        else:
            stage(manifest, job, policy, template, save)
            for profile in manifest['profiles']:
                saved = job['profiles'][profile['slug']]
                key = owned_objects(manifest, hub, saved, job, save)
                publish(manifest, hub, profile, saved, key, save)
        print(json.dumps({'configuration': 'ROLLED_BACK_RETAINED' if args.rollback else 'APPLIED',
                          'profiles': len(manifest['profiles']), 'runtime_acceptance': 'NOT_CLAIMED'}))
    finally:
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--job', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    try:
        manifest = read_json(args.manifest)
        directory = safe_path(args.job, directory=True)
        if not directory.parent.is_dir():
            raise ValueError('missing_job_parent')
        job_path = directory / 'job.json'
        previous = read_json(str(job_path)) if job_path.exists() else None
        if args.rollback and not previous:
            raise ValueError('missing_rollback_job')
        if any(Path(row['config']).parent == directory or Path(row['config']).parent in directory.parents
               for row in manifest['profiles']):
            raise ValueError('job_must_be_outside_native_discovery')
        hub = Hub(manifest['hub'])
        hub.login(selected_bytes(manifest['passwordFile']).decode().strip())
        job, policy, template = preflight(manifest, hub, previous)
        if not (args.apply or args.rollback):
            print(json.dumps({'preflight': 'PASS', 'profiles': len(manifest['profiles']), 'configuration_writes': 0}))
            return
        _execute_job(args, manifest, hub, (job, policy, template), previous)
    except (HubError, ValueError, KeyError, TypeError, OSError, YAMLError, RecursionError) as error:
        category = str(error) if isinstance(error, (HubError, ValueError)) else ''
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,80}', category):
            category = 'validation_or_filesystem_refused'
        print('Blocked (' + category + '); retain the protected job and reconcile native API/file afterimages. No automatic retry.', file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
