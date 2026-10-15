#!/usr/bin/env python3
"""Prepare scoped Hermes memory settings; dry-run unless --apply is supplied."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode
from yaml.tokens import AliasToken

try:
    from .hermes_log_remediation import Change, UniqueLoader, _read, apply
except ImportError:
    from hermes_log_remediation import Change, UniqueLoader, _read, apply


def _real_path(path: Path) -> None:
    # Refuse symlinked ancestors as well as the final destination.
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("symlinked configuration paths are refused")


def _directory(path: Path) -> None:
    _real_path(path)
    if not path.is_dir():
        raise ValueError("expected a real configuration directory")


def _parse(text: str):
    if any(isinstance(token, AliasToken) for token in yaml.scan(text)):
        raise ValueError("configuration aliases are refused")
    data = yaml.load(text, Loader=UniqueLoader)
    node = yaml.compose(text, Loader=UniqueLoader)
    if not isinstance(data, dict) or not isinstance(node, MappingNode):
        raise ValueError("expected a configuration mapping")
    return data, node


def _child(node: MappingNode, name: str):
    for key, value in node.value:
        if isinstance(key, ScalarNode) and key.tag == 'tag:yaml.org,2002:str' and key.value == name:
            return value
    return None


def _insert(text: str, node: MappingNode, key: str, value: str) -> str:
    newline = '\r\n' if '\r\n' in text else '\n'
    if node.flow_style:
        position = node.end_mark.index - 1
        addition = (', ' if node.value else '') + f'{key}: {value}'
    else:
        position = node.end_mark.index - node.end_mark.column
        # An EOF without a newline requires appending after the last value.
        if node.end_mark.index == len(text) and text and not text.endswith('\n'):
            position = len(text)
        indent = node.value[0][0].start_mark.column if node.value else node.start_mark.column
        addition = (newline if position and text[position - 1] != '\n' else '')
        addition += ' ' * indent + f'{key}: {value}' + newline
    return text[:position] + addition + text[position:]


def _set(text: str, path: tuple[str, ...], value: int | str) -> str:
    _, node = _parse(text)
    for index, key in enumerate(path):
        child = _child(node, key)
        if index == len(path) - 1:
            if child is None:
                return _insert(text, node, key, str(value))
            if not isinstance(child, ScalarNode):
                raise ValueError("memory setting must be a scalar")
            return text[:child.start_mark.index] + str(value) + text[child.end_mark.index:]
        if child is None:
            # Only the optional lsp section may be introduced.
            if path != ('lsp', 'idle_timeout') or index != 0:
                raise ValueError("required Compose mapping is absent")
            return _insert(text, node, key, '{idle_timeout: 300}')
        if not isinstance(child, MappingNode):
            raise ValueError("memory setting parent must be a mapping")
        node = child
    raise AssertionError("empty setting path")


def _change(path: Path, settings: list[tuple[tuple[str, ...], int | str]]) -> Change | None:
    _real_path(path)
    original, mode, uid, gid = _read(path)
    text = original.decode('utf-8')
    before, _ = _parse(text)
    expected = copy.deepcopy(before)
    for keys, value in settings:
        parent = expected
        for key in keys[:-1]:
            if key not in parent and keys == ('lsp', 'idle_timeout'):
                parent[key] = {}
            parent = parent.get(key)
            if not isinstance(parent, dict):
                raise ValueError("memory setting parent must be a mapping")
        if keys[-1] in parent and isinstance(parent[keys[-1]], (dict, list)):
            raise ValueError("memory setting must be a scalar")
        parent[keys[-1]] = value
        text = _set(text, keys, value)
    after, _ = _parse(text)
    if after != expected:
        raise ValueError("unexpected configuration semantic change")
    replacement = text.encode('utf-8')
    return Change(path, original, replacement, mode, uid, gid) if replacement != original else None


def plan(home: Path, compose: Path) -> list[Change]:
    _directory(home)
    homes = [home]
    profiles = home / 'profiles'
    if profiles.exists() or profiles.is_symlink():
        _directory(profiles)
        for profile in sorted(profiles.iterdir()):
            _real_path(profile)
            if profile.is_dir():
                config = profile / 'config.yaml'
                if config.exists() or config.is_symlink():
                    homes.append(profile)
    changes = []
    for profile in homes:
        change = _change(profile / 'config.yaml', [(('lsp', 'idle_timeout'), 300)])
        if change:
            changes.append(change)
    change = _change(compose, [(('services', 'hermes', 'mem_limit'), '4g'),
                               (('services', 'hermes', 'memswap_limit'), '6g')])
    if change:
        changes.append(change)
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--compose', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    if args.apply and args.backup_dir is None:
        parser.error('--apply requires an empty owner-only --backup-dir')
    try:
        changes = plan(args.home, args.compose)
        if args.apply:
            _real_path(args.backup_dir)
            apply(changes, args.backup_dir, home=args.home)
    except (OSError, ValueError, yaml.YAMLError, UnicodeError, TypeError, RuntimeError):
        parser.exit(1, 'Configuration preflight or apply failed; inspect inputs privately.\n')
    print(f"{'Applied' if args.apply else 'Planned'} {len(changes)} file changes")


if __name__ == '__main__':
    main()
