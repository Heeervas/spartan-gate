#!/usr/bin/env python3
"""Save an operator-entered tool secret in Hermes' existing managed dotenv."""
from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import uuid
import warnings

class HelperError(ValueError):
    """An operator-safe message containing no input values."""


class SecretSafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, 'Invalid Hermes env arguments; use --help. Secret values must be entered at the hidden prompt.\n')


ROOT = Path(__file__).resolve().parent.parent
KEY_RE = re.compile(r'[A-Z][A-Z0-9_]*\Z')
ASSIGNMENT = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=')
MAX_BYTES = 1024 * 1024
# These are service configuration and executable-loader controls, never tool secrets.
RESERVED = {'PATH', 'HOME', 'USER', 'SHELL', 'ENV', 'BASH_ENV', 'IFS', 'PYTHONPATH',
            'PYTHONHOME', 'NODE_OPTIONS', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
            'ALL_PROXY', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'SEARXNG_URL',
            'PLAYWRIGHT_BROWSERS_PATH'}
PREFIXES = ('HERMES_', '_HERMES_', 'SPARTAN_', 'S6_', 'CAMOFOX_', 'BROWSERLESS_',
            'CLAWROUTE_', 'COMPOSE_', 'DOCKER_', 'GATEWAY_', 'LD_', 'DYLD_')

# No document, secret, exception or third-party output is returned to the terminal.
NATIVE_CHECK = r'''
import contextlib, io, json, os, sys, tempfile
sys.path.insert(0, '/opt/hermes')
request = json.load(sys.stdin)
different = False
try:
    with tempfile.TemporaryDirectory(prefix='hermes-env-') as isolated, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        os.environ.clear()
        os.environ.update(HOME=isolated, HERMES_HOME=isolated, HERMES_DEFAULT_ROOT=isolated, HERMES_MANAGED_DIR=isolated, PATH='/usr/local/bin:/usr/bin:/bin')
        from hermes_constants import set_hermes_home_override
        set_hermes_home_override(isolated)
        from tools.environments.local_env_policy import _is_provider_env_blocklisted, _is_hermes_internal_secret
        from agent.secret_scope import _is_global_env
        key = request['key']
        if _is_provider_env_blocklisted(key) or _is_hermes_internal_secret(key) or _is_global_env(key):
            raise ValueError()
        if 'text' in request:
            from agent.secret_scope import _parse_env_text
            from dotenv import dotenv_values
            from dotenv.parser import parse_stream
            text = request['text']
            bindings = list(parse_stream(io.StringIO(text)))
            if any(b.error or (b.key is not None and len(b.original.string.rstrip('\r\n').splitlines()) != 1) for b in bindings):
                raise ValueError()
            native = _parse_env_text(text)
            startup = dict(dotenv_values(stream=io.StringIO(text)))
            if native != startup:
                raise ValueError()
            if 'secret' in request and native.get(key) != request['secret']:
                different = True
except BaseException:
    sys.exit(1)
print('different' if different else 'ok')
'''


