#!/usr/bin/env python3
"""Prepare narrow, idempotent profile-scope repairs for installed user plugins."""

from __future__ import annotations

import ast

TALK_MARKER = "# Spartan Gate: preserve the transcript owner's context in its worker."
NODE_MARKER = "# Spartan Gate: read node-search policy from the current profile scope."


def patch_talk_transcript(source: str) -> str:
    original = '''    worker = threading.Thread(
        target=_finish_claim,
        args=(claimed, transcript_fd, lease, _default_run_agent),'''
    replacement = '''    # Spartan Gate: preserve the transcript owner's context in its worker.
    from contextvars import copy_context

    owner_context = copy_context()
    worker = threading.Thread(
        target=owner_context.run,
        args=(_finish_claim, claimed, transcript_fd, lease, _default_run_agent),'''
    if TALK_MARKER in source:
        if source.count(replacement) != 1:
            raise ValueError("incomplete transcript scope repair")
        return source
    if source.count(original) != 1:
        raise ValueError("transcript worker anchor changed")
    return source.replace(original, replacement, 1)


def patch_node_search(source: str) -> str:
    helper = '''# Spartan Gate: read node-search policy from the current profile scope.
def _profile_env(name: str) -> str:
    try:
        from agent.secret_scope import get_secret
    except ModuleNotFoundError as exc:
        if exc.name not in {"agent", "agent.secret_scope"}:
            raise
        # Standalone use has no Hermes profile authority.
        return os.getenv(name, "").strip()
    # A scoped miss or failed lookup must never borrow the launch profile's policy.
    return (get_secret(name) or "").strip()


'''
    if NODE_MARKER in source:
        if helper not in source or any(f'os.getenv("{key}"' in source for key in ["NODE_SEARCH_ALLOWED_ROOTS", "NODE_SEARCH_ROOT", "NODE_SEARCH_CACHE"]):
            raise ValueError("incomplete node-search scope repair")
        return source
    if source.count("import yaml\n") != 1:
        raise ValueError("node-search import anchor changed")
    for key, count in [("NODE_SEARCH_ALLOWED_ROOTS", 2), ("NODE_SEARCH_ROOT", 2), ("NODE_SEARCH_CACHE", 1)]:
        old = f'os.getenv("{key}", "")'
        if source.count(old) != count:
            raise ValueError("node-search policy anchor changed")
        source = source.replace(old, f'_profile_env("{key}")')
    return source.replace("import yaml\n", "import yaml\n\n" + helper, 1)


def _replace_function(source: str, name: str, replacement: str) -> str:
    matches = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise ValueError(f"missing or ambiguous plugin function: {name}")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    return "".join(lines[:node.lineno - 1]) + replacement + "".join(lines[node.end_lineno:])


TOOLARIA_HOME = '''# Spartan Gate: never select a multiplexed profile's store from process environment.
def _profile_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
    except ModuleNotFoundError as exc:
        if exc.name != "hermes_constants":
            raise
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve()
    return Path(get_hermes_home()).resolve()


def _safe_cfg(ctx) -> dict:
    import yaml

    path = _profile_home() / "config.yaml"
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or not isinstance(raw.get("toolaria", {}), dict):
        raise ValueError("invalid profile Toolaria configuration")
    cfg = dict(raw.get("toolaria", {}))
    # Native PluginContext exposes plugin-relative settings through get_config.
    get_config = getattr(ctx, "get_config", None)
    if callable(get_config):
        absent = object()
        for key in set(_load_defaults()) | set(cfg) | {"store_path"}:
            value = get_config(key, absent)
            if value is not absent:
                cfg[key] = value
    return cfg
'''
TOOLARIA_AMBIENT = '''def _mark_rescuer_ambient() -> bool:
    """Only rescue when the host guarantees retrieval in restricted sessions."""
    try:
        from tools.registry import registry
        mark = getattr(registry, "mark_ambient", None)
        if not callable(mark):
            logger.info("toolaria: ambient retrieval unavailable; oversized results pass through unchanged")
            return False
        mark(_RESCUER_TOOLSET)
    except Exception:
        logger.warning("toolaria: ambient retrieval registration failed; oversized results pass through unchanged")
        return False
    return True
'''
TOOLARIA_PATH = '''    # Spartan Gate: bind the configured store to the registration profile.
    hermes_home = _profile_home()
    if not isinstance(store_path, str) or not store_path.strip():
        raise ValueError("invalid Toolaria store path")
    if store_path == "~/.hermes":
        candidate = hermes_home
    elif store_path.startswith("~/.hermes/"):
        candidate = hermes_home / store_path.removeprefix("~/.hermes/")
    else:
        candidate = Path(store_path).expanduser()
        if not candidate.is_absolute():
            candidate = hermes_home / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(hermes_home):
        raise ValueError("Toolaria store must remain inside its profile home")
    defaults["store_path"] = str(candidate)
'''
TOOLARIA_PATH_OLD = '''    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home and isinstance(store_path, str):
        if store_path == "~/.hermes":
            defaults["store_path"] = hermes_home
        elif store_path.startswith("~/.hermes/"):
            defaults["store_path"] = str(
                Path(hermes_home) / store_path.removeprefix("~/.hermes/")
            )
'''
FETCH_IDENTITY = '''    session_id = kwargs.get("session_id", "")
    if not isinstance(session_id, str) or not session_id.strip():
        return "Error: rescuer requires an owning session"
'''
TRANSFORM_GUARD = '''    """Replace oversized tool results with excerpt + rescue handle."""
    if not _retrieval_available or not isinstance(session_id, str) or not session_id.strip():
        return None
'''


