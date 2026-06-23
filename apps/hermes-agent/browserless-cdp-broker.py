#!/usr/bin/env python3
"""Multiplex local CDP clients onto one Browserless persistent-profile session.

Browserless launch WebSocket endpoints start a browser for each connection.
Hermes uses more than one CDP client per task (its supervisor plus the browser
driver and optional MCP clients), so pointing all clients at a launch endpoint
causes concurrent --user-data-dir starts. This broker owns the single upstream
Browserless connection and fans local CDP clients into it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Request, Response
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.server import ServerConnection


LOG = logging.getLogger("browserless-cdp-broker")
HOST = os.environ.get("BROWSERLESS_CDP_BROKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("BROWSERLESS_CDP_BROKER_PORT", "9229"))
DISCOVERY_WS_URL = os.environ.get("BROWSER_CDP_URL", "").strip() or (
    f"ws://127.0.0.1:{PORT}"
)
UPSTREAM_URL = os.environ.get("BROWSER_CDP_LAUNCH_URL", "").strip()
READY_FILE = os.environ.get("BROWSERLESS_CDP_BROKER_READY_FILE", "").strip()
PROFILE_ROOT = os.environ.get("BROWSERLESS_PROFILE_ROOT", "/profiles").rstrip("/")
PROFILE_NAME = os.environ.get("BROWSERLESS_PROFILE", "main").strip() or "main"
BROWSERLESS_HTTP_BASE = os.environ.get("BROWSERLESS_HTTP_BASE", "http://browserless:3000").rstrip("/")
BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN", "").strip()
TRACE = os.environ.get("BROWSERLESS_CDP_BROKER_TRACE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LOCK_FILE_NAMES = ("SingletonLock", "SingletonSocket", "SingletonCookie")
LOCAL_NAVIGATION_HOSTS = {"localhost", "127.0.0.1", "::1"}
LOCAL_NAVIGATION_HELP = (
    "Browserless/CDP runs in a separate container. Do not navigate to file:// "
    "or localhost URLs. For local HTML/assets, run "
    "`python3 -m http.server <port> --bind 0.0.0.0` in Hermes and open "
    "`http://hermes:<port>/...`."
)


def browserless_url(path: str) -> str:
    url = f"{BROWSERLESS_HTTP_BASE}{path}"
    if BROWSERLESS_TOKEN:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}token={BROWSERLESS_TOKEN}"
    return url


def fetch_json(path: str, timeout: float = 5.0) -> Any:
    request = urllib.request.Request(
        browserless_url(path),
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(1024 * 1024)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def browserless_is_idle() -> bool:
    sessions = fetch_json("/sessions")
    if not isinstance(sessions, list) or sessions:
        LOG.info("Profile lock cleanup skipped: Browserless sessions are active")
        return False

    pressure = fetch_json("/pressure")
    running = pressure.get("pressure", {}).get("running") if isinstance(pressure, dict) else None
    if running != 0:
        LOG.info("Profile lock cleanup skipped: Browserless running count is %r", running)
        return False

    return True


def remove_stale_profile_locks(profile_path: Path, reason: str) -> bool:
    if not profile_path.exists():
        LOG.info("Profile lock cleanup skipped: %s does not exist", profile_path)
        return False

    lock_paths = [profile_path / name for name in LOCK_FILE_NAMES]
    existing = [path for path in lock_paths if path.exists() or path.is_symlink()]
    if not existing:
        return False

    try:
        if not browserless_is_idle():
            return False
    except Exception as exc:
        LOG.warning("Profile lock cleanup skipped: cannot confirm Browserless is idle: %s", exc)
        return False

    removed = False
    for path in existing:
        try:
            target = path.readlink() if path.is_symlink() else None
            path.unlink()
            LOG.warning(
                "Removed stale Chromium profile lock %s%s (%s)",
                path,
                f" -> {target}" if target is not None else "",
                reason,
            )
            removed = True
        except FileNotFoundError:
            continue
        except Exception as exc:
            LOG.warning("Failed to remove stale Chromium profile lock %s: %s", path, exc)
    return removed


def blocked_navigation_reason(message: dict[str, Any]) -> str | None:
    method = message.get("method")
    if method not in {"Page.navigate", "Target.createTarget"}:
        return None

    params = message.get("params")
    if not isinstance(params, dict):
        return None

    raw_url = params.get("url")
    if not isinstance(raw_url, str):
        return None

    parsed = urlparse(raw_url)
    if parsed.scheme.lower() == "file":
        return f"Refusing Browserless navigation to local file URL: {raw_url}. {LOCAL_NAVIGATION_HELP}"

    if parsed.scheme.lower() in {"http", "https"}:
        host = (parsed.hostname or "").strip("[]").lower()
        if host in LOCAL_NAVIGATION_HOSTS:
            return f"Refusing Browserless navigation to Hermes-local URL: {raw_url}. {LOCAL_NAVIGATION_HELP}"

    return None


def cdp_error(original_id: Any, message: str, session_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "code": -32000,
            "message": message,
        }
    }
    if original_id is not None:
        payload["id"] = original_id
    if session_id:
        payload["sessionId"] = session_id
    return payload


class CDPBroker:
    def __init__(self, upstream_url: str) -> None:
        self.upstream_url = upstream_url
        self.upstream: ClientConnection | None = None
        self.upstream_reader: asyncio.Task[None] | None = None
        self.clients: set[ServerConnection] = set()
        self.pending: dict[int, tuple[ServerConnection, Any, str]] = {}
        self.internal_pending: dict[int, str] = {}
        self.session_owners: dict[str, ServerConnection] = {}
        self.targets: dict[str, dict[str, Any]] = {}
        self.next_id = 1
        self.lock = asyncio.Lock()

    async def ensure_upstream(self) -> ClientConnection:
        async with self.lock:
            if self.upstream is not None:
                return self.upstream

            await self.cleanup_profile_locks("before opening upstream")
            try:
                upstream = await self.open_upstream()
            except Exception as exc:
                if await self.cleanup_profile_locks(f"after upstream open failure: {exc}"):
                    LOG.info("Retrying Browserless persistent-profile session after lock cleanup")
                    upstream = await self.open_upstream()
                else:
                    raise
            self.upstream = upstream
            self.upstream_reader = asyncio.create_task(self.read_upstream(upstream))
            await self.initialize_target_discovery(upstream)
            LOG.info("Browserless persistent-profile session is ready")
            return upstream

    async def cleanup_profile_locks(self, reason: str) -> bool:
        profile_path = Path(PROFILE_ROOT) / PROFILE_NAME
        return await asyncio.to_thread(remove_stale_profile_locks, profile_path, reason)

    async def open_upstream(self) -> ClientConnection:
        LOG.info("Opening Browserless persistent-profile session")
        return await websockets.connect(
            self.upstream_url,
            proxy=None,
            compression=None,
            max_size=50 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        )

    async def initialize_target_discovery(self, upstream: ClientConnection) -> None:
        await self.send_internal_command(
            upstream,
            "Target.setDiscoverTargets",
            {"discover": True},
        )
        await self.send_internal_command(upstream, "Target.getTargets")

    async def send_internal_command(
        self,
        upstream: ClientConnection,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        broker_id = self.next_id
        self.next_id += 1
        self.internal_pending[broker_id] = method
        message: dict[str, Any] = {"id": broker_id, "method": method}
        if params is not None:
            message["params"] = params
        await upstream.send(json.dumps(message, separators=(",", ":")))

    async def connect_client(self, client: ServerConnection) -> None:
        try:
            await self.ensure_upstream()
        except Exception as exc:
            LOG.error("Cannot open Browserless session: %s", exc)
            await client.close(code=1011, reason="Browserless upstream unavailable")
            return

        self.clients.add(client)
        LOG.info("CDP client attached; clients=%s", len(self.clients))
        try:
            async for payload in client:
                await self.forward_from_client(client, payload)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(client)
            self.pending = {
                broker_id: owner
                for broker_id, owner in self.pending.items()
                if owner[0] is not client
            }
            self.session_owners = {
                session_id: owner
                for session_id, owner in self.session_owners.items()
                if owner is not client
            }
            LOG.info("CDP client detached; clients=%s", len(self.clients))

    async def forward_from_client(self, client: ServerConnection, payload: str | bytes) -> None:
        if isinstance(payload, bytes):
            upstream = await self.ensure_upstream()
            await upstream.send(payload)
            return

        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            upstream = await self.ensure_upstream()
            await upstream.send(payload)
            return

        if not isinstance(message, dict):
            upstream = await self.ensure_upstream()
            await upstream.send(payload)
            return

        original_id = message.get("id")
        method = str(message.get("method", ""))
        if TRACE and method:
            LOG.info("CDP client -> upstream: %s", method)
        if method == "Browser.close" and original_id is not None:
            await self.send_json(client, {"id": original_id, "result": {}})
            return

        navigation_error = blocked_navigation_reason(message)
        if navigation_error:
            LOG.warning(navigation_error)
            if original_id is not None:
                session_id = message.get("sessionId")
                await self.send_json(
                    client,
                    cdp_error(
                        original_id,
                        navigation_error,
                        session_id if isinstance(session_id, str) else None,
                    ),
                )
            return

        upstream = await self.ensure_upstream()

        session_id = message.get("sessionId")
        if isinstance(session_id, str):
            self.session_owners.setdefault(session_id, client)

        if original_id is not None:
            broker_id = self.next_id
            self.next_id += 1
            self.pending[broker_id] = (client, original_id, method)
            message["id"] = broker_id
            payload = json.dumps(message, separators=(",", ":"))

        await upstream.send(payload)

    async def read_upstream(self, upstream: ClientConnection) -> None:
        try:
            async for payload in upstream:
                await self.forward_from_upstream(payload)
        except websockets.exceptions.ConnectionClosed as exc:
            LOG.warning("Browserless upstream disconnected: %s", exc)
        except Exception:
            LOG.exception("Browserless upstream reader failed")
        finally:
            if self.pending:
                pending_methods: dict[str, int] = {}
                for _, _, method in self.pending.values():
                    pending_methods[method] = pending_methods.get(method, 0) + 1
                LOG.warning(
                    "Dropping %s pending CDP commands after upstream disconnect: %s",
                    len(self.pending),
                    pending_methods,
                )
            async with self.lock:
                if self.upstream is upstream:
                    self.upstream = None
                    self.upstream_reader = None
                    self.pending.clear()
                    self.internal_pending.clear()
                    self.session_owners.clear()
                    self.targets.clear()
            clients = list(self.clients)
            self.clients.clear()
            for client in clients:
                await client.close(code=1011, reason="Browserless upstream disconnected")

    async def forward_from_upstream(self, payload: str | bytes) -> None:
        if isinstance(payload, bytes):
            await self.broadcast(payload)
            return

        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            await self.broadcast(payload)
            return

        if not isinstance(message, dict):
            await self.broadcast(payload)
            return

        self.update_targets_from_event(message)

        broker_id = message.get("id")
        if isinstance(broker_id, int) and broker_id in self.internal_pending:
            method = self.internal_pending.pop(broker_id)
            self.update_targets_from_internal_response(method, message)
            return

        if isinstance(broker_id, int) and broker_id in self.pending:
            client, original_id, method = self.pending.pop(broker_id)
            message["id"] = original_id
            if method == "Target.getTargets":
                self.update_targets_from_internal_response(method, message)
            if method in {"Target.attachToTarget", "Target.attachToBrowserTarget"}:
                result = message.get("result")
                if isinstance(result, dict) and isinstance(result.get("sessionId"), str):
                    self.session_owners[result["sessionId"]] = client
            await self.send_json(client, message)
            return

        session_id = message.get("sessionId")
        if isinstance(session_id, str) and session_id in self.session_owners:
            await self.send_json(self.session_owners[session_id], message)
            return

        await self.broadcast(json.dumps(message, separators=(",", ":")))

    def update_targets_from_internal_response(
        self,
        method: str,
        message: dict[str, Any],
    ) -> None:
        if method != "Target.getTargets":
            return
        result = message.get("result")
        target_infos = result.get("targetInfos") if isinstance(result, dict) else None
        if not isinstance(target_infos, list):
            return
        for target_info in target_infos:
            if isinstance(target_info, dict):
                self.upsert_target(target_info)

    def update_targets_from_event(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        if method in {"Target.targetCreated", "Target.targetInfoChanged"}:
            target_info = params.get("targetInfo")
            if isinstance(target_info, dict):
                self.upsert_target(target_info)
        elif method == "Target.targetDestroyed":
            target_id = params.get("targetId")
            if isinstance(target_id, str):
                self.targets.pop(target_id, None)

    def upsert_target(self, target_info: dict[str, Any]) -> None:
        target_id = target_info.get("targetId")
        if not isinstance(target_id, str) or not target_id:
            return
        self.targets[target_id] = dict(target_info)

    def discovery_targets(self) -> list[dict[str, Any]]:
        page_targets = [
            target
            for target in self.targets.values()
            if target.get("type") == "page"
        ]
        non_blank = [
            target
            for target in page_targets
            if target.get("url") and target.get("url") != "about:blank"
        ]
        targets = non_blank or page_targets
        return [self.discovery_target(target) for target in targets]

    def discovery_target(self, target: dict[str, Any]) -> dict[str, Any]:
        target_id = str(target.get("targetId") or "")
        title = str(target.get("title") or "")
        url = str(target.get("url") or "")
        return {
            "description": "",
            "devtoolsFrontendUrl": f"/devtools/inspector.html?ws={DISCOVERY_WS_URL}",
            "id": target_id,
            "title": title,
            "type": str(target.get("type") or "page"),
            "url": url,
            "webSocketDebuggerUrl": DISCOVERY_WS_URL,
        }

    def discovery_payload(self, path: str) -> Any | None:
        path = path.split("?", 1)[0].rstrip("/")
        if path == "/json/version":
            return {
                "Browser": "Spartan Browserless CDP Broker",
                "Protocol-Version": "1.3",
                "webSocketDebuggerUrl": DISCOVERY_WS_URL,
            }
        if path in {"/json", "/json/list"}:
            return self.discovery_targets()
        return None

    def discovery_response(
        self,
        _connection: ServerConnection,
        request: Request,
    ) -> Response | None:
        payload = self.discovery_payload(request.path)
        if payload is None:
            return None
        return json_response(payload)

    async def broadcast(self, payload: str | bytes) -> None:
        for client in list(self.clients):
            try:
                await client.send(payload)
            except websockets.exceptions.ConnectionClosed:
                self.clients.discard(client)

    @staticmethod
    async def send_json(client: ServerConnection, message: dict[str, Any]) -> None:
        try:
            await client.send(json.dumps(message, separators=(",", ":")))
        except websockets.exceptions.ConnectionClosed:
            pass


def json_response(payload: Any) -> Response:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return Response(
        200,
        "OK",
        Headers(
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ]
        ),
        body,
    )


async def main() -> None:
    if not UPSTREAM_URL:
        raise SystemExit("BROWSER_CDP_LAUNCH_URL is required")

    ready_file = Path(READY_FILE) if READY_FILE else None
    if ready_file is not None:
        ready_file.unlink(missing_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("websockets.server").setLevel(logging.WARNING)
    broker = CDPBroker(UPSTREAM_URL)
    stop = asyncio.get_running_loop().create_future()

    def request_stop() -> None:
        if not stop.done():
            stop.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, request_stop)

    async with websockets.serve(
        broker.connect_client,
        HOST,
        PORT,
        compression=None,
        max_size=50 * 1024 * 1024,
        process_request=broker.discovery_response,
    ):
        if ready_file is not None:
            ready_file.touch()
        LOG.info("CDP broker listening on ws://%s:%s", HOST, PORT)
        await stop

    if ready_file is not None:
        ready_file.unlink(missing_ok=True)
    upstream = broker.upstream
    upstream_reader = broker.upstream_reader
    if upstream is not None:
        await upstream.close()
    if upstream_reader is not None:
        await upstream_reader


if __name__ == "__main__":
    asyncio.run(main())
