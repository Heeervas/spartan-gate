#!/usr/bin/env python3
"""Fail-closed laptop relays for a private Hermes workstation endpoint."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TERMINAL_BROKER = ROOT / "scripts" / "workstation-terminal-broker.py"
DEFAULT_CONFIG = ROOT / "private" / "workstation" / "bridges.json"
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
ALLOWED_HOST_KEY_TYPES = {"ssh-ed25519"}


class WorkstationError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise WorkstationError(message)


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def require_fields(
    value: dict[str, Any],
    label: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    unknown = sorted(set(value) - allowed)
    if unknown:
        fail(f"{label} has unknown field: {unknown[0]}")
    missing = sorted(required - set(value))
    if missing:
        fail(f"{label} is missing field: {missing[0]}")


def require_port(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        fail(f"{label} must be an integer")
    if not 1024 <= value <= 65535:
        fail(f"{label} must be between 1024 and 65535")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{label} must be a non-empty string")
    return value


def parse_host_key(public_key: Any) -> tuple[str, str]:
    raw = require_string(public_key, "remote.host_public_key")
    fields = raw.split()
    if len(fields) < 2:
        fail("remote.host_public_key must contain a key type and base64 payload")
    key_type, blob = fields[:2]
    if key_type not in ALLOWED_HOST_KEY_TYPES:
        fail("remote.host_public_key must be an ssh-ed25519 key")
    try:
        decoded = base64.b64decode(blob, validate=True)
    except (binascii.Error, ValueError):
        fail("remote.host_public_key has invalid base64")
    if not decoded:
        fail("remote.host_public_key has an empty key payload")
    return key_type, blob


def host_key_fingerprint(blob: str) -> str:
    digest = hashlib.sha256(base64.b64decode(blob, validate=True)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def resolve_identity(config_dir: Path, value: Any, label: str) -> Path:
    raw = Path(require_string(value, label))
    path = raw if raw.is_absolute() else config_dir / raw
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} does not exist: {path}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular non-symlink file: {path}")
    if info.st_uid != os.getuid():
        fail(f"{label} must be owned by uid {os.getuid()}: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        fail(f"{label} permissions must not grant group or other access: {path}")
    return path.resolve()


def load_config(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            fail(f"configuration must not be a symlink: {path}")
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fail(f"missing private workstation configuration: {path}")
    except OSError as exc:
        fail(f"cannot read workstation configuration {path}: {exc}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"invalid workstation JSON: {exc}")

    root = require_object(payload, "configuration")
    require_fields(
        root,
        "configuration",
        required={"version", "remote", "execution", "tunnel", "apps"},
        optional={"terminal_broker"},
    )
    if root["version"] != 1:
        fail("configuration.version must be 1")

    remote = require_object(root["remote"], "remote")
    require_fields(
        remote,
        "remote",
        required={
            "host",
            "ssh_port",
            "user",
            "host_public_key",
            "host_key_sha256",
        },
    )
    host_raw = require_string(remote["host"], "remote.host")
    try:
        host = ipaddress.ip_address(host_raw)
    except ValueError:
        fail("remote.host must be a Tailscale IPv4 address")
    if not isinstance(host, ipaddress.IPv4Address) or host not in TAILNET_V4:
        fail("remote.host must be a Tailscale IPv4 address in 100.64.0.0/10")
    remote["ssh_port"] = require_port(remote["ssh_port"], "remote.ssh_port")
    user = require_string(remote["user"], "remote.user")
    if not USER_RE.fullmatch(user):
        fail("remote.user has an invalid Linux username")
    key_type, key_blob = parse_host_key(remote["host_public_key"])
    expected_fingerprint = require_string(
        remote["host_key_sha256"], "remote.host_key_sha256"
    )
    actual_fingerprint = host_key_fingerprint(key_blob)
    if actual_fingerprint != expected_fingerprint:
        fail(
            "remote.host_key_sha256 fingerprint does not match "
            "remote.host_public_key"
        )

    config_dir = path.parent.resolve()
    execution = require_object(root["execution"], "execution")
    require_fields(
        execution,
        "execution",
        required={"gateway_port", "identity_file"},
    )
    execution["gateway_port"] = require_port(
        execution["gateway_port"], "execution.gateway_port"
    )
    execution["_identity_path"] = resolve_identity(
        config_dir, execution["identity_file"], "execution.identity_file"
    )

    terminal_broker = require_object(
        root.get("terminal_broker", {"enabled": False}), "terminal_broker"
    )
    require_fields(
        terminal_broker,
        "terminal_broker",
        required={"enabled"},
        optional={"container_name"},
    )
    if not isinstance(terminal_broker["enabled"], bool):
        fail("terminal_broker.enabled must be true or false")
    terminal_broker.setdefault("container_name", "spartan_gate_hermes")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", terminal_broker["container_name"]):
        fail("terminal_broker.container_name is invalid")
    root["terminal_broker"] = terminal_broker


    tunnel = require_object(root["tunnel"], "tunnel")
    require_fields(tunnel, "tunnel", required={"identity_file"})
    tunnel["_identity_path"] = resolve_identity(
        config_dir, tunnel["identity_file"], "tunnel.identity_file"
    )

    apps = root["apps"]
    if not isinstance(apps, list):
        fail("apps must be an array")
    names: set[str] = set()
    gateway_ports = {execution["gateway_port"]}
    local_ports: set[int] = set()
    for index, item in enumerate(apps):
        label = f"apps[{index}]"
        app = require_object(item, label)
        require_fields(
            app,
            label,
            required={
                "name",
                "enabled",
                "gateway_port",
                "local_port",
                "remote_host",
                "remote_port",
            },
        )
        name = require_string(app["name"], f"{label}.name")
        if not NAME_RE.fullmatch(name):
            fail(f"{label}.name must match {NAME_RE.pattern}")
        if name in names:
            fail(f"duplicate app name: {name}")
        names.add(name)
        if not isinstance(app["enabled"], bool):
            fail(f"{label}.enabled must be true or false")
        app["gateway_port"] = require_port(
            app["gateway_port"], f"{label}.gateway_port"
        )
        app["local_port"] = require_port(app["local_port"], f"{label}.local_port")
        app["remote_port"] = require_port(
            app["remote_port"], f"{label}.remote_port"
        )
        if app["remote_host"] != "127.0.0.1":
            fail(f"{label}.remote_host must be 127.0.0.1")
        if app["gateway_port"] in gateway_ports:
            fail(f"duplicate gateway port: {app['gateway_port']}")
        gateway_ports.add(app["gateway_port"])
        if app["local_port"] in local_ports:
            fail(f"duplicate local forward port: {app['local_port']}")
        local_ports.add(app["local_port"])

    root["_path"] = path.resolve()
    root["_digest"] = hashlib.sha256(raw.encode()).hexdigest()
    root["_host_key_type"] = key_type
    root["_host_key_blob"] = key_blob
    return root


def validate_gateway(raw: str) -> str:
    try:
        gateway = ipaddress.ip_address(raw)
    except ValueError:
        fail("gateway must be a private IPv4 address")
    if (
        not isinstance(gateway, ipaddress.IPv4Address)
        or gateway.is_unspecified
        or gateway.is_multicast
        or not (gateway.is_private or gateway.is_loopback)
    ):
        fail("gateway must be a private or loopback IPv4 address")
    return str(gateway)


def state_dir() -> Path:
    override = os.environ.get("SPARTAN_WORKSTATION_STATE_DIR")
    if override:
        path = Path(override)
    else:
        runtime = Path(f"/run/user/{os.getuid()}")
        base = runtime if runtime.is_dir() and os.access(runtime, os.W_OK) else Path("/tmp")
        suffix = "" if base == runtime else f"-{os.getuid()}"
        path = base / f"spartan-gate-workstation{suffix}"
    if not path.is_absolute():
        fail("SPARTAN_WORKSTATION_STATE_DIR must be absolute")
    return path


def ensure_state_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    validate_state_dir(path)


def validate_state_dir(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"workstation state path is not a real directory: {path}")
    if info.st_uid != os.getuid():
        fail(f"workstation state path is not owned by uid {os.getuid()}: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        fail(f"workstation state directory is accessible by group or other: {path}")


def state_dir_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        fail(f"required command not found: {name}")
    return path


def scan_host_key(config: dict[str, Any]) -> None:
    remote = config["remote"]
    result = subprocess.run(
        [
            require_binary("ssh-keyscan"),
            "-T",
            "5",
            "-t",
            "ed25519",
            "-p",
            str(remote["ssh_port"]),
            remote["host"],
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    expected = (config["_host_key_type"], config["_host_key_blob"])
    presented: set[tuple[str, str]] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3:
            presented.add((fields[1], fields[2]))
    if result.returncode != 0 or presented != {expected}:
        fail(
            "remote SSH host key mismatch; no relay was started "
            f"(expected {remote['host_key_sha256']})"
        )


def check_bind_available(host: str, port: int, label: str) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
    except OSError as exc:
        fail(f"{label} cannot bind {host}:{port}: {exc}")
    finally:
        probe.close()


def process_start_time(pid: int) -> str | None:
    try:
        remainder = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(
            ")", 1
        )[1]
        return remainder.split()[19]
    except (FileNotFoundError, IndexError, OSError):
        return None


def entry_running(entry: dict[str, Any]) -> bool:
    pid = entry.get("pid")
    start_time = entry.get("start_time")
    return (
        isinstance(pid, int)
        and isinstance(start_time, str)
        and process_start_time(pid) == start_time
    )


def read_state(path: Path) -> dict[str, Any] | None:
    state_file = path / "state.json"
    try:
        info = state_file.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            fail(f"workstation state file is not a regular file: {state_file}")
        if info.st_uid != os.getuid():
            fail(f"workstation state file is not owned by uid {os.getuid()}: {state_file}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            fail(f"workstation state file is accessible by group or other: {state_file}")
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        fail(f"invalid workstation state file {state_file}: {exc}")
    if not isinstance(payload, dict) or not isinstance(payload.get("processes"), list):
        fail(f"invalid workstation state file: {state_file}")
    return payload


def write_state(path: Path, payload: dict[str, Any]) -> None:
    target = path / "state.json"
    temporary = path / f".state.json.{os.getpid()}.tmp"
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def stop_entry(entry: dict[str, Any]) -> None:
    if not entry_running(entry):
        return
    pid = entry["pid"]
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not entry_running(entry):
            return
        time.sleep(0.05)
    if entry_running(entry):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def stop_entries(entries: list[dict[str, Any]]) -> None:
    for entry in reversed(entries):
        stop_entry(entry)


def remove_runtime_artifacts(path: Path) -> None:
    for candidate in (
        path / "state.json",
        path / "known_hosts.remote",
        path / "known_hosts.execution",
        path / "app-tunnel.control",
        path / "workstation-terminal.sock",
    ):
        candidate.unlink(missing_ok=True)


def start_process(
    state_path: Path, name: str, command: list[str]
) -> dict[str, Any]:
    log_path = state_path / f"{name}.log"
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(0.25)
    if process.poll() is not None:
        detail = ""
        try:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-500:].strip()
        except OSError:
            pass
        suffix = f": {detail}" if detail else ""
        fail(f"{name} failed to start{suffix}")
    start_time = process_start_time(process.pid)
    if start_time is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        fail(f"{name} failed to publish process state")
    return {"name": name, "pid": process.pid, "start_time": start_time}


def known_hosts_lines(config: dict[str, Any], gateway: str) -> tuple[str, str]:
    remote = config["remote"]
    key = f"{config['_host_key_type']} {config['_host_key_blob']}"
    remote_line = f"[{remote['host']}]:{remote['ssh_port']} {key}\n"
    execution_line = (
        f"[{gateway}]:{config['execution']['gateway_port']} {key}\n"
    )
    return remote_line, execution_line


def app_ssh_command(
    config: dict[str, Any], known_hosts: Path, control_socket: Path
) -> list[str]:
    remote = config["remote"]
    command = [
        require_binary("ssh"),
        "-F",
        "/dev/null",
        "-N",
        "-T",
        "-M",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-S",
        str(control_socket),
        "-i",
        str(config["tunnel"]["_identity_path"]),
        "-p",
        str(remote["ssh_port"]),
    ]
    for app in config["apps"]:
        if app["enabled"]:
            command.extend(
                [
                    "-L",
                    (
                        f"127.0.0.1:{app['local_port']}:"
                        f"{app['remote_host']}:{app['remote_port']}"
                    ),
                ]
            )
    command.append(f"{remote['user']}@{remote['host']}")
    return command


def command_up(config_path: Path, gateway_raw: str) -> None:
    config = load_config(config_path)
    gateway = validate_gateway(gateway_raw)
    path = state_dir()
    if state_dir_exists(path):
        validate_state_dir(path)
        existing = read_state(path)
    else:
        existing = None
    recovering = False
    if existing:
        running = [entry for entry in existing["processes"] if entry_running(entry)]
        matches_current = (
            existing.get("config_digest") == config["_digest"]
            and existing.get("gateway") == gateway
        )
        if len(running) == len(existing["processes"]):
            if matches_current:
                print("Workstation bridges already running.")
                return
            fail("workstation bridges are running with different configuration; stop them first")
        if running:
            if not matches_current:
                fail(
                    "workstation bridge state is partial and uses different "
                    "configuration or gateway; stop it first"
                )
            recovering = True

    require_binary("socat")
    require_binary("ssh")
    require_binary("ssh-keyscan")
    if config["terminal_broker"]["enabled"] and not TERMINAL_BROKER.is_file():
        fail(f"workstation terminal broker is missing: {TERMINAL_BROKER}")
    scan_host_key(config)

    if existing:
        if recovering:
            stop_entries(existing["processes"])
        remove_runtime_artifacts(path)

    check_bind_available(
        gateway,
        config["execution"]["gateway_port"],
        "execution relay",
    )
    enabled_apps = [app for app in config["apps"] if app["enabled"]]
    for app in enabled_apps:
        check_bind_available(
            "127.0.0.1", app["local_port"], f"{app['name']} SSH forward"
        )
        check_bind_available(
            gateway, app["gateway_port"], f"{app['name']} gateway relay"
        )

    ensure_state_dir(path)
    remote_known_hosts, execution_known_hosts = known_hosts_lines(config, gateway)
    remote_hosts_path = path / "known_hosts.remote"
    execution_hosts_path = path / "known_hosts.execution"
    remote_hosts_path.write_text(remote_known_hosts, encoding="utf-8")
    execution_hosts_path.write_text(execution_known_hosts, encoding="utf-8")
    remote_hosts_path.chmod(0o600)
    execution_hosts_path.chmod(0o600)

    processes: list[dict[str, Any]] = []
    try:
        if enabled_apps:
            processes.append(
                start_process(
                    path,
                    "app-tunnel",
                    app_ssh_command(
                        config,
                        remote_hosts_path,
                        path / "app-tunnel.control",
                    ),
                )
            )

        processes.append(
            start_process(
                path,
                "execution",
                [
                    require_binary("socat"),
                    (
                        f"TCP-LISTEN:{config['execution']['gateway_port']},"
                        f"bind={gateway},fork,reuseaddr"
                    ),
                    f"TCP:{config['remote']['host']}:{config['remote']['ssh_port']}",
                ],
            )
        )
        for app in enabled_apps:
            processes.append(
                start_process(
                    path,
                    app["name"],
                    [
                        require_binary("socat"),
                        (
                            f"TCP-LISTEN:{app['gateway_port']},"
                            f"bind={gateway},fork,reuseaddr"
                        ),
                        f"TCP:127.0.0.1:{app['local_port']}",
                    ],
                )
            )

        if config["terminal_broker"]["enabled"]:
            processes.append(
                start_process(
                    path,
                    "workstation-terminal",
                    [
                        sys.executable,
                        str(TERMINAL_BROKER),
                        "--config",
                        str(config["_path"]),
                        "--gateway",
                        gateway,
                        "--socket",
                        str(path / "workstation-terminal.sock"),
                        "--known-hosts",
                        str(execution_hosts_path),
                        "--container",
                        config["terminal_broker"]["container_name"],
                    ],
                )
            )
    except Exception:
        stop_entries(processes)
        remove_runtime_artifacts(path)
        raise

    write_state(
        path,
        {
            "version": 1,
            "config_digest": config["_digest"],
            "gateway": gateway,
            "processes": processes,
        },
    )
    names = [
        entry["name"] for entry in processes if entry["name"] != "app-tunnel"
    ]
    if recovering:
        print(f"Workstation bridges recovered from partial state: {', '.join(names)}.")
    else:
        print(f"Workstation bridges started: {', '.join(names)}.")


def command_restart_terminal(config_path: Path, gateway_raw: str) -> None:
    config = load_config(config_path)
    gateway = validate_gateway(gateway_raw)
    if not config["terminal_broker"]["enabled"]:
        fail("workstation terminal broker is disabled in configuration")
    if not TERMINAL_BROKER.is_file():
        fail(f"workstation terminal broker is missing: {TERMINAL_BROKER}")
    path = state_dir()
    if not state_dir_exists(path):
        fail("workstation bridges are not running")
    validate_state_dir(path)
    state = read_state(path)
    if not state:
        fail("workstation bridges are not running")
    if (
        state.get("config_digest") != config["_digest"]
        or state.get("gateway") != gateway
    ):
        fail("running workstation bridges use different configuration")
    terminal = [
        entry
        for entry in state["processes"]
        if entry.get("name") == "workstation-terminal"
    ]
    if len(terminal) != 1:
        fail("workstation terminal broker state is missing or ambiguous")
    if any(
        not entry_running(entry)
        for entry in state["processes"]
        if entry.get("name") != "workstation-terminal"
    ):
        fail("another workstation bridge is stopped; use `sg-workstation down` then `up`")
    stop_entry(terminal[0])
    socket_path = path / "workstation-terminal.sock"
    socket_path.unlink(missing_ok=True)
    replacement = start_process(
        path,
        "workstation-terminal",
        [
            sys.executable,
            str(TERMINAL_BROKER),
            "--config",
            str(config["_path"]),
            "--gateway",
            gateway,
            "--socket",
            str(socket_path),
            "--known-hosts",
            str(path / "known_hosts.execution"),
            "--container",
            config["terminal_broker"]["container_name"],
        ],
    )
    state["processes"] = [
        replacement if entry.get("name") == "workstation-terminal" else entry
        for entry in state["processes"]
    ]
    write_state(path, state)
    print("Workstation terminal broker restarted; other bridges were unchanged.")


def command_status() -> None:
    path = state_dir()
    if state_dir_exists(path):
        validate_state_dir(path)
        state = read_state(path)
    else:
        state = None
    if not state:
        print("Workstation bridges: not running.")
        raise SystemExit(1)
    result = 0
    for entry in state["processes"]:
        running = entry_running(entry)
        print(f"{entry['name']}: {'running' if running else 'stopped'}")
        if not running:
            result = 1
    raise SystemExit(result)


def command_down() -> None:
    path = state_dir()
    if state_dir_exists(path):
        validate_state_dir(path)
        state = read_state(path)
    else:
        state = None
    if not state:
        print("Workstation bridges: not running.")
        return
    stop_entries(state["processes"])
    remove_runtime_artifacts(path)
    print("Workstation bridges stopped.")


def command_test(config_path: Path, gateway_raw: str) -> None:
    config = load_config(config_path)
    gateway = validate_gateway(gateway_raw)
    path = state_dir()
    if state_dir_exists(path):
        validate_state_dir(path)
        state = read_state(path)
    else:
        state = None
    if not state or any(not entry_running(entry) for entry in state["processes"]):
        fail("workstation bridges are not fully running")
    known_hosts = path / "known_hosts.execution"
    command = [
        require_binary("ssh"),
        "-F",
        "/dev/null",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ConnectTimeout=10",
        "-i",
        str(config["execution"]["_identity_path"]),
        "-p",
        str(config["execution"]["gateway_port"]),
        f"{config['remote']['user']}@{gateway}",
        "printf 'workstation-ssh-ok\\n'",
    ]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != "workstation-ssh-ok":
        detail = result.stderr.strip() or result.stdout.strip() or "no response"
        fail(f"workstation SSH test failed: {detail}")
    print("Workstation SSH: healthy.")
    for app in config["apps"]:
        if app["enabled"]:
            try:
                with socket.create_connection(
                    (gateway, app["gateway_port"]), timeout=3
                ):
                    pass
            except OSError as exc:
                fail(f"{app['name']} bridge test failed: {exc}")
            print(f"{app['name']} bridge: reachable.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Operate fail-closed Hermes-to-workstation relays."
    )
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in ("validate", "up", "restart-terminal", "status", "test", "down"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        subparser.add_argument("--gateway", default="127.0.0.1")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "validate":
            load_config(args.config)
            validate_gateway(args.gateway)
            print(f"Workstation configuration valid: {args.config}")
        elif args.command == "up":
            command_up(args.config, args.gateway)
        elif args.command == "restart-terminal":
            command_restart_terminal(args.config, args.gateway)
        elif args.command == "status":
            command_status()
        elif args.command == "test":
            command_test(args.config, args.gateway)
        elif args.command == "down":
            command_down()
        return 0
    except WorkstationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
