#!/usr/bin/env python3
"""Supervise only the bounded Workstation application SSH tunnel."""

from __future__ import annotations

import argparse
import ctypes
from functools import partial
import importlib.util
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "scripts" / "workstation-bridge.py"
HEALTH_FILE = "app-tunnel.health.json"
PR_SET_PDEATHSIG = 1
STABLE_SECONDS = 30
MAX_BACKOFF_SECONDS = 30.0
ATTEMPT_READY_TIMEOUT_SECONDS = 10.0


def arm_parent_death_signal(expected_parent_pid: int) -> None:
    """Ensure the SSH child cannot outlive an abruptly killed supervisor."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0) != 0:
        os._exit(126)
    if os.getppid() != expected_parent_pid:
        os.kill(os.getpid(), signal.SIGTERM)


class SupervisorError(RuntimeError):
    pass


def load_bridge_module():
    spec = importlib.util.spec_from_file_location(
        "spartan_workstation_bridge_for_tunnel", BRIDGE_PATH
    )
    if spec is None or spec.loader is None:
        raise SupervisorError("cannot load workstation bridge validation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish_health(
    bridge,
    state_path: Path,
    state: str,
    *,
    child_pid: int | None,
    restart_count: int,
    child_start_time: str | None = None,
    retry_seconds: float = 0,
) -> None:
    bridge.write_private_json(
        state_path / HEALTH_FILE,
        {
            "version": 1,
            "supervisor_pid": os.getpid(),
            "supervisor_start_time": bridge.process_start_time(os.getpid()),
            "state": state,
            "child_pid": child_pid,
            "child_start_time": child_start_time,
            "restart_count": restart_count,
            "retry_seconds": retry_seconds,
            "updated_at": time.time(),
        },
    )


def validate_known_hosts(bridge, config: dict[str, Any], path: Path) -> None:
    try:
        info = path.lstat()
        expected, _execution = bridge.known_hosts_lines(config, "127.0.0.1")
        actual = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SupervisorError("cannot read app-tunnel known_hosts") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or actual != expected
    ):
        raise SupervisorError("app-tunnel known_hosts is unsafe or changed")


def supervise(config_path: Path, state_path: Path) -> int:
    bridge = load_bridge_module()
    if not state_path.is_absolute():
        raise SupervisorError("app-tunnel state directory must be absolute")
    config = bridge.load_config(config_path)
    bridge.validate_state_dir(state_path)
    enabled_apps = [app for app in config["apps"] if app["enabled"]]
    if not enabled_apps:
        raise SupervisorError("app-tunnel supervision requires an enabled app")
    known_hosts = state_path / "known_hosts.remote"
    control_socket = state_path / "app-tunnel.control"
    validate_known_hosts(bridge, config, known_hosts)
    command = bridge.app_ssh_command(config, known_hosts, control_socket)
    remote = config["remote"]
    control_check = [
        command[0],
        "-F",
        "/dev/null",
        "-S",
        str(control_socket),
        "-O",
        "check",
        "-p",
        str(remote["ssh_port"]),
        f"{remote['user']}@{remote['host']}",
    ]

    stopping = threading.Event()
    parent_death_guard = partial(arm_parent_death_signal, os.getpid())
    child_lock = threading.RLock()
    child: subprocess.Popen | None = None

    def stop_handler(_signum, _frame):
        stopping.set()
        with child_lock:
            current = child
        if current is not None and current.poll() is None:
            try:
                current.terminate()
            except ProcessLookupError:
                pass

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    failures = 0
    restart_count = 0
    publish_health(
        bridge,
        state_path,
        "starting",
        child_pid=None,
        restart_count=restart_count,
    )

    while not stopping.is_set():
        control_socket.unlink(missing_ok=True)
        started_at = time.monotonic()
        try:
            spawned = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                preexec_fn=parent_death_guard,
            )
        except OSError:
            spawned = None
        with child_lock:
            child = spawned
        if spawned is not None:
            publish_health(
                bridge,
                state_path,
                "starting",
                child_pid=spawned.pid,
                child_start_time=bridge.process_start_time(spawned.pid),
                restart_count=restart_count,
            )
            try:
                spawned.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                ready = False
                ready_deadline = time.monotonic() + ATTEMPT_READY_TIMEOUT_SECONDS
                while (
                    not stopping.is_set()
                    and spawned.poll() is None
                    and time.monotonic() < ready_deadline
                ):
                    try:
                        checked = subprocess.run(
                            control_check,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2,
                            check=False,
                        )
                    except subprocess.TimeoutExpired:
                        checked = None
                    if checked is not None and checked.returncode == 0:
                        ready = True
                        break
                    stopping.wait(0.1)
                if not ready and spawned.poll() is None:
                    spawned.terminate()
                if ready:
                    publish_health(
                        bridge,
                        state_path,
                        "ready",
                        child_pid=spawned.pid,
                        child_start_time=bridge.process_start_time(spawned.pid),
                        restart_count=restart_count,
                    )
                while not stopping.is_set() and spawned.poll() is None:
                    stopping.wait(0.25)
                if stopping.is_set() and spawned.poll() is None:
                    spawned.terminate()
                try:
                    spawned.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    spawned.kill()
                    spawned.wait(timeout=2)
        with child_lock:
            child = None
        if stopping.is_set():
            break
        lifetime = time.monotonic() - started_at
        failures = 0 if lifetime >= STABLE_SECONDS else failures + 1
        restart_count += 1
        retry_seconds = min(
            0.25 * (2 ** min(max(failures - 1, 0), 7)),
            MAX_BACKOFF_SECONDS,
        )
        publish_health(
            bridge,
            state_path,
            "restarting",
            child_pid=None,
            restart_count=restart_count,
            retry_seconds=retry_seconds,
        )
        stopping.wait(retry_seconds)

    control_socket.unlink(missing_ok=True)
    publish_health(
        bridge,
        state_path,
        "stopped",
        child_pid=None,
        restart_count=restart_count,
    )
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, required=True)
    result.add_argument("--state-dir", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        return supervise(args.config, args.state_dir)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
