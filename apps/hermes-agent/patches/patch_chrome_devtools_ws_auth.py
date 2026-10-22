#!/usr/bin/env python3
"""Remove only Spartan-managed retired CDP settings; preserve custom tooling.

Compatibility filename retained for existing bootstrap callers. No browser
profiles or credential stores are opened by this configuration migration.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

MANAGED_VALUES = {'${BROWSER_CDP_URL}', '${BROWSER_CDP_MAIN_URL}'}


def managed(value: object) -> bool:
    return isinstance(value, str) and (
        value in MANAGED_VALUES or value.startswith('ws://browserless:3000')
        or value.startswith('http://browserless:3000')
    )


def get_targets() -> list[Path]:
    home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    return [home / 'config.yaml', *sorted((home / 'profiles').glob('*/config.yaml'))]


def get_env_targets() -> list[Path]:
    home = Path(os.environ.get('HERMES_HOME', '/opt/data'))
    return [home / '.env', *sorted((home / 'profiles').glob('*/.env'))]


def clean_browser(browser: dict) -> None:
    if managed(browser.get('cdp_url')):
        del browser['cdp_url']
    profiles = browser.get('profiles')
    if isinstance(profiles, dict):
        for profile in profiles.values():
            if isinstance(profile, dict) and managed(profile.get('cdp_url')):
                del profile['cdp_url']


def clean_config(config: dict) -> None:
    browser = config.get('browser')
    if isinstance(browser, dict):
        clean_browser(browser)
    servers = config.get('mcp_servers')
    if not isinstance(servers, dict):
        return
    devtools = servers.get('chrome_devtools')
    if not isinstance(devtools, dict):
        return
    args = devtools.get('args', [])
    if isinstance(args, list) and any(
        isinstance(arg, str) and ('${BROWSER_CDP_' in arg or 'browserless:3000' in arg)
        for arg in args
    ):
        del servers['chrome_devtools']


def patch_file(path: Path) -> str:
    if not path.exists():
        return f'Skip: {path} missing'
    source = path.read_text(encoding='utf-8')
    config = yaml.safe_load(source)
    if not isinstance(config, dict):
        raise ValueError('Hermes config must be a mapping')
    before = yaml.safe_dump(config, sort_keys=False)
    clean_config(config)
    updated = yaml.safe_dump(config, sort_keys=False)
    if before == updated:
        return f'No-op: {path}'
    path.write_text(updated, encoding='utf-8')
    return f'Patched: {path} - removed managed retired CDP settings'


def patch_env_file(path: Path) -> str:
    if not path.exists():
        return f'Skip: {path} missing'
    source = path.read_text(encoding='utf-8')
    lines = source.splitlines(keepends=True)
    updated = ''.join(line for line in lines if not (
        line.partition('=')[0].strip() in {
            'BROWSER_CDP_LAUNCH_URL', 'BROWSER_CDP_URL', 'BROWSER_CDP_MAIN_URL',
        } and (line.partition('=')[2].strip().strip('\"\'') in MANAGED_VALUES
               or managed(line.partition('=')[2].strip().strip('\"\'')))
    ))
    if updated == source:
        return f'No-op: {path}'
    path.write_text(updated, encoding='utf-8')
    return f'Patched: {path} - removed managed retired CDP env'


def main() -> None:
    for path in get_targets():
        print(patch_file(path))
    for path in get_env_targets():
        print(patch_env_file(path))


if __name__ == '__main__':
    main()
