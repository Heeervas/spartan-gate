#!/usr/bin/env python3
"""Build-time patcher: restore /rollback handlers with scoped checkpoint lookup.

Upstream main currently dispatches /rollback in the gateway and CLI, and ships
the v2 checkpoint store helpers, but the handler methods are absent. Spartan
Gate keeps the handler small and conservative:

* resolve Docker's generic cwd from terminal.cwd/env when possible;
* if the active cwd has no checkpoints, scan known checkpoint projects;
* auto-select only one unambiguous project;
* refuse numeric restore/diff across multiple candidate projects.
"""

from __future__ import annotations

import sys
from pathlib import Path


CM_TARGET = Path("/opt/hermes/tools/checkpoint_manager.py")
GW_TARGET = Path("/opt/hermes/gateway/run.py")
CLI_TARGET = Path("/opt/hermes/cli.py")

CM_MARKER = "# --- Spartan Gate checkpoint scope helpers ---"
GW_MARKER = "# --- Spartan Gate gateway rollback handler ---"
CLI_MARKER = "# --- Spartan Gate CLI rollback handler ---"

CM_PATCH = r'''

# --- Spartan Gate checkpoint scope helpers ---
_SPARTAN_CHECKPOINT_SYSTEM_DIRS = {
    "/",
    "/opt",
    "/opt/hermes",
    "/tmp",
}


def _spartan_resolve_configured_terminal_cwd() -> str | None:
    """Read terminal.cwd from HERMES_HOME/config.yaml when available."""
    try:
        import yaml
    except Exception:
        return None

    config_path = CHECKPOINT_BASE.parent / "config.yaml"
    if not config_path.exists():
        return None

    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None

    terminal = config.get("terminal") if isinstance(config, dict) else None
    cwd = terminal.get("cwd") if isinstance(terminal, dict) else None
    if not isinstance(cwd, str):
        return None

    cwd = cwd.strip()
    if cwd in ("", ".", "auto", "cwd"):
        return None
    return str(Path(cwd).expanduser())


def resolve_checkpoint_cwd(default_cwd: str) -> str:
    """Resolve the best cwd for checkpoint lookups inside containers."""
    for candidate in (
        os.getenv("TERMINAL_CWD"),
        os.getenv("MESSAGING_CWD"),
        _spartan_resolve_configured_terminal_cwd(),
        default_cwd,
    ):
        if not candidate:
            continue
        candidate = str(Path(candidate).expanduser())
        if candidate not in _SPARTAN_CHECKPOINT_SYSTEM_DIRS:
            return candidate
    return default_cwd


def _spartan_broad_checkpoint_dir(workdir: str) -> bool:
    workdir = str(Path(workdir).expanduser())
    if workdir in _SPARTAN_CHECKPOINT_SYSTEM_DIRS:
        return True
    if workdir.startswith("/opt/data"):
        suffix = workdir[len("/opt/data"):].strip("/")
        return suffix in ("", "workspace", "brain", "workspace/projects")
    return False


def list_all_checkpoint_dirs() -> list:
    """Return [(workdir, checkpoints), ...] for non-broad checkpoint projects."""
    results = {}
    store = _store_path()
    if (store / "HEAD").exists():
        try:
            for meta in _list_projects(store):
                workdir = meta.get("workdir") if isinstance(meta, dict) else None
                if not workdir or _spartan_broad_checkpoint_dir(workdir):
                    continue
                checkpoints = CheckpointManager(enabled=True).list_checkpoints(workdir)
                if checkpoints:
                    results[str(Path(workdir).expanduser())] = checkpoints
        except Exception:
            pass

    # Backward-compatible scan for pre-v2 per-project shadow repos.
    if CHECKPOINT_BASE.exists():
        for shadow in sorted(CHECKPOINT_BASE.iterdir()):
            if not shadow.is_dir():
                continue
            workdir_file = shadow / "HERMES_WORKDIR"
            if not workdir_file.exists() or not (shadow / "HEAD").exists():
                continue
            workdir = workdir_file.read_text(encoding="utf-8").strip()
            if not workdir or _spartan_broad_checkpoint_dir(workdir):
                continue
            checkpoints = CheckpointManager(enabled=True).list_checkpoints(workdir)
            if checkpoints:
                results.setdefault(workdir, checkpoints)

    return [(workdir, checkpoints) for workdir, checkpoints in results.items()]


def match_checkpoint_dirs(active_cwd: str, all_dirs: list) -> list:
    """Return fallback dirs only when scope can be inferred safely."""
    if len(all_dirs) == 1:
        return all_dirs

    active = str(Path(active_cwd).expanduser())
    exact = []
    nested = []
    parents = []
    for workdir, checkpoints in all_dirs:
        workdir = str(Path(workdir).expanduser())
        if workdir == active:
            exact.append((workdir, checkpoints))
        elif workdir.startswith(active.rstrip("/") + "/"):
            nested.append((workdir, checkpoints))
        elif active.startswith(workdir.rstrip("/") + "/"):
            parents.append((workdir, checkpoints))

    if exact:
        return exact
    if len(nested) == 1:
        return nested
    if len(parents) == 1:
        return parents
    return []


def format_all_checkpoints_list(all_dirs: list) -> str:
    """Format checkpoint lists from multiple dirs without global indices."""
    if not all_dirs:
        return "No checkpoints found in any directory."

    lines = []
    for workdir, checkpoints in all_dirs:
        lines.append(f"\n📸 Checkpoints for {workdir}:")
        for idx, checkpoint in enumerate(checkpoints, start=1):
            timestamp = checkpoint["timestamp"]
            if "T" in timestamp:
                time_part = timestamp.split("T")[1].split("+")[0].split("-")[0][:5]
                timestamp = f"{timestamp.split('T')[0]} {time_part}"
            files = checkpoint.get("files_changed", 0)
            if files:
                stat = (
                    f"  ({files} file{'s' if files != 1 else ''}, "
                    f"+{checkpoint.get('insertions', 0)}/-{checkpoint.get('deletions', 0)})"
                )
            else:
                stat = ""
            lines.append(
                f"  {idx}. {checkpoint['short_hash']}  {timestamp}  "
                f"{checkpoint['reason']}{stat}"
            )

    lines.append("\nAmbiguous rollback scope: multiple workdirs have checkpoints.")
    lines.append("Set terminal.cwd in config.yaml or open the intended project before using /rollback.")
    lines.append("Grouped restore/diff stays disabled until the active cwd is narrowed to one project.")
    return "\n".join(lines)
'''

