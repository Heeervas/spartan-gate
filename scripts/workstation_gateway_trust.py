"""Fail-closed discovery of the supervised default Hermes gateway."""

from __future__ import annotations

import json
import os
import re
import select
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_GATEWAY_COMMAND = (
    "/opt/hermes/.venv/bin/python3 /opt/hermes/.venv/bin/hermes "
    "gateway run --replace"
)
DEFAULT_SUPERVISOR_COMMAND = "s6-supervise gateway-default"
DEFAULT_SVSCAN_COMMAND = (
    "/package/admin/s6/command/s6-svscan -d4 -- /run/service"
)
CONTAINER_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,128}$")
PROVENANCE_FIELDS = (
    "container_name",
    "image_id",
    "compose_project",
    "compose_service",
    "compose_oneoff",
    "compose_config_hash",
)


class GatewayTrustError(RuntimeError):
    """A gateway trust failure safe to surface without private values."""


@dataclass(frozen=True)
class ProcessRow:
    pid: int
    ppid: int
    user: str
    command: str


@dataclass(frozen=True)
class GatewaySnapshot:
    container_id: str
    provenance: dict[str, Any]
    gateway_pid: int
    gateway_uid: str
    supervisor_pid: int = 0
    svscan_pid: int = 0


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )


def parse_process_rows(output: str) -> list[ProcessRow]:
    rows: list[ProcessRow] = []
    for line in output.splitlines()[1:]:
        fields = line.strip().split(None, 3)
        if len(fields) != 4 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        rows.append(
            ProcessRow(
                pid=int(fields[0]),
                ppid=int(fields[1]),
                user=fields[2],
                command=fields[3],
            )
        )
    return rows


def parse_supervised_gateway(output: str) -> tuple[ProcessRow, ProcessRow, ProcessRow]:
    rows = parse_process_rows(output)
    by_pid = {row.pid: row for row in rows}
    gateways = [row for row in rows if row.command == DEFAULT_GATEWAY_COMMAND]
    if len(gateways) != 1:
        raise GatewayTrustError(
            "expected exactly one default Hermes gateway process; "
            f"found {len(gateways)}"
        )
    gateway = gateways[0]
    supervisor = by_pid.get(gateway.ppid)
    if (
        supervisor is None
        or supervisor.user != "root"
        or supervisor.command != DEFAULT_SUPERVISOR_COMMAND
    ):
        raise GatewayTrustError(
            "default Hermes gateway is not supervised by gateway-default"
        )
    svscan = by_pid.get(supervisor.ppid)
    if (
        svscan is None
        or svscan.user != "root"
        or svscan.command != DEFAULT_SVSCAN_COMMAND
    ):
        raise GatewayTrustError(
            "gateway-default is not supervised by the expected s6-svscan"
        )
    return gateway, supervisor, svscan


def capture_gateway_snapshot(
    container_name: str,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run,
) -> GatewaySnapshot:
    if not CONTAINER_RE.fullmatch(container_name):
        raise GatewayTrustError("invalid broker container_name")
    inspect_format = (
        '{{.Id}}\t{{.Image}}\t{{.State.Running}}\t'
        '{{index .Config.Labels "com.docker.compose.project"}}\t'
        '{{index .Config.Labels "com.docker.compose.service"}}\t'
        '{{index .Config.Labels "com.docker.compose.oneoff"}}\t'
        '{{index .Config.Labels "com.docker.compose.config-hash"}}'
    )
    inspected = runner(
        ["docker", "inspect", "--format", inspect_format, container_name]
    )
    fields = inspected.stdout.strip().split("\t")
    if inspected.returncode != 0 or len(fields) != 7 or fields[2] != "true":
        raise GatewayTrustError("configured Hermes container is not running")
    container_id, image_id, _running, project, service, oneoff, config_hash = fields
    if (
        not container_id
        or not image_id.startswith("sha256:")
        or not project
        or service != "hermes"
        or oneoff.lower() != "false"
        or not config_hash
    ):
        raise GatewayTrustError("configured Hermes container provenance is invalid")
    top = runner(
        ["docker", "top", container_id, "-eo", "pid,ppid,user,args"]
    )
    if top.returncode != 0:
        raise GatewayTrustError("cannot inspect the configured Hermes container")
    gateway, supervisor, svscan = parse_supervised_gateway(top.stdout)
    provenance = {
        "version": 1,
        "container_name": container_name,
        "image_id": image_id,
        "compose_project": project,
        "compose_service": service,
        "compose_oneoff": oneoff,
        "compose_config_hash": config_hash,
    }
    return GatewaySnapshot(
        container_id=container_id,
        provenance=provenance,
        gateway_pid=gateway.pid,
        gateway_uid=gateway.user,
        supervisor_pid=supervisor.pid,
        svscan_pid=svscan.pid,
    )


