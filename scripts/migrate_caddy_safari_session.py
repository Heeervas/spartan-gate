#!/usr/bin/env python3
"""Prepare, validate and publish signed Caddy sessions; never activate services."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
MARKER_RE = re.compile(r"^# spartan-gate:add-port:(\d+)$", re.MULTILINE)


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    path: Path
    content: str
    mode: int


def env_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line):
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def set_env_value(text: str, key: str, value: str) -> str:
    lines = [line for line in text.splitlines() if not line.startswith(f"{key}=")]
    lines.append(f"{key}='{value}'")
    return "\n".join(lines) + "\n"


def extract_listener(source: str, address: str) -> tuple[int, int, str]:
    marker = f"{address} {{"
    start = source.find(marker)
    if start < 0:
        raise MigrationError(f"missing Caddy listener {address}")
    depth = 0
    for pos in range(start, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1, source[start : pos + 1]
    raise MigrationError(f"unclosed Caddy listener {address}")


def replace_listener(source: str, address: str, replacement: str) -> str:
    start, end, _ = extract_listener(source, address)
    return source[:start] + replacement + source[end:]


def app_listener(port: int, secure: bool) -> str:
    secure_attr = "; Secure" if secure else ""
    tls = (
        "\ttls {$SPARTAN_CADDY_TLS_CERT_FILE:/certs/cert.pem} "
        "{$SPARTAN_CADDY_TLS_KEY_FILE:/certs/key.pem}\n\n"
        if secure
        else ""
    )
    return f""":{port} {{
{tls}\troute {{
\trequest_header -X-Edge-Valid
\trequest_header -X-Edge-Cookie
\tforward_auth edge-sessions:9080 {{
\t\turi /verify/apps
\t\theader_up X-Edge-Service-Key {{$CADDY_SESSION_SERVICE_KEY}}
\t\tcopy_headers X-Edge-Valid
\t}}
\t@spartan_app_websocket {{
\t\theader Connection *Upgrade*
\t\theader Upgrade websocket
\t}}

\t@spartan_app_session_authorized {{
\t\theader X-Edge-Valid yes
\t}}

\t@spartan_app_only_edge_cookies {{
\t\theader_regexp Cookie "^[ \\t]*((spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*[; \\t]*)+$"
\t}}

\thandle @spartan_app_session_authorized {{
\t\troute {{
\t\t\theader +Set-Cookie "spartan_ws_edge=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict{secure_attr}"
\t\t\trequest_header @spartan_app_only_edge_cookies -Cookie
\t\t\trequest_header Cookie "^([ \\t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*(;[ \\t]*)?)+" ""
\t\t\trequest_header Cookie ";[ \\t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*" ""
\t\t\trequest_header -Authorization
\t\t\treverse_proxy http://hermes:{port} {{
\t\t\t\theader_up -X-Edge-Valid
\t\t\t\theader_up -X-Edge-Cookie
\t\t\t\theader_up -X-Edge-Service-Key
\t\t\t}}
\t\t}}
\t}}

\thandle @spartan_app_websocket {{
\t\trespond "Unauthorized" 401
\t}}

\thandle {{
\t\troute {{
\t\t\tbasic_auth {{
\t\t\t\t{{$CADDY_AUTH_USER:admin}} {{$CADDY_AUTH_HASH}}
\t\t\t}}
\t\t\tforward_auth edge-sessions:9080 {{
\t\t\t\turi /mint/apps
\t\t\t\theader_up X-Edge-Service-Key {{$CADDY_SESSION_SERVICE_KEY}}
\t\t\t\theader_up X-Edge-Secure {'yes' if secure else 'no'}
\t\t\t\tcopy_headers X-Edge-Cookie
\t\t\t}}
\t\t\theader {{
\t\t\t\t+Set-Cookie "{{http.request.header.X-Edge-Cookie}}"
\t\t\t\t+Set-Cookie "spartan_ws_edge=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict{secure_attr}"
\t\t\t}}
\t\t\trequest_header @spartan_app_only_edge_cookies -Cookie
\t\t\trequest_header Cookie "^([ \\t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*(;[ \\t]*)?)+" ""
\t\t\trequest_header Cookie ";[ \\t]*(spartan_ws_edge|spartan_edge_[A-Za-z0-9_]+)=[^;]*" ""
\t\t\trequest_header -Authorization
\t\t\treverse_proxy http://hermes:{port} {{
\t\t\t\theader_up -X-Edge-Valid
\t\t\t\theader_up -X-Edge-Cookie
\t\t\t\theader_up -X-Edge-Service-Key
\t\t\t}}
\t\t}}
\t}}
\t}}
}}"""


def migrate_ports(source: str, secure: bool) -> str:
    matches = list(MARKER_RE.finditer(source))
    if not matches:
        raise MigrationError("ports Caddyfile has no managed add-port listeners")
    output = [source[: matches[0].start()].rstrip()]
    seen: set[int] = set()
    for index, match in enumerate(matches):
        port = int(match.group(1))
        if port in seen:
            raise MigrationError(f"duplicate add-port marker: {port}")
        seen.add(port)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        segment = source[match.end() : end]
        _, listener_end, listener = extract_listener(segment, f":{port}")
        if segment[listener_end:].strip():
            raise MigrationError(f"unmanaged content after add-port listener {port}")
        if f"reverse_proxy http://hermes:{port}" not in listener:
            raise MigrationError(f"add-port listener {port} targets an unexpected upstream")
        if "CADDY_WS_EDGE_TOKEN" not in listener and "/verify/apps" not in listener:
            raise MigrationError(f"add-port listener {port} has no edge token contract")
        output.append(f"# spartan-gate:add-port:{port}\n{app_listener(port, secure)}")
    return "\n\n".join(part for part in output if part) + "\n"


def preflight_paths(paths: tuple[Path, ...]) -> None:
    """Reject unsafe input and backup destinations before preparing secrets."""
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise MigrationError(f"required regular file is missing or unsafe: {path}")
        backup = path.with_name(path.name + '.safari-session.bak')
        if backup.is_symlink() or (backup.exists() and not backup.is_file()):
            raise MigrationError(f"unsafe backup target: {backup}")


def build_candidates(env_path: Path, caddy_path: Path, ports_path: Path, template_path: Path) -> list[Candidate]:
    preflight_paths((env_path, caddy_path, ports_path, template_path))
    env_text = env_path.read_text(encoding="utf-8")
    values = env_values(env_text)
    websocket_token = values.get("CADDY_WS_EDGE_TOKEN", "")
    if not TOKEN_RE.fullmatch(websocket_token):
        raise MigrationError("CADDY_WS_EDGE_TOKEN must be 64 lowercase hexadecimal characters before migration")
    camofox_token = values.get("CADDY_CAMOFOX_EDGE_TOKEN", "")
    if not TOKEN_RE.fullmatch(camofox_token):
        camofox_token = secrets.token_hex(32)
    if camofox_token == websocket_token:
        camofox_token = secrets.token_hex(32)
    service_key = values.get("CADDY_SESSION_SERVICE_KEY", "")
    if not TOKEN_RE.fullmatch(service_key) or service_key in (websocket_token, camofox_token):
        service_key = secrets.token_hex(32)
    if len({websocket_token, camofox_token, service_key}) != 3:
        raise MigrationError("session keys must be independent")
    env_candidate = set_env_value(env_text, "CADDY_CAMOFOX_EDGE_TOKEN", camofox_token)
    env_candidate = set_env_value(env_candidate, "CADDY_SESSION_SERVICE_KEY", service_key)

    template_text = template_path.read_text(encoding="utf-8")
    _, _, camofox_template = extract_listener(template_text, ":26080")
    if not all(part in camofox_template for part in (
        "/verify/camofox", "/mint/camofox", "CADDY_SESSION_SERVICE_KEY", "X-Edge-Valid",
    )):
        raise MigrationError("HTTPS template does not contain the reviewed Camofox session contract")

    caddy_text = caddy_path.read_text(encoding="utf-8")
    _, _, current_camofox = extract_listener(caddy_text, ":26080")
    if "reverse_proxy http://camofox:6080" not in current_camofox:
        raise MigrationError("private Camofox listener targets an unexpected upstream")
    caddy_candidate = replace_listener(caddy_text, ":26080", camofox_template)

    secure = values.get("SPARTAN_EDGE_SCHEME", "http") == "https"
    ports_candidate = migrate_ports(ports_path.read_text(encoding="utf-8"), secure)
    return [
        Candidate(env_path, env_candidate, env_path.stat().st_mode),
        Candidate(caddy_path, caddy_candidate, caddy_path.stat().st_mode),
        Candidate(ports_path, ports_candidate, ports_path.stat().st_mode),
    ]


def run_command(command: list[str], cwd: Path, label: str) -> None:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise MigrationError(f"{label} failed: {detail}")


def install_transaction(candidates: list[Candidate], validate_command: list[str]) -> None:
    originals = {candidate.path: candidate.path.read_bytes() for candidate in candidates}
    temp_paths: dict[Path, Path] = {}
    replaced_paths: list[Path] = []
    try:
        for candidate in candidates:
            fd, raw_path = tempfile.mkstemp(prefix=f".{candidate.path.name}.", dir=candidate.path.parent)
            os.close(fd)
            temp_path = Path(raw_path)
            temp_path.write_text(candidate.content, encoding="utf-8")
            os.chmod(temp_path, candidate.mode)
            temp_paths[candidate.path] = temp_path
        # Validate only unpublished candidates. The command receives a staged
        # env, main Caddyfile and ports file; no shell or Docker is invoked.
        run_command(validate_command + [str(temp_paths[c.path]) for c in candidates], ROOT,
                    "candidate Caddy validation")
        for candidate in candidates:
            backup = candidate.path.with_name(candidate.path.name + ".safari-session.bak")
            shutil.copy2(candidate.path, backup)
        for candidate in candidates:
            os.replace(temp_paths[candidate.path], candidate.path)
            replaced_paths.append(candidate.path)
    except Exception as error:
        rollback_error = None
        for candidate in candidates:
            if candidate.path not in replaced_paths:
                continue
            try:
                candidate.path.write_bytes(originals[candidate.path])
                os.chmod(candidate.path, candidate.mode)
            except Exception as restore_error:
                rollback_error = restore_error
        if rollback_error is not None:
            raise MigrationError(f"transaction failed and rollback was incomplete: {rollback_error}") from error
        if isinstance(error, MigrationError):
            raise
        raise MigrationError(f"transaction failed before validation: {error}") from error
    finally:
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)


def validate_staged(env_path: Path, caddy_path: Path, ports_path: Path) -> None:
    binary = os.environ.get("CADDY_BIN", "caddy")
    if not shutil.which(binary):
        raise MigrationError("native Caddy required; set CADDY_BIN")
    env = {**os.environ, **env_values(env_path.read_text())}
    with tempfile.TemporaryDirectory(prefix="spartan-caddy-validate-") as scratch:
        for index, path in enumerate((caddy_path, ports_path)):
            result = subprocess.run([binary, "adapt", "--adapter", "caddyfile", "--config", str(path)],
                                    env=env, capture_output=True, text=True, check=False)
            if result.returncode:
                raise MigrationError("native Caddy adaptation failed; no files published")
            adapted = Path(scratch) / f"{index}.json"
            adapted.write_text(result.stdout)
            result = subprocess.run([binary, "validate", "--config", str(adapted)],
                                    env=env, capture_output=True, text=True, check=False)
            if result.returncode:
                raise MigrationError("native Caddy validation failed; check local TLS/import inputs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=ROOT / "private" / "env" / "local.env")
    parser.add_argument("--caddy", type=Path, default=ROOT / "private" / "caddy" / "Caddyfile.https")
    parser.add_argument("--ports", type=Path, default=ROOT / "private" / "caddy.local.d" / "ports.caddy")
    parser.add_argument("--template", type=Path, default=ROOT / "private.example" / "caddy" / "Caddyfile.https.example")
    parser.add_argument("--validate-command", help="test override; shell-style command split without a shell")
    parser.add_argument("--validate-staged", nargs=3, type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.validate_staged:
            validate_staged(*args.validate_staged)
            return 0
        lock_path = args.env.with_name(args.env.name + '.config.lock')
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            candidates = build_candidates(args.env, args.caddy, args.ports, args.template)
            validate_command = (shlex.split(args.validate_command) if args.validate_command else
                                [sys.executable, str(Path(__file__).resolve()), "--validate-staged"])
            install_transaction(candidates, validate_command)
    except (MigrationError, OSError) as error:
        print(f"[migrate-caddy-session] {error}", file=sys.stderr)
        return 1
    print("[migrate-caddy-session] Signed sessions validated and saved; no service changed. "
          "Review private Compose verifier/network/key wiring before separately authorized activation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
