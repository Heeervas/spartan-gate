#!/usr/bin/env python3
"""Root-created browser policy shared by Hermes' profile-scoped runtimes.

The managed dotenv supplies infrastructure credentials through Hermes' native
secret_scope. The strict mode document separately prevents Chromium fallback;
upstream's managed overlay deliberately ignores malformed configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from urllib.parse import urlsplit


MANAGED_DIR = Path('/run/spartan-hermes-managed')
SELECTORS = {
    'tools/browser_camofox.py': 'is_camofox_mode',
    'tools/browser_use_cli.py': 'is_browser_use_cli_mode',
    'tools/browser_tool_cloud.py': '_resolve_cloud_provider_uncached',
    'tools/browser_tool_cdp.py': '_get_cdp_override_raw',
    'tools/tool_backend_helpers.py': 'read_selection',
}
MARKER = 'SPARTAN_CAMOFOX_MANAGED_POLICY_V1'
_cache = None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_permissions(st, mode: int, owner_uid: int, gid: int) -> None:
    if st.st_uid != owner_uid or st.st_gid != gid or stat.S_IMODE(st.st_mode) != mode:
        raise RuntimeError('Spartan browser policy has unsafe ownership or permissions')


def _protected_bytes(path: Path, owner_uid: int, gid: int) -> bytes:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
        raise RuntimeError('Spartan browser policy must contain bounded regular files')
    _check_permissions(st, 0o640, owner_uid, gid)
    # A workload cannot replace entries in this root-owned directory.
    return path.read_bytes()


def validate_policy(directory: Path, owner_uid: int, gid: int) -> dict:
    st = directory.lstat()
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError('Spartan browser policy directory must not be a symlink')
    _check_permissions(st, 0o750, owner_uid, gid)
    document = json.loads(_protected_bytes(directory / 'mode.json', owner_uid, gid))
    if (not isinstance(document, dict) or set(document) != {'schema', 'mode', 'files'}
            or type(document['schema']) is not int or document['schema'] != 1
            or document['mode'] not in ('camofox', 'unmanaged')
            or not isinstance(document['files'], dict)
            or set(document['files']) != {'.env', 'config.yaml'}):
        raise RuntimeError('Spartan browser policy document is invalid')
    for name, expected in document['files'].items():
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise RuntimeError('Spartan browser policy checksum is invalid')
        if digest(_protected_bytes(directory / name, owner_uid, gid)) != expected:
            raise RuntimeError('Spartan browser policy contents do not match the validated policy')
    return document


def camofox_policy_enabled() -> bool:
    global _cache
    enabled = os.environ.get('SPARTAN_BROWSER_POLICY_ENABLED', '')
    if not enabled and not MANAGED_DIR.exists():
        return False  # Unmodified upstream deployments keep their own semantics.
    if enabled != '1' or os.environ.get('HERMES_MANAGED_DIR') != str(MANAGED_DIR):
        raise RuntimeError('Spartan browser policy deployment settings are invalid')
    gid = _identity(os.environ.get('SPARTAN_HERMES_RUN_GID', ''), 'runtime GID')
    try:
        paths = [MANAGED_DIR, *(MANAGED_DIR / name for name in ('mode.json', '.env', 'config.yaml'))]
        stats = [p.lstat() for p in paths]
        signature = tuple((st.st_mode, st.st_uid, st.st_gid, st.st_ino,
                           st.st_size, st.st_mtime_ns, st.st_ctime_ns) for st in stats)
        key = (gid, signature)
        if _cache is not None and _cache[0] == key:
            return _cache[1]
        document = validate_policy(MANAGED_DIR, 0, gid)
        result = document['mode'] == 'camofox'
        _cache = (key, result)
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError('Spartan browser policy is missing or invalid; restart Hermes after fixing deployment') from exc


def _identity(value: str, label: str) -> int:
    if not re.fullmatch(r'[0-9]+', value) or not 1 <= int(value) <= 65534:
        raise ValueError(f'Invalid {label}')
    return int(value)


def _source_bytes(path: Path) -> bytes | None:
    if not path.exists():
        if path.is_symlink():
            raise ValueError('Managed source must not be a dangling symlink')
        return None
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
        raise ValueError('Managed source must be a bounded regular file')
    return path.read_bytes()


def native_env_values(raw: bytes) -> dict[str, str]:
    # Parse exactly the captured bytes, with the same tokenizer secret_scope
    # uses later. A second file read could observe a different admin update.
    from agent.secret_scope import _decode_env_bytes, _parse_env_text
    return _parse_env_text(_decode_env_bytes(raw))


def prepare_documents(env: dict, source_dir: Path) -> dict[str, bytes]:
    import yaml
    from dotenv.parser import parse_stream
    import io

    source_config = _source_bytes(source_dir / 'config.yaml')
    config = yaml.safe_load(source_config.decode('utf-8-sig')) if source_config else {}
    config = {} if config is None else config
    if not isinstance(config, dict):
        raise ValueError('Managed config must be a mapping')
    source_env = _source_bytes(source_dir / '.env')
    if source_env:
        from agent.secret_scope import _decode_env_bytes
        bindings = list(parse_stream(io.StringIO(_decode_env_bytes(source_env))))
        if any(binding.error or (binding.key is not None and
                len(binding.original.string.rstrip('\r\n').splitlines()) != 1) for binding in bindings):
            raise ValueError('Managed dotenv contains a malformed or multiline assignment')
    inherited = native_env_values(source_env) if source_env else {}
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) or not isinstance(v, str) for k, v in inherited.items()):
        raise ValueError('Managed dotenv contains an invalid assignment')

    url = env.get('CAMOFOX_URL', '')
    mode = 'unmanaged'
    browser = config.get('browser', {})
    if not isinstance(browser, dict):
        raise ValueError('Managed browser config must be a mapping')
    if url:
        parsed = urlsplit(url)
        if (url != url.strip() or any(ord(c) < 33 or ord(c) == 127 for c in url)
                or parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('CAMOFOX_URL must be an HTTP(S) control endpoint without credentials or a path')
        parsed.port  # Validate malformed/out-of-range ports before publication.
        key = env.get('CAMOFOX_API_KEY', '')
        if not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError('Camofox mode requires a valid protected-route API key')
        mode = 'camofox'
        config['browser'] = {**browser, 'cloud_provider': 'camofox', 'backend': 'off',
                             'use_gateway': False, 'cdp_url': ''}
        inherited.update({'CAMOFOX_URL': url.rstrip('/'), 'CAMOFOX_API_KEY': key})
        access_key = env.get('CAMOFOX_ACCESS_KEY', '')
        if access_key:
            if access_key != key:
                raise ValueError('Camofox API and protected-route access keys must match')
            inherited['CAMOFOX_ACCESS_KEY'] = access_key
    elif browser.get('cloud_provider') == 'camofox' or inherited.get('CAMOFOX_URL'):
        raise ValueError('Managed Camofox configuration conflicts with the selected Spartan browser mode')

    files = {
        'config.yaml': yaml.safe_dump(config, sort_keys=True).encode(),
        '.env': ''.join(k + '="' + v.replace('\\', '\\\\').replace('"', '\\"') + '"\n'
                       for k, v in sorted(inherited.items())).encode(),
    }
    if native_env_values(files['.env']) != inherited:
        raise ValueError('Managed dotenv serialization does not preserve native Hermes values')
    files['mode.json'] = (json.dumps({'schema': 1, 'mode': mode,
                           'files': {name: digest(value) for name, value in files.items()}}, sort_keys=True) + '\n').encode()
    return files


def publish_policy(files: dict[str, bytes], directory: Path, owner_uid: int, gid: int) -> None:
    if directory.exists() or directory.is_symlink():
        validate_policy(directory, owner_uid, gid)
        if all((directory / name).read_bytes() == value for name, value in files.items()):
            return
        raise ValueError('Browser policy changed during initialization; recreate Hermes to apply it')
    staged = Path(tempfile.mkdtemp(prefix='.spartan-browser-policy-', dir=directory.parent))
    try:
        os.chown(staged, owner_uid, gid)
        os.chmod(staged, 0o750)
        for name, value in files.items():
            path = staged / name
            path.write_bytes(value)
            os.chown(path, owner_uid, gid)
            os.chmod(path, 0o640)
        validate_policy(staged, owner_uid, gid)
        os.rename(staged, directory)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def validate_code(root: Path, manifest_path: Path | None) -> None:
    import ast

    guards = {
        'is_camofox_mode': 'if _spartan_camofox_policy():\n return True',
        'is_browser_use_cli_mode': 'if _spartan_camofox_policy():\n return False',
        '_get_cdp_override_raw': 'if _spartan_camofox_policy():\n return ""',
        'read_selection': 'if section == "browser" and _spartan_camofox_policy():\n return "camofox"',
        '_resolve_cloud_provider_uncached': (
            'if _spartan_camofox_policy():\n'
            ' _bt = _origin()\n'
            ' _bt._cached_cloud_provider = None\n'
            ' _bt._cloud_provider_resolved = True\n'
            ' return None'),
    }
    for name, function in SELECTORS.items():
        text = (root / name).read_text()
        tree = ast.parse(text)
        matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function]
        if len(matches) != 1:
            raise ValueError('Browser policy selector is incompatible with installed Hermes')
        body = matches[0].body
        offset = 1 if (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                       and isinstance(body[0].value.value, str)) else 0
        expected = ast.parse(
            'from spartan_browser_policy import camofox_policy_enabled as _spartan_camofox_policy\n'
            + guards[function]).body
        if (len(body) <= offset + 1 or MARKER not in text
                or [ast.dump(node) for node in body[offset:offset + 2]]
                   != [ast.dump(node) for node in expected]):
            raise ValueError('Installed Hermes lacks the mandatory browser policy guards')
        compile(text, name, 'exec')
    if manifest_path is None:
        return  # Built-in adaptation; the image owns the installation tree.
    manifest = json.loads(manifest_path.read_text())
    if (not isinstance(manifest, dict) or manifest.get('schema') != 1
            or manifest.get('image') != os.environ.get('SPARTAN_HERMES_CODE_IMAGE')
            or not isinstance(manifest.get('files'), dict)
            or not isinstance(manifest.get('anchors'), dict)
            or not isinstance(manifest.get('support'), dict)
            or set(manifest['files']) != set(SELECTORS)):
        raise ValueError('Browser code override manifest is incompatible')
    required = {'hermes_cli/config.py', 'agent/secret_scope.py', 'hermes_constants.py',
                'tools/browser_tool.py', 'pyproject.toml'}
    if set(manifest['anchors']) != required:
        raise ValueError('Browser code override manifest lacks image compatibility anchors')
    for name, expected in {**manifest['files'], **manifest['anchors']}.items():
        if digest((root / name).read_bytes()) != expected:
            raise ValueError(f'Browser code override fingerprint mismatch: {name}')
    support = {'/opt/hermes/bootstrap/spartan_browser_policy.py',
               '/opt/hermes/spartan_browser_policy.py', '/etc/cont-init.d/0195-spartan-browser-policy'}
    if set(manifest['support']) != support:
        raise ValueError('Browser code override support files are incompatible')
    for name, expected in manifest['support'].items():
        if digest(Path(name).read_bytes()) != expected:
            raise ValueError('Browser code override support fingerprint changed')


def cold_import_preflight(uid: int, gid: int) -> None:
    import subprocess
    import sys

    # Run imports as the workload user in a disposable home, with no channel
    # credentials or user profile files. Never import plugins as bootstrap root.
    with tempfile.TemporaryDirectory(prefix='.spartan-browser-import-', dir='/run') as raw:
        os.chown(raw, uid, gid)
        environment = {
            'PATH': '/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin',
            'PYTHONPATH': '/opt/hermes', 'HOME': raw, 'HERMES_HOME': raw,
            'SPARTAN_BROWSER_POLICY_ENABLED': '1', 'HERMES_MANAGED_DIR': str(MANAGED_DIR),
            'SPARTAN_HERMES_RUN_UID': str(uid), 'SPARTAN_HERMES_RUN_GID': str(gid),
        }
        code = ('from spartan_browser_policy import camofox_policy_enabled\n'
                'from tools.browser_camofox import is_camofox_mode\n'
                'from tools.browser_use_cli import is_browser_use_cli_mode\n'
                'from tools.browser_tool_cdp import _get_cdp_override_raw\n'
                'from tools.browser_tool_cloud import _resolve_cloud_provider_uncached\n'
                'from tools.tool_backend_helpers import read_selection\n'
                'import tools.browser_tool\n'
                'if camofox_policy_enabled():\n'
                ' assert is_camofox_mode()\n'
                ' assert not is_browser_use_cli_mode()\n'
                ' assert not _get_cdp_override_raw()\n'
                ' assert read_selection("browser") == "camofox"\n'
                ' assert _resolve_cloud_provider_uncached() is None\n')
        result = subprocess.run(['/command/s6-applyuidgid', '-u', str(uid), '-g', str(gid),
                                 sys.executable, '-P', '-B', '-c', code], cwd='/opt/hermes', env=environment,
                                capture_output=True, timeout=30, check=False)
        if result.returncode:
            raise RuntimeError('Hermes browser selectors failed their unprivileged cold import preflight')


def validate_init_runner(pid: int) -> None:
    # Only the native cont-init runner may be interrupted. A manual invocation
    # must never signal the operator's shell or another unrelated caller.
    process = Path('/proc') / str(pid)
    expected = Path('/package/admin/s6-overlay/etc/s6-rc/scripts/cont-init').resolve(strict=True)
    shell = Path('/bin/sh').resolve(strict=True)
    argv = process.joinpath('cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    if (process.joinpath('exe').resolve(strict=True) != shell or len(argv) not in (2, 3)
            or Path(os.fsdecode(argv[0])).resolve(strict=True) != shell
            or (len(argv) == 3 and argv[1] != b'-e')
            or Path(os.fsdecode(argv[-1])).resolve(strict=True) != expected):
        raise RuntimeError('Spartan browser policy requires the native s6 cont-init runner')


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError('Spartan browser policy must initialize as root')
    if os.environ.get('SPARTAN_BROWSER_POLICY_ENABLED') != '1' or os.environ.get('HERMES_MANAGED_DIR') != str(MANAGED_DIR):
        raise ValueError('Spartan browser policy requires its fixed managed directory and deployment flag')
    gid = _identity(os.environ.get('SPARTAN_HERMES_RUN_GID', ''), 'runtime GID')
    uid = _identity(os.environ.get('SPARTAN_HERMES_RUN_UID', ''), 'runtime UID')
    source = Path(os.environ.get('HERMES_MANAGED_SOURCE_DIR', '/etc/hermes'))
    if source == MANAGED_DIR or source.is_symlink():
        raise ValueError('Managed source directory is invalid')
    raw_manifest = os.environ.get('SPARTAN_HERMES_CODE_MANIFEST', '')
    validate_code(Path('/opt/hermes'), Path(raw_manifest) if raw_manifest else None)
    documents = prepare_documents(dict(os.environ), source)
    publish_policy(documents, MANAGED_DIR, 0, gid)
    cold_import_preflight(uid, gid)
    mode = validate_policy(MANAGED_DIR, 0, gid)['mode']
    print(f'[browser-policy] validated {mode} mode for all Hermes profiles')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never include parser errors or raw source values: managed dotenv can contain credentials.
        print(f'[browser-policy] FATAL: browser initialization rejected ({type(exc).__name__})', file=__import__('sys').stderr)
        raise SystemExit(1) from None