GW_PATCH = r'''

# --- Spartan Gate gateway rollback handler ---
async def _spartan_gateway_handle_rollback_command(self, event):
    from pathlib import Path
    from tools.checkpoint_manager import (
        CheckpointManager,
        format_all_checkpoints_list,
        format_checkpoint_list,
        list_all_checkpoint_dirs,
        match_checkpoint_dirs,
        resolve_checkpoint_cwd,
    )

    mgr = CheckpointManager(enabled=True)
    active_cwd = resolve_checkpoint_cwd(str(Path.home()))
    arg = event.get_command_args().strip()
    checkpoints = mgr.list_checkpoints(active_cwd)
    all_dirs = None

    if not checkpoints:
        all_dirs = list_all_checkpoint_dirs()
        matched_dirs = match_checkpoint_dirs(active_cwd, all_dirs)
        if len(matched_dirs) == 1:
            active_cwd, checkpoints = matched_dirs[0]
            all_dirs = None
        elif matched_dirs:
            all_dirs = matched_dirs

    if not arg:
        if all_dirs:
            return format_all_checkpoints_list(all_dirs)
        if not checkpoints:
            return f"No checkpoints found for {active_cwd}"
        return format_checkpoint_list(checkpoints, active_cwd)

    if all_dirs:
        return format_all_checkpoints_list(all_dirs)
    if not checkpoints:
        return "No checkpoints found"

    if arg.lower().startswith("diff"):
        diff_parts = arg.split(None, 1)
        if len(diff_parts) < 2:
            return "Usage: /rollback diff <N>"
        target_hash = _spartan_resolve_gateway_checkpoint_ref(diff_parts[1], checkpoints)
        if target_hash is None:
            return f"Invalid checkpoint number. Use 1-{len(checkpoints)}."
        result = mgr.diff(active_cwd, target_hash)
        if not result["success"]:
            return f"❌ {result['error']}"
        stat = result.get("stat", "")
        diff = result.get("diff", "")
        if not stat and not diff:
            return "No changes since this checkpoint."
        parts = [part for part in (stat, _spartan_limit_checkpoint_diff(diff)) if part]
        return "\n".join(parts)

    restore_parts = arg.split(None, 1)
    target_hash = _spartan_resolve_gateway_checkpoint_ref(restore_parts[0], checkpoints)
    if target_hash is None:
        return f"Invalid checkpoint number. Use 1-{len(checkpoints)}."
    file_path = restore_parts[1] if len(restore_parts) > 1 else None
    result = mgr.restore(active_cwd, target_hash, file_path=file_path)
    if not result["success"]:
        return f"❌ {result['error']}"
    if file_path:
        msg = f"✅ Restored {file_path} from checkpoint {result['restored_to']}: {result['reason']}"
    else:
        msg = f"✅ Restored to checkpoint {result['restored_to']}: {result['reason']}"
    return msg + "\nA pre-rollback snapshot was saved automatically."


def _spartan_resolve_gateway_checkpoint_ref(ref: str, checkpoints: list) -> str | None:
    try:
        idx = int(ref) - 1
    except ValueError:
        return ref
    if 0 <= idx < len(checkpoints):
        return checkpoints[idx]["hash"]
    return None


def _spartan_limit_checkpoint_diff(diff: str) -> str:
    diff_lines = diff.splitlines()
    if len(diff_lines) <= 80:
        return diff
    return "\n".join(diff_lines[:80]) + f"\n... ({len(diff_lines) - 80} more lines)"


GatewayRunner._handle_rollback_command = _spartan_gateway_handle_rollback_command
'''