def provenance_matches(baseline: dict[str, Any], current: dict[str, Any]) -> bool:
    return baseline.get("version") == 1 and all(
        baseline.get(field) == current.get(field) for field in PROVENANCE_FIELDS
    )


def load_baseline(path: Path, container_name: str) -> dict[str, Any]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise GatewayTrustError("gateway trust baseline is not a regular file")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise GatewayTrustError("gateway trust baseline permissions are unsafe")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GatewayTrustError(
            "gateway trust baseline is missing; run `sg-workstation repin`"
        ) from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise GatewayTrustError("gateway trust baseline is invalid") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise GatewayTrustError("gateway trust baseline has an unsupported schema")
    if payload.get("container_name") != container_name:
        raise GatewayTrustError("gateway trust baseline targets another container")
    if any(not isinstance(payload.get(field), str) for field in PROVENANCE_FIELDS):
        raise GatewayTrustError("gateway trust baseline is incomplete")
    return payload


def pidfd_pid(pidfd: int) -> int:
    try:
        content = Path(f"/proc/self/fdinfo/{pidfd}").read_text(encoding="utf-8")
    except OSError as exc:
        raise GatewayTrustError("cannot inspect Hermes peer pidfd") from exc
    for line in content.splitlines():
        if line.startswith("Pid:\t"):
            value = line.split("\t", 1)[1]
            if value.isdigit():
                return int(value)
    raise GatewayTrustError("Hermes peer pidfd does not expose a process identity")


def pidfd_alive(pidfd: int) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    return not poller.poll(0)


def _proc_uid(pid: int) -> int:
    for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    raise GatewayTrustError("cannot read supervised process uid")


def _proc_ppid(pid: int) -> int:
    remainder = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1]
    return int(remainder.split()[1])


def _proc_command(pid: int) -> str:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes().rstrip(b"\0")
    return raw.replace(b"\0", b" ").decode("utf-8", errors="strict")


def validate_peer_process(
    peer_pid: int,
    peer_pidfd: int,
    snapshot: GatewaySnapshot,
) -> None:
    try:
        if pidfd_pid(peer_pidfd) != peer_pid or not pidfd_alive(peer_pidfd):
            raise GatewayTrustError("Hermes peer pidfd identity changed")
        if peer_pid != snapshot.gateway_pid or _proc_uid(peer_pid) != os.getuid():
            raise GatewayTrustError("broker peer is not the default Hermes gateway")
        if _proc_command(peer_pid) != DEFAULT_GATEWAY_COMMAND:
            raise GatewayTrustError("Hermes gateway command identity changed")
        if _proc_ppid(peer_pid) != snapshot.supervisor_pid:
            raise GatewayTrustError("Hermes gateway supervisor identity changed")
        if _proc_uid(snapshot.supervisor_pid) != 0:
            raise GatewayTrustError("Hermes gateway supervisor is not root-owned")
        if _proc_command(snapshot.supervisor_pid) != DEFAULT_SUPERVISOR_COMMAND:
            raise GatewayTrustError("Hermes gateway supervisor command changed")
        if _proc_ppid(snapshot.supervisor_pid) != snapshot.svscan_pid:
            raise GatewayTrustError("Hermes s6 parent identity changed")
        if _proc_uid(snapshot.svscan_pid) != 0:
            raise GatewayTrustError("Hermes s6 root is not root-owned")
        if _proc_command(snapshot.svscan_pid) != DEFAULT_SVSCAN_COMMAND:
            raise GatewayTrustError("Hermes s6 root command changed")
        gateway_cgroup = Path(f"/proc/{peer_pid}/cgroup").read_text(encoding="utf-8")
        supervisor_cgroup = Path(
            f"/proc/{snapshot.supervisor_pid}/cgroup"
        ).read_text(encoding="utf-8")
        if gateway_cgroup != supervisor_cgroup:
            raise GatewayTrustError("Hermes gateway and supervisor cgroups differ")
    except (FileNotFoundError, OSError, UnicodeError, ValueError) as exc:
        raise GatewayTrustError("cannot validate Hermes gateway process chain") from exc

