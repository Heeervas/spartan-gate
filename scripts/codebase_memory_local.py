#!/usr/bin/env python3
"""Index the selected full Git root once, then exec native analysis-only stdio."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

BINARY_SHA256 = '77745491f0a9c346d6da23eed4d92b5feaa31a27a883c9faa4fa9f47c8183705'
SENSITIVE_DIRS = {'.ssh', '.aws', '.azure', '.gcloud', '.config', '.pi', '.codex',
                  '.hermes', 'private', 'credentials', 'secrets'}
ENV_TEMPLATES = {'.env.example', '.env.sample', '.env.template'}
SENSITIVE_FILES = {'auth.json', 'credentials.json', 'token.json', 'tokens.json',
                   'id_rsa', 'id_ed25519', '.netrc', '.npmrc', '.pypirc'}


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
    result = subprocess.run(['git', '--no-optional-locks', '-C', str(root), *args],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            check=True)
    return result.stdout


def sensitive(path):
    parts = Path(path).parts
    name = parts[-1].lower()
    return (bool(set(p.lower() for p in parts) & SENSITIVE_DIRS)
            or name in SENSITIVE_FILES or (name.startswith('.env') and parts[-1] not in ENV_TEMPLATES)
            or name.startswith(('secret.', 'secrets.', 'credentials.'))
            or name.endswith(('.pem', '.key', '.p12', '.pfx')))


def parse_public_template(specification):
    name, separator, digest = specification.rpartition('=')
    relative = Path(name)
    if (not separator or relative.is_absolute() or str(relative) != name
            or '..' in relative.parts or any(c in name for c in '*?[]')
            or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('Public template requires an exact relative file and SHA-256')
    return name, digest


def public_template_pins(root, visible, specifications):
    approved, seen = set(), set()
    for specification in specifications:
        name, digest = parse_public_template(specification)
        if name in seen:
            raise ValueError('Public template must identify each relative file exactly once')
        seen.add(name)
        if name not in visible:
            continue  # Valid unused pin grants no exemption in this repository.
        if not sensitive(name):
            raise ValueError('Public template must identify a sensitive-path file')
        file = root
        for part in Path(name).parts:
            file = file / part
            if file.is_symlink():
                raise ValueError('Public template path must not contain symlinks')
        if not file.is_file():
            raise ValueError('Public template must be a regular file')
        # Explicit operator permission applies ONLY to this public file's bytes.
        with file.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise ValueError('Public template bytes do not match the approved SHA-256')
        approved.add(name)
    return approved


def repository_root(public_templates=()):
    selected = Path.cwd().resolve()
    root = Path(os.fsdecode(git(selected, 'rev-parse', '--show-toplevel')).removesuffix('\n')).resolve()
    if not selected.is_relative_to(root) or Path.home().resolve().is_relative_to(root):
        raise ValueError('Select a coding repository, never HOME or its ancestor')
    # Tracked paths remain visible even when an ignore rule also matches them.
    paths = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-per-directory=.gitignore')
    visible = {os.fsdecode(p) for p in paths.split(b'\0') if p}
    if any(p.endswith('/') or (root / p).is_dir() for p in visible):
        raise ValueError('Opaque directory inventory; embedded repositories/submodules are unsupported')
    approved = public_template_pins(root, visible, public_templates)
    if any(sensitive(p) and p not in approved for p in visible):
        raise ValueError('Sensitive VCS-visible path; exclude untracked data or remove tracked data first')
    for name in ('.gitignore', '.cbmignore'):
        if (root / name).is_symlink():
            raise ValueError('Root ignore policy must not be a symlink')
    return root


def trusted_state(path, root):
    directory = Path(path).absolute()
    for part in (directory, *directory.parents):
        info = part.lstat()
        if part.is_symlink() or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
            raise ValueError('State ancestry must be trusted and not group/other writable')
    if not directory.is_dir() or directory.stat().st_uid != os.getuid():
        raise ValueError('State parent must be an existing directory owned by this user')
    if directory.resolve().is_relative_to(root):
        raise ValueError('State must be outside the indexed repository')
    return directory


def native_setup(binary, root, env, state):
    for key in ('ui_enabled', 'auto_watch', 'watcher_enabled', 'auto_index'):
        subprocess.run([str(binary), 'config', 'set', key, 'false'], cwd=root, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    with tempfile.TemporaryFile(dir=state) as output:
        subprocess.run([str(binary), 'cli', '--json', 'index_repository',
                        json.dumps({'repo_path': str(root), 'name': 'current_repository', 'mode': 'full',
                                    'doc_candidates': False, 'persistence': False})],
                       cwd=root, env=env, stdout=output, stderr=subprocess.DEVNULL, check=True)
        output.seek(0)
        result = json.loads(output.read(1024 * 1024))
    if result.get('isError') is not False or result.get('structuredContent', {}).get('status') != 'indexed':
        raise ValueError('Native indexing did not complete')


def run(binary, state_parent, public_templates=()):
    root = repository_root(public_templates)
    parent = trusted_state(state_parent, root)
    binary = Path(binary).resolve(strict=True)
    with binary.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != BINARY_SHA256:
            raise ValueError('Expected the approved Codebase Memory 0.12.1 program')
    os.umask(0o077)
    state = Path(tempfile.mkdtemp(prefix='session-', dir=parent))
    (state / 'runtime').mkdir(mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith('CBM_')}
    env.update(CBM_CACHE_DIR=str(state), CBM_RUNTIME_DIR=str(state / 'runtime'),
               CBM_ALLOWED_ROOT=str(root), CBM_LOG_LEVEL='error',
               CBM_MEM_BUDGET_MB='256', CBM_WORKERS='1')
    # Native daemons may outlive their CLI/client. Never remove live storage or
    # signal discovered PIDs. Retention is deliberate, including failed launches.
    native_setup(binary, root, env, state)
    os.chdir(root)
    os.execve(binary, [str(binary), '--tool-profile=analysis', '--ui=false'], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, help='Approved native 0.12.1 executable')
    parser.add_argument('--state-parent', required=True, help='Trusted existing cache parent outside source')
    parser.add_argument('--public-template', action='append', default=[], metavar='RELATIVE_PATH=SHA256',
                        help='Operator-approved exact public sensitive-path file; repeatable, no glob/directory')
    args = parser.parse_args()
    try:
        run(args.binary, args.state_parent, args.public_template)
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Codebase Memory refused startup: verify repository exclusions, trusted state and native program; '
              'generated session data is retained for safe operator disposition.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