CLI_PATCH = r'''

# --- Spartan Gate CLI rollback handler ---
def _spartan_cli_handle_rollback_command(self, command):
    import os
    from tools.checkpoint_manager import (
        CheckpointManager,
        format_all_checkpoints_list,
        format_checkpoint_list,
        list_all_checkpoint_dirs,
        match_checkpoint_dirs,
        resolve_checkpoint_cwd,
    )

    mgr = None
    if hasattr(self, "agent") and self.agent and hasattr(self.agent, "_checkpoint_mgr"):
        mgr = self.agent._checkpoint_mgr
    if mgr is None or not mgr.enabled:
        if getattr(self, "checkpoints_enabled", False):
            mgr = CheckpointManager(
                enabled=True,
                max_snapshots=getattr(self, "checkpoint_max_snapshots", 50),
            )
        else:
            print("  Checkpoints are not enabled.")
            print("  Enable with: hermes --checkpoints")
            print("  Or in config.yaml: checkpoints: { enabled: true }")
            return

    active_cwd = resolve_checkpoint_cwd(os.getcwd())
    parts = command.split()
    args = parts[1:] if len(parts) > 1 else []
    checkpoints = mgr.list_checkpoints(active_cwd)
    all_dirs = None

    if not checkpoints:
        all_dirs = list_all_checkpoint_dirs()
        matched_dirs = match_checkpoint_dirs(active_cwd, all_dirs)
        if len(matched_dirs) == 1:
            active_cwd, checkpoints = matched_dirs[0]
            all_dirs = None
        elif matched_dirs:
            all_dirs = matched_dirs

    if not args:
        if all_dirs:
            print(format_all_checkpoints_list(all_dirs))
        elif not checkpoints:
            print(f"  No checkpoints found for {active_cwd}")
        else:
            print(format_checkpoint_list(checkpoints, active_cwd))
        return

    if all_dirs:
        print(format_all_checkpoints_list(all_dirs))
        return
    if not checkpoints:
        print("  No checkpoints found")
        return

    if args[0].lower() == "diff":
        if len(args) < 2:
            print("  Usage: /rollback diff <N>")
            return
        target_hash = self._resolve_checkpoint_ref(args[1], checkpoints)
        if not target_hash:
            return
        result = mgr.diff(active_cwd, target_hash)
        if result["success"]:
            _spartan_cli_print_checkpoint_diff(result)
        else:
            print(f"  ❌ {result['error']}")
        return

    target_hash = self._resolve_checkpoint_ref(args[0], checkpoints)
    if not target_hash:
        return
    file_path = args[1] if len(args) > 1 else None
    result = mgr.restore(active_cwd, target_hash, file_path=file_path)
    if result["success"]:
        if file_path:
            print(f"  ✅ Restored {file_path} from checkpoint {result['restored_to']}: {result['reason']}")
        else:
            print(f"  ✅ Restored to checkpoint {result['restored_to']}: {result['reason']}")
        print("  A pre-rollback snapshot was saved automatically.")
        if self.conversation_history:
            self.undo_last()
            print("  Chat turn undone to match restored file state.")
    else:
        print(f"  ❌ {result['error']}")


def _spartan_cli_print_checkpoint_diff(result):
    stat = result.get("stat", "")
    diff = result.get("diff", "")
    if not stat and not diff:
        print("  No changes since this checkpoint.")
        return
    if stat:
        print(f"\n{stat}")
    if diff:
        diff_lines = diff.splitlines()
        if len(diff_lines) > 80:
            print("\n".join(diff_lines[:80]))
            print(f"\n  ... ({len(diff_lines) - 80} more lines, showing first 80)")
        else:
            print(f"\n{diff}")


HermesCLI._handle_rollback_command = _spartan_cli_handle_rollback_command
'''


def _append_once(path: Path, marker: str, patch: str) -> None:
    if not path.exists():
        print(f"FATAL: {path} not found", file=sys.stderr)
        sys.exit(1)

    source = path.read_text(encoding="utf-8")
    if marker in source:
        print(f"SKIP: {path.name} already contains {marker}")
        return

    path.write_text(source.rstrip() + "\n" + patch, encoding="utf-8")
    print(f"OK: appended {marker} to {path}")


def _require_callsite(path: Path, text: str) -> None:
    source = path.read_text(encoding="utf-8")
    if text not in source:
        print(f"FATAL: expected rollback callsite not found in {path}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    _append_once(CM_TARGET, CM_MARKER, CM_PATCH)
    _require_callsite(GW_TARGET, "return await self._handle_rollback_command(event)")
    _append_once(GW_TARGET, GW_MARKER, GW_PATCH)
    _require_callsite(CLI_TARGET, "self._handle_rollback_command(cmd_original)")
    _append_once(CLI_TARGET, CLI_MARKER, CLI_PATCH)


if __name__ == "__main__":
    main()
