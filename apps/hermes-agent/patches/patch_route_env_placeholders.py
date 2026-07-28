#!/usr/bin/env python3
'''Patch Hermes route-token .env placeholders to delegate to route secrets.

Spartan Gate profile .env files may intentionally delegate route keys to the
container environment, including the legacy-to-native alias
OPENAI_API_KEY=${CUSTOM_1_API_KEY}. Native Hermes provider auth prefers .env
values, so without this patch that literal placeholder is treated as a usable
bearer token and sent upstream. Resolve only explicit route-key references,
keeping other profile secrets scoped and fail-closed.
'''

from __future__ import annotations

from pathlib import Path
from typing import Callable

CONFIG_TARGET = Path("/opt/hermes/hermes_cli/config.py")
SECRET_SCOPE_TARGET = Path("/opt/hermes/agent/secret_scope.py")
CREDENTIAL_POOL_TARGET = Path("/opt/hermes/agent/credential_pool.py")
CONFIG_MARKER = "# Spartan Gate patch: route env placeholder delegation (config)"
SECRET_MARKER = "# Spartan Gate patch: route env placeholder delegation (secret_scope)"
CREDENTIAL_POOL_MARKER = "# Spartan Gate patch: route env placeholder delegation (credential_pool)"
ROUTE_REFERENCES = (
    "{'OPENAI_API_KEY': ('OPENAI_API_KEY', 'CUSTOM_1_API_KEY'), "
    "'CUSTOM_1_API_KEY': ('CUSTOM_1_API_KEY', 'OPENAI_API_KEY')}"
)


def reference_helper(argument: str, marker: str) -> str:
    return f'''def _spartan_route_env_reference(value: object, {argument}: str) -> Optional[str]:\n    {marker}\n    references = {ROUTE_REFERENCES}.get({argument}, ())\n    text = str(value).strip()\n    for candidate in references:\n        if text == "$" + "{{" + candidate + "}}":\n            return candidate\n    return None\n\n\n'''


def patch_config_source(source: str) -> str:
    if CONFIG_MARKER in source:
        return source

    helper_anchor = "def get_env_value_prefer_dotenv(key: str) -> Optional[str]:\n"
    helper = reference_helper("key", CONFIG_MARKER)
    if helper_anchor not in source:
        raise ValueError("get_env_value_prefer_dotenv anchor not found")
    patched = source.replace(helper_anchor, helper + helper_anchor, 1)

    old = '''    env_vars = load_env()\n    val = env_vars.get(key)\n    if val:\n        return val\n'''
    new = '''    env_vars = load_env()\n    val = env_vars.get(key)\n    reference = _spartan_route_env_reference(val, key)\n    if reference:\n        val = os.environ.get(reference)\n    if val:\n        return val\n'''
    if old not in patched:
        raise ValueError("dotenv lookup block not found")
    return patched.replace(old, new, 1)


def patch_secret_scope_source(source: str) -> str:
    if SECRET_MARKER in source:
        return source

    helper_anchor = "def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:\n"
    helper = reference_helper("name", SECRET_MARKER)
    if helper_anchor not in source:
        raise ValueError("get_secret anchor not found")
    patched = source.replace(helper_anchor, helper + helper_anchor, 1)

    old = '''    scope = _SECRET_SCOPE.get()\n    if scope is not None:\n        val = scope.get(name)\n        if val is not None:\n            return val\n'''
    new = '''    scope = _SECRET_SCOPE.get()\n    if scope is not None:\n        val = scope.get(name)\n        if val is not None:\n            reference = _spartan_route_env_reference(val, name)\n            if reference:\n                val = os.environ.get(reference)\n            return val if val is not None else default\n'''
    if old not in patched:
        raise ValueError("secret scope lookup block not found")
    return patched.replace(old, new, 1)


def patch_credential_pool_source(source: str) -> str:
    if CREDENTIAL_POOL_MARKER in source:
        return source

    helper_anchor = "def _seed_from_env(provider: str, entries: List[PooledCredential]) -> Tuple[bool, Set[str]]:\n"
    helper = reference_helper("key", CREDENTIAL_POOL_MARKER)
    if helper_anchor not in source:
        raise ValueError("_seed_from_env anchor not found")
    patched = source.replace(helper_anchor, helper + helper_anchor, 1)

    old = '''    def _get_env_prefer_dotenv(key: str) -> str:\n        env_file = load_env()\n        raw = env_file.get(key, "").strip()\n        scoped_value = (_get_secret(key, "") or "").strip()\n'''
    new = '''    def _get_env_prefer_dotenv(key: str) -> str:\n        env_file = load_env()\n        raw = env_file.get(key, "").strip()\n        scoped_value = (_get_secret(key, "") or "").strip()\n        reference = _spartan_route_env_reference(raw, key)\n        if reference:\n            raw = str(_get_secret(reference, "") or "").strip()\n'''
    if old not in patched:
        raise ValueError("credential pool dotenv lookup block not found")
    return patched.replace(old, new, 1)


PatchSource = Callable[[str], str]


def patch_files_atomically(targets: tuple[tuple[Path, PatchSource], ...]) -> list[str]:
    """Preflight every transformation before writing the first target."""

    prepared: list[tuple[Path, str, str]] = []
    for path, patcher in targets:
        original = path.read_text(encoding="utf-8")
        prepared.append((path, original, patcher(original)))

    results: list[str] = []
    for path, original, patched in prepared:
        if patched == original:
            results.append(f"Already patched: {path}")
            continue
        path.write_text(patched, encoding="utf-8")
        results.append(f"Patched: {path}")
    return results


def main() -> None:
    targets = (
        (CONFIG_TARGET, patch_config_source),
        (SECRET_SCOPE_TARGET, patch_secret_scope_source),
        (CREDENTIAL_POOL_TARGET, patch_credential_pool_source),
    )
    for result in patch_files_atomically(targets):
        print(result)


if __name__ == "__main__":
    main()