def compose(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ['bash', '--noprofile', '--norc', '-c',
         'set +x; source "$1"; shift; sg_compose "$@"', 'hermes-env',
         str(ROOT / 'scripts/aliases.sh'), *args], input=input_text,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise HelperError('Compose operation failed; review the deployment configuration separately')
    return result.stdout


def native_check(key: str, text: str | None = None, secret: str | None = None) -> bool:
    request = {'key': key}
    if text is not None:
        request['text'] = text
    if secret is not None:
        request['secret'] = secret
    config = json.loads(compose('config', '--format', 'json'))
    environment = config['services']['hermes'].get('environment') or {}
    uid = str(environment.get('SPARTAN_HERMES_RUN_UID', ''))
    gid = str(environment.get('SPARTAN_HERMES_RUN_GID', ''))
    if any(not re.fullmatch(r'[0-9]+', v) or not 1 <= int(v) <= 65534 for v in (uid, gid)):
        raise HelperError('Hermes workload identity must be configured for native validation')
    try:
        output = compose('exec', '-T', '--user', uid + ':' + gid, '-w', '/opt/hermes', 'hermes',
                         '/opt/hermes/.venv/bin/python', '-I', '-c', NATIVE_CHECK,
                         input_text=json.dumps(request))
    except HelperError:
        if text is None:
            raise HelperError('Installed Hermes denied this key or its native policy is unavailable; use a tool API secret key') from None
        raise HelperError('Native dotenv validation failed; no managed secret was written') from None
    if output.strip() not in {'ok', 'different'}:
        raise HelperError('Installed Hermes policy or dotenv validation failed')
    return output.strip() == 'ok'


def validate_key(key: str, environment: dict) -> None:
    if not KEY_RE.fullmatch(key):
        raise HelperError('Use an uppercase environment key, for example SERVICE_API_KEY')
    if key in RESERVED or key.startswith(PREFIXES) or key in {k.upper() for k in environment}:
        raise HelperError('Service and infrastructure environment keys cannot be changed here')


def discover_source(config: dict, root: Path) -> Path:
    service = config.get('services', {}).get('hermes', {})
    environment = service.get('environment') or {}
    directory = environment.get('HERMES_MANAGED_SOURCE_DIR') or '/etc/hermes'
    target = PurePosixPath(directory) / '.env'
    if not target.is_absolute() or '..' in target.parts:
        raise HelperError('Managed source directory must be an absolute normalized container path')
    matches = []
    for volume in service.get('volumes', []):
        destination = PurePosixPath(volume.get('target', ''))
        if destination == target or destination in target.parents:
            matches.append(volume)
    if len(matches) != 1 or matches[0].get('type') != 'bind':
        raise HelperError('Managed dotenv needs one unambiguous existing bind mount')
    volume = matches[0]
    if volume.get('read_only') is not True:
        raise HelperError('Managed dotenv bind mount must be read-only in Hermes')
    source = Path(volume.get('source', ''))
    if not source.is_absolute():
        raise HelperError('Compose must resolve the managed source to an absolute host path')
    source = source.joinpath(*target.relative_to(PurePosixPath(volume['target'])).parts)
    if '..' in source.parts:
        raise HelperError('Managed source path is unsafe')
    for component in [source, *source.parents]:
        if component.is_symlink():
            raise HelperError('Managed source path must not contain symlinks')
    info = source.stat()
    parent = source.parent.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
        raise HelperError('Managed dotenv must be a bounded, unlinked regular file')
    if info.st_uid != os.geteuid() or parent.st_uid != os.geteuid():
        raise HelperError('Managed dotenv and directory must be owned by the current operator')
    if stat.S_IMODE(info.st_mode) != 0o600 or stat.S_IMODE(parent.st_mode) != 0o700:
        raise HelperError('Managed dotenv requires file mode 0600 and directory mode 0700')
    if not os.access(source, os.W_OK) or not os.access(source.parent, os.W_OK):
        raise HelperError('Managed dotenv and directory must be writable')
    forbidden = [root / '.env', root / 'private/env/local.env', root / 'private/env/private.env']
    for path in forbidden:
        if source.name in {'local.env', 'private.env'} or (path.exists() and os.path.samefile(source, path)):
            raise HelperError('Compose environment files cannot be used as the managed dotenv')
    return source


def encode_secret(secret: str) -> str:
    if not secret or len(secret.encode('utf-8')) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in secret):
        raise HelperError('Secret must be a nonempty single line of at most 4096 bytes')
    if '${' in secret:
        raise HelperError('Dotenv interpolation syntax is unsupported in secret values')
    return '"' + secret.replace('\\', '\\\\').replace('"', '\\"') + '"'


def prepare_update(original: str, key: str, secret: str) -> str:
    encoded = encode_secret(secret)
    replacement = f'{key}={encoded}'
    lines = original.splitlines(keepends=True)
    found = False
    updated = []
    for line in lines:
        match = ASSIGNMENT.match(line)
        if match and match.group(1) == key:
            if found:
                raise HelperError('Managed dotenv contains duplicate assignments for this key')
            found = True
            # Preserve a target assignment's inline comment, including its spacing.
            value = line[match.end():].rstrip('\r\n')
            comment = ''
            quoted = value.lstrip().startswith(("'", '"'))
            if quoted:
                quote = value.lstrip()[0]
                start = len(value) - len(value.lstrip()) + 1
                index = start
                while index < len(value):
                    if value[index] == '\\':
                        index += 2
                        continue
                    if value[index] == quote:
                        comment = value[index + 1:]
                        break
                    index += 1
            else:
                suffix = re.search(r'\s+#.*$', value)
                if suffix:
                    comment = suffix.group()
            ending = '\r\n' if line.endswith('\r\n') else '\n' if line.endswith('\n') else ''
            updated.append(replacement + comment + ending)
        else:
            updated.append(line)
    if not found:
        if original and not original.endswith('\n'):
            updated.append('\n')
        updated.append(replacement + '\n')
    return ''.join(updated)


