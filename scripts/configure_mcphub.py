#!/usr/bin/env python3
"""Preview/apply one empty MCPHub and explicitly selected native clients.

No Docker, network, package installation or implicit home discovery. Targets JSON:
{"hermes": {"config": "/absolute/config.yaml", "url": "http://caddy:18793/mcp/agents"},
 "pi": [{"client": "host-pi", "settings": "/absolute/settings.json",
         "mcp": "/absolute/mcp.json", "url": "http://127.0.0.1:18793/mcp/agents"},
        {"client": "container-pi", "settings": "/absolute/settings.json",
         "mcp": "/absolute/mcp.json", "url": "http://caddy:18793/mcp/agents"}]}
Omit --targets for state-only preparation. All selected files must preflight before
any write. Backups contain secrets; keep them outside native discovery.
"""
import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from urllib.parse import urlsplit
import uuid

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

NAME = 'spartan-mcphub'
GROUP_ID = 'cc020e20-a2cc-45b8-8f80-e959d4d91943'
# A deliberately nonexistent server ID keeps an applicable allow set even with no
# grants. Never remove this rule: absent/empty upstream policies allow everything.
CLOSED_SERVER_ID = '00000000-0000-0000-0000-000000000000'
CLIENTS = ('hermes', 'host-pi', 'container-pi')