def patch_toolaria(source: str) -> str:
    if TOOLARIA_HOME.splitlines()[0] in source:
        required = [TOOLARIA_HOME, TOOLARIA_PATH, TOOLARIA_AMBIENT, FETCH_IDENTITY, TRANSFORM_GUARD,
                    '    _retrieval_available = _mark_rescuer_ambient()']
        if any(source.count(text) != 1 for text in required):
            raise ValueError("incomplete Toolaria profile repair")
        return source
    anchors = [TOOLARIA_PATH_OLD, '    global _store, _cfg\n', '    _mark_rescuer_ambient()\n',
               '    session_id = kwargs.get("session_id", "")\n',
               '    """Replace oversized tool results with excerpt + rescue handle."""\n',
               '        return _store._audit_summary(count)\n']
    if any(source.count(anchor) != 1 for anchor in anchors):
        raise ValueError("Toolaria repair anchor changed")
    source = _replace_function(source, '_safe_cfg', TOOLARIA_HOME)
    source = _replace_function(source, '_mark_rescuer_ambient', TOOLARIA_AMBIENT)
    source = source.replace(TOOLARIA_PATH_OLD, TOOLARIA_PATH, 1)
    source = source.replace('    global _store, _cfg\n', '    global _store, _cfg, _retrieval_available\n    _retrieval_available = False\n', 1)
    source = source.replace('    _mark_rescuer_ambient()\n', '    _retrieval_available = _mark_rescuer_ambient()\n', 1)
    source = source.replace('    session_id = kwargs.get("session_id", "")\n', FETCH_IDENTITY, 1)
    source = source.replace('        return _store._audit_summary(count)\n', '        return "Error: audit is available only through the operator command"\n', 1)
    source = source.replace('    """Replace oversized tool results with excerpt + rescue handle."""\n', TRANSFORM_GUARD, 1)
    source = source.replace('_passref_alive: bool = False\n', '_passref_alive: bool = False\n_retrieval_available: bool = False\n', 1)
    source = source.replace('"chain", "stat", "full", "audit"],', '"chain", "stat", "full"],', 1)
    compile(source, 'toolaria/__init__.py', 'exec')
    return source


def patch_toolaria_passref(source: str) -> str:
    original = '''    if "tla:" not in text:
        return text, False
'''
    replacement = original + '''    # Spartan Gate: an unidentified caller must never expand a stored handle.
    if not isinstance(session_id, str) or not session_id.strip():
        return TOKEN_RE.sub("[Toolaria: owning session required]", text), True
'''
    if '# Spartan Gate: an unidentified caller' in source:
        if source.count(replacement) != 1:
            raise ValueError("incomplete pass-reference identity repair")
        return source
    if source.count(original) != 1:
        raise ValueError("pass-reference identity anchor changed")
    return source.replace(original, replacement, 1)