def atomic_file(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def source_signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def update_secret(path: Path, key: str, secret: str, validate=native_check) -> bool:
    for component in [path, *path.parents]:
        if component.is_symlink():
            raise HelperError('Managed source path must not contain symlinks')
    info = path.stat()
    parent_info = path.parent.stat()
    if info.st_uid != os.geteuid() or parent_info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600 or stat.S_IMODE(parent_info.st_mode) != 0o700:
        raise HelperError('Managed dotenv requires operator ownership, file mode 0600 and directory mode 0700')
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
        raise HelperError('Managed dotenv must be a bounded, unlinked regular file')
    original_bytes = path.read_bytes()
    original = original_bytes.decode('utf-8-sig')
    validate(key, original, None)
    updated = prepare_update(original, key, secret)
    updated_bytes = updated.encode('utf-8-sig' if original_bytes.startswith(b'\xef\xbb\xbf') else 'utf-8')
    if len(updated_bytes) > MAX_BYTES:
        raise HelperError('Updated managed dotenv exceeds the supported size limit')
    if validate(key, updated, secret) is False:
        raise HelperError('Secret does not roundtrip through native dotenv parsers')
    if updated == original or validate(key, original, secret) is True:
        return False
    # Fail before any write if another editor changed the captured source.
    if (source_signature(path.lstat()) != source_signature(info) or
            source_signature(path.parent.lstat()) != source_signature(parent_info) or
            path.read_bytes() != original_bytes):
        raise HelperError('Managed dotenv changed during validation; retry')
    backup = path.with_name(path.name + '.bak.' + uuid.uuid4().hex)
    atomic_file(backup, original_bytes)
    atomic_file(path, updated_bytes)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def main(argv=None) -> int:
    parser = SecretSafeParser(prog='sg-hermes env', description=__doc__)
    parser.add_argument('command', choices=['set'])
    parser.add_argument('key', nargs='?')
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--apply', action='store_true', help='recreate only Hermes after saving')
    choice.add_argument('--no-apply', action='store_true', help='save for later without an apply prompt')
    args = parser.parse_args(argv)
    try:
        config = json.loads(compose('config', '--format', 'json'))
        path = discover_source(config, ROOT)
        with contextlib.ExitStack() as terminals:
            try:
                reader = terminals.enter_context(open('/dev/tty', 'r'))
                writer = terminals.enter_context(open('/dev/tty', 'w'))
            except OSError:
                raise HelperError('A controlling terminal is required for hidden secret entry; run sg-hermes env set from an interactive terminal') from None
            key = args.key
            if key is None:
                writer.write('Environment key: ')
                writer.flush()
                key = reader.readline().strip()
            validate_key(key, config['services']['hermes'].get('environment') or {})
            native_check(key)
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                secret = getpass.getpass('Secret (hidden): ', stream=writer)
            changed = update_secret(path, key, secret)
            secret = ''
            if not changed:
                print('Secret is unchanged; Hermes was not recreated.')
                return 0
            print('Saved shared tool secret with a private backup.')
            apply = args.apply
            if not args.apply and not args.no_apply:
                writer.write('1) Recreate only Hermes now\n2) Save for later\nChoice [2]: ')
                writer.flush()
                answer = reader.readline().strip()
                if answer not in {'', '1', '2'}:
                    raise HelperError('Invalid choice; secret is saved for later')
                apply = answer == '1'
        if apply:
            compose('config', '--quiet')
            compose('up', '-d', '--no-build', '--no-deps', '--force-recreate', 'hermes')
            print('Recreated only Hermes; shared secrets remain subject to native tool and profile filters.')
        else:
            print('Saved for later. Recreate only Hermes to activate the managed secret.')
        return 0
    except HelperError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, ValueError, KeyboardInterrupt, EOFError, getpass.GetPassWarning):
        # Never render exception strings: parser/OS/Compose errors may contain secrets.
        print('Hermes env operation stopped safely. Check the managed mount, permissions, native key policy and terminal access. Any completed save remains available for later activation.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