def encode(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def safe_path(value, *, directory=False):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Paths must be explicit absolute paths')
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Symlink paths are not supported')
    if path.exists():
        info = path.stat()
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not expected or info.st_mode & 0o077:
            raise ValueError('Selected private file/directory must be owner-only')
        if info.st_uid not in (os.geteuid(),) and os.geteuid() != 0:
            raise ValueError('Selected path has another owner')
    return path


def object_bytes(raw, yaml=False):
    value = YAML(typ='rt').load(raw.decode()) if yaml else json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Expected native configuration object')
    return value


def initial_state():
    tokens = {client: secrets.token_hex(32) for client in CLIENTS}
    settings = {
        'mcpServers': {}, 'users': [],
        'groups': [{'id': GROUP_ID, 'name': 'agents', 'owner': 'admin', 'servers': []}],
        'bearerKeys': [
            {'id': str(uuid.uuid4()), 'name': client, 'kind': 'system', 'enabled': True,
             'accessType': 'groups', 'allowedGroups': [GROUP_ID], 'allowedServers': [],
             'tokenHash': hashlib.sha256(token.encode()).hexdigest(), 'tokenPrefix': token[:12]}
            for client, token in tokens.items()],
        'systemConfig': {
            'routing': {'skipAuth': False, 'enableBearerAuth': True,
                        'enableGlobalRoute': False, 'enableGroupNameRoute': True},
            'auth': {'allowRegistration': False},
            'activityLog': {'storeToolPayload': False},
            'toolPolicies': [{'id': 'agents-closed-until-granted', 'effect': 'allow',
                              'groupId': GROUP_ID, 'serverId': CLOSED_SERVER_ID}]}}
    return {'mcp_settings.json': encode(settings), 'clients.json': encode(tokens),
            'bootstrap-password': (secrets.token_urlsafe(32) + '\n').encode()}


def state_plan(state):
    if not state.exists():
        if not state.parent.is_dir():
            raise ValueError('State parent must already exist')
        files = initial_state()
        return files, json.loads(files['clients.json'])
    required = ('mcp_settings.json', 'clients.json', 'bootstrap-password')
    for name in required:
        path = safe_path(str(state / name))
        if not path.exists():
            raise ValueError('Occupied/incomplete MCPHub state; refusing bootstrap overwrite')
    tokens = object_bytes((state / 'clients.json').read_bytes())
    if set(tokens) != set(CLIENTS) or any(not isinstance(t, str) or len(t) != 64 for t in tokens.values()):
        raise ValueError('Invalid managed client token file')
    # Do not rewrite mutable hub settings, keys, permissions or admin passwords on rerun.
    object_bytes((state / 'mcp_settings.json').read_bytes())
    return {}, tokens


def native_entry(url, token, pi=False):
    parsed = urlsplit(url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path != '/mcp/agents'):
        raise ValueError('Expected native /mcp/agents URL without credentials/query')
    if parsed.scheme == 'http' and parsed.hostname not in ('caddy', 'localhost', '127.0.0.1', '::1'):
        raise ValueError('Plain HTTP is limited to loopback or internal caddy')
    result = {'url': url, 'headers': {'Authorization': 'Bearer ' + token}}
    if pi:
        result['exposure'] = 'direct'
    return result


def merge_server(config, field, entry):
    result = copy.deepcopy(config)
    servers = result.setdefault(field, {})
    if not isinstance(servers, dict):
        raise ValueError('Native MCP servers field must be an object')
    if field == 'mcpServers' and NAME.replace('-', '_') in servers:
        raise ValueError('Occupied normalized Pi MCP server name')
    if NAME in servers and servers[NAME] != entry:
        raise ValueError('Occupied foreign MCP server entry; refusing replacement')
    servers[NAME] = entry
    return result


def pi_settings(config):
    result = copy.deepcopy(config)
    extensions = result.get('extensions', [])
    if not isinstance(extensions, list) or any(not isinstance(e, str) for e in extensions):
        raise ValueError('Expected native extensions array of strings')
    if '!builtin:mcp' in extensions:
        raise ValueError('Unexpected explicit MCP exclusion; review selected settings')
    result['extensions'] = [e for e in extensions if e != '-builtin:mcp']
    if 'builtin:mcp' not in result['extensions']:
        result['extensions'].append('builtin:mcp')
    return result


def client_plan(targets, tokens):
    changes = {}
    if set(targets) != {'hermes', 'pi'} or len(targets['pi']) != 2:
        raise ValueError('Select exactly Hermes and the two native Pi roots')
    hermes = targets['hermes']
    add_native(changes, hermes['config'], lambda v: merge_server(
        v, 'mcp_servers', native_entry(hermes['url'], tokens['hermes'])), yaml=True)
    seen = set()
    for target in targets['pi']:
        client = target['client']
        if client not in ('host-pi', 'container-pi') or client in seen:
            raise ValueError('Select each Pi client exactly once')
        seen.add(client)
        settings = Path(target['settings'])
        mcp = Path(target['mcp'])
        if settings.name != 'settings.json' or mcp.name != 'mcp.json' or settings.parent != mcp.parent:
            raise ValueError('Pi files must be settings.json and sibling mcp.json')
        add_native(changes, str(settings), pi_settings)
        add_native(changes, str(mcp), lambda v: merge_server(
            v, 'mcpServers', native_entry(target['url'], tokens[client], pi=True)), missing=True)
    return changes


def add_native(changes, name, transform, *, yaml=False, missing=False):
    path = safe_path(name)
    if path in changes or not path.parent.is_dir():
        raise ValueError('Duplicate target or missing native parent')
    if not path.exists() and not missing:
        raise ValueError('Selected native config must already exist')
    before = path.read_bytes() if path.exists() else None
    value = object_bytes(before, yaml) if before is not None else {}
    result = transform(value)
    if yaml:
        stream = io.StringIO()
        YAML(typ='rt').dump(result, stream)
        after = stream.getvalue().encode()
    else:
        after = encode(result)
    # Keep identical logical files byte-for-byte, including native formatting.
    changes[path] = (before, before if value == result else after)


def replace_private(path, data, owner):
    fd, name = tempfile.mkstemp(prefix='.mcphub-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(name, *owner)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def apply(changes, backup):
    changed = {p: pair for p, pair in changes.items() if pair[0] != pair[1]}
    if not changed:
        return
    if backup.exists():
        raise ValueError('Backup destination must be vacant')
    backup.mkdir(mode=0o700)
    owners = {p: (p.stat().st_uid, p.stat().st_gid) if p.exists() else
              (p.parent.stat().st_uid, p.parent.stat().st_gid) for p in changed}
    # Back up all beforeimages and manifest before the first native write.
    manifest = {}
    for index, (path, (before, _)) in enumerate(changed.items()):
        name = str(index)
        manifest[str(path)] = name if before is not None else None
        if before is not None:
            replace_private(backup / name, before, (os.geteuid(), os.getegid()))
    replace_private(backup / 'manifest.json', encode(manifest), (os.geteuid(), os.getegid()))
    written = []
    try:
        for path, (_, after) in changed.items():
            replace_private(path, after, owners[path])
            written.append(path)
    except OSError:
        for path in reversed(written):
            before = changed[path][0]
            if before is None:
                path.unlink()
            else:
                replace_private(path, before, owners[path])
        raise


def validate_locations(state, changes, backup, applying):
    if applying and (backup is None or not backup.parent.is_dir() or backup.exists()):
        raise ValueError('Apply needs a vacant backup directory beneath an existing private parent')
    protected = [state, *(p.parent for p in changes)]
    if backup and any(backup == p or p in backup.parents for p in protected):
        raise ValueError('Backups must be outside state and native discovery')
    if any(state == p or state in p.parents for p in changes):
        raise ValueError('Native configs must be outside hub state')


def run(args):
    state = safe_path(args.state_dir, directory=True)
    files, tokens = state_plan(state)
    targets = object_bytes(safe_path(args.targets).read_bytes()) if args.targets else None
    changes = client_plan(targets, tokens) if targets else {}
    backup = safe_path(args.backup_dir, directory=True) if args.backup_dir else None
    validate_locations(state, changes, backup, args.apply)
    if files and os.geteuid() == 0 and not args.state_owner:
        raise ValueError('Root must explicitly select a non-root service state owner')
    print('Preflight OK: empty-state creation=' + str(bool(files)) + '; selected native files=' + str(len(changes)))
    if not args.apply:
        print('Preview only; no writes or service changes')
        return
    # Every selected target is validated before any state/native write.
    if files:
        state.mkdir(mode=0o700)
        owner = tuple(map(int, args.state_owner.split(':'))) if args.state_owner else (os.geteuid(), os.getegid())
        os.chown(state, *owner)
        for name, data in files.items():
            replace_private(state / name, data, owner)
    apply(changes, backup)
    print('Provisioned selected files only; no services started. Restart selected clients after activation.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--targets')
    parser.add_argument('--backup-dir')
    parser.add_argument('--state-owner', help='Explicit service UID:GID when preparing as root')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if args.state_owner:
            uid, gid = map(int, args.state_owner.split(':'))
            if uid < 1 or gid < 1:
                raise ValueError('Non-root state owner required')
        run(args)
    except (ValueError, KeyError, TypeError, OSError, YAMLError):
        # Native parser messages can contain credential-bearing source text.
        parser.exit(1, 'MCPHub preparation refused: check selected paths, permissions, schema and occupied entries\n')


if __name__ == '__main__':
    main()
