#!/usr/bin/env python3
"""Add conservative project selection to upstream's native rollback handlers.

Upstream owns restore, hand-edit protection, backend checks, and chat undo.
Only resolve the container cwd and select a single unambiguous project here.
"""
from pathlib import Path
import ast

ROOT = Path('/opt/hermes')
CM_TARGET = ROOT / 'tools/checkpoint_manager.py'
GW_TARGET = ROOT / 'gateway/slash_commands.py'
CLI_TARGET = ROOT / 'hermes_cli/cli_commands_mixin.py'
CM_MARKER = '# Spartan Gate checkpoint scope selection'
CM_PATCH = '''

# Spartan Gate checkpoint scope selection
def spartan_checkpoint_scope(mgr, cwd):
    """Return (cwd, diagnostic); never restore across ambiguous projects."""
    from hermes_constants import get_hermes_home
    from tools.terminal_scope import terminal_env
    from hermes_cli.config import read_raw_config
    broad = {"/", "/opt", "/opt/hermes", "/tmp", "/opt/data", "/opt/data/workspace", "/opt/data/brain", "/opt/data/workspace/projects"}
    if cwd in broad:
        config = read_raw_config()
        configured = config.get("terminal", {}).get("cwd") if isinstance(config, dict) else None
        for value in (terminal_env("TERMINAL_CWD", ""), terminal_env("MESSAGING_CWD", ""), configured):
            if isinstance(value, str) and value not in broad | {"", ".", "auto", "cwd"}:
                cwd = str(Path(value).expanduser())
                break
    if mgr.list_checkpoints(cwd) or mgr.unsupported_backend_reason():
        return cwd, None
    grouped = {}
    for entry in mgr.list_all_checkpoints():
        workdir = entry.get("workdir")
        if workdir and workdir not in broad:
            grouped.setdefault(workdir, []).append(entry)
    if not grouped:
        return cwd, None
    candidates = list(grouped)
    if len(candidates) != 1:
        nested = [p for p in candidates if p.startswith(cwd.rstrip("/") + "/")]
        parents = [p for p in candidates if cwd.startswith(p.rstrip("/") + "/")]
        candidates = nested if len(nested) == 1 else parents if len(parents) == 1 else []
    if len(candidates) == 1:
        return candidates[0], None
    listing = "\\n".join(format_checkpoint_list(entries, directory) for directory, entries in grouped.items())
    return cwd, listing + "\\nAmbiguous rollback scope: set terminal.cwd to the intended project before restore/diff."
'''


def patch_handler(source: str, *, gateway: bool) -> str:
    marker = '# Spartan Gate: resolve native rollback scope'
    if marker in source:
        return source
    methods = [n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == '_handle_rollback_command']
    if len(methods) != 1:
        raise ValueError('expected one native rollback handler')
    method = methods[0]
    lines = source.splitlines(keepends=True)
    block = ''.join(lines[method.lineno - 1:method.end_lineno])
    anchor = '        cwd = self._terminal_cwd()\n' if gateway else '        cwd = os.getenv("TERMINAL_CWD", os.getcwd())\n'
    if block.count(anchor) != 1:
        raise ValueError('native rollback cwd anchor changed')
    addition = anchor + f'''        {marker}
        from tools.checkpoint_manager import spartan_checkpoint_scope
        cwd, diagnostic = spartan_checkpoint_scope(mgr, cwd)
        if diagnostic:
            return {"diagnostic" if gateway else "print(diagnostic)"}
'''
    lines[method.lineno - 1:method.end_lineno] = [block.replace(anchor, addition, 1)]
    return ''.join(lines)


def main():
    # Preflight every source before the first write.
    original = {p: p.read_text() for p in (CM_TARGET, GW_TARGET, CLI_TARGET)}
    changed = {
        CM_TARGET: original[CM_TARGET] if CM_MARKER in original[CM_TARGET] else original[CM_TARGET] + CM_PATCH,
        GW_TARGET: patch_handler(original[GW_TARGET], gateway=True),
        CLI_TARGET: patch_handler(original[CLI_TARGET], gateway=False),
    }
    for p, source in changed.items():
        compile(source, str(p), 'exec')
    for p, source in changed.items():
        if source != original[p]:
            p.write_text(source)
    print('Verified native rollback scope adapters')


if __name__ == '__main__':
    main()
