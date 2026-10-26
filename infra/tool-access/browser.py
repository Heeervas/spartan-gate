"""Narrow managed-browser authority; no backend discovery, adoption or replay."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import re
import secrets
import struct
from urllib.parse import urlsplit
import zlib

from aiohttp import web

from policy import AccessError, authenticate, strict_json

JSON_LIMIT = 128 * 1024
PNG_LIMIT = 2 * 1024 * 1024
TOKEN = re.compile(r"[0-9a-f]{64}\Z")
TAB_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
FIELDS = {
    "create": {"url"}, "tabs": set(), "snapshot": {"tabId"},
    "navigate": {"tabId", "url"}, "click": {"tabId", "ref", "selector"},
    "type": {"tabId", "ref", "selector", "text"},
    "scroll": {"tabId", "direction", "amount"},
    "screenshot": {"tabId"}, "close": {"tabId"}, "cleanup": set(),
}


@dataclass
class Tab:
    retired: bool = False


@dataclass
class Workflow:
    principal: str
    fingerprint: str
    user: str
    group: str
    generation: int
    tabs: dict = field(default_factory=dict)
    retired: bool = False
    busy: bool = False


def body_arguments(request, action):
    if request.query:
        raise AccessError("invalid_request")
    try:
        raw = request._read_bytes
        data = strict_json(raw) if raw else {}
    except (ValueError, UnicodeError, RecursionError):
        raise AccessError("invalid_request") from None
    if not isinstance(data, dict) or set(data) - FIELDS[action]:
        raise AccessError("invalid_request")
    return data


def text_argument(data, name, *, required=True, limit=8192):
    value = data.get(name)
    if name not in data and not required:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise AccessError("invalid_request")
    return value


def navigation_url(value):
    try:
        parsed = urlsplit(value)
        if (not value or parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port == 0 or "\\" in value
                or any(ord(char) <= 32 for char in value)):
            raise ValueError("invalid URL")
    except (ValueError, TypeError):
        raise AccessError("invalid_request") from None
    return value


def operation_arguments(data, action):
    outgoing = {}
    if action in {"create", "navigate"}:
        url = text_argument(data, "url", required=action == "navigate")
        if url is not None:
            outgoing["url"] = navigation_url(url)
    if action in {"click", "type"}:
        targets = [key for key in ("ref", "selector") if key in data]
        if len(targets) != 1:
            raise AccessError("invalid_request")
        target = targets[0]
        value = text_argument(data, target, limit=2048)
        if not value:
            raise AccessError("invalid_request")
        outgoing[target] = value
    if action == "click":
        outgoing["singleAttempt"] = True
    if action == "type":
        outgoing.update(text=text_argument(data, "text"), mode="fill",
                        submit=False, pressEnter=False)
    if action == "scroll":
        direction = data.get("direction", "down")
        amount = data.get("amount", 500)
        if (direction not in ("up", "down", "left", "right")
                or type(amount) is not int or not 1 <= amount <= 10000):
            raise AccessError("invalid_request")
        outgoing.update(direction=direction, amount=amount)
    return outgoing


def decode_json(response):
    try:
        if len(response.body) > JSON_LIMIT:
            raise ValueError("too large")
        data = strict_json(response.body)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data
    except (ValueError, UnicodeError, RecursionError):
        raise AccessError("upstream_error") from None


def png_response(response):
    data = response.body
    if (len(data) > PNG_LIMIT or len(data) < 33
            or data[:8] != b"\x89PNG\r\n\x1a\n"
            or data[8:16] != b"\x00\x00\x00\rIHDR"):
        raise AccessError("upstream_error")
    width, height = struct.unpack(">II", data[16:24])
    crc = struct.unpack(">I", data[29:33])[0]
    if (not 1 <= width <= 4096 or not 1 <= height <= 4096
            or width * height > 16000000 or zlib.crc32(data[12:29]) != crc):
        raise AccessError("upstream_error")
    return web.Response(body=data, content_type="image/png")


def snapshot_response(data):
    required = {"url": str, "snapshot": str, "refsCount": int, "truncated": bool}
    if any(type(data.get(key)) is not kind for key, kind in required.items()):
        raise AccessError("upstream_error")
    # Native windowSnapshot omits these fields only for untruncated snapshots.
    pagination = {"hasMore": False, "nextOffset": None} if data["truncated"] is False else {}
    pagination.update({key: data[key] for key in ("hasMore", "nextOffset") if key in data})
    more = pagination.get("hasMore")
    offset = pagination.get("nextOffset")
    if data["refsCount"] < 0 or type(more) is not bool or "nextOffset" not in pagination:
        raise AccessError("upstream_error")
    if more:
        if data["truncated"] is not True or type(offset) is not int or offset <= 0:
            raise AccessError("upstream_error")
    elif offset is not None:
        raise AccessError("upstream_error")
    return {**{key: data[key] for key in required}, **pagination}


class Browser:
    def __init__(self, app):
        self.app = app
        self.workflows = {}
        settings = app["settings"]
        self.key = settings.get("CAMOFOX_ACCESS_KEY", "")
        self.configured = bool(settings.get("CAMOFOX_URL") and self.key)

    async def upstream(self, method, path, *, params=None, body=None, image=False):
        return await self.app["transport"].request(
            "browser", method, path, limit=PNG_LIMIT if image else JSON_LIMIT,
            params=params, json_body=body,
            headers={"Authorization": "Bearer " + self.key})

    async def generation(self):
        health = await self.upstream("GET", "/health")
        capabilities = await self.upstream("GET", "/spartan/capabilities")
        if health.status != 200 or capabilities.status != 200:
            raise AccessError("unavailable")
        status, caps = decode_json(health), decode_json(capabilities)
        generation = status.get("browserGeneration")
        if (status.get("ok") is not True or type(generation) is not int
                or not 1 <= generation <= 9007199254740991):
            raise AccessError("unavailable")
        if (caps.get("nonDestructiveTabCreate") is not True
                or caps.get("singleAttemptBrowserClick") is not True):
            raise AccessError("unsupported_backend")
        return generation

    def capacity(self, principal):
        count = sum(item.principal == principal.principal for item in self.workflows.values())
        if len(self.workflows) >= 64 or count >= 8:
            raise AccessError("capacity")

    async def mint(self, request):
        principal = authenticate(request, "browser")
        body_arguments(request, "cleanup")
        if not self.configured:
            raise AccessError("unavailable")
        async with self.app["transport"].admit(principal):
            principal = authenticate(request, "browser")
            self.capacity(principal)
            # Reserve before the first await: concurrent minting cannot oversubscribe.
            token = secrets.token_hex(32)
            workflow = Workflow(principal.principal, principal.fingerprint,
                                principal.browser_user, secrets.token_hex(32), 0, busy=True)
            self.workflows[token] = workflow
            try:
                workflow.generation = await self.generation()
            except BaseException:
                del self.workflows[token]
                raise
            workflow.busy = False
            return web.json_response({"workflowToken": token})

    def lookup(self, request, principal):
        headers = request.headers.getall("X-Spartan-Workflow", [])
        token = headers[0] if len(headers) == 1 else ""
        workflow = self.workflows.get(token) if TOKEN.fullmatch(token) else None
        if (workflow is None or workflow.principal != principal.principal
                or workflow.fingerprint != principal.fingerprint):
            raise AccessError("stale_workflow")
        return token, workflow

    @asynccontextmanager
    async def admission(self, request):
        principal = authenticate(request, "browser")
        token, workflow = self.lookup(request, principal)
        if workflow.busy:
            raise AccessError("workflow_busy")
        workflow.busy = True
        try:
            async with self.app["transport"].admit(principal):
                self.lookup(request, authenticate(request, "browser"))
                yield token, workflow
        finally:
            workflow.busy = False

    def owned_tab(self, workflow, data, *, cleanup=False):
        tab_id = text_argument(data, "tabId", limit=128)
        tab = workflow.tabs.get(tab_id)
        if tab is None:
            raise AccessError("forbidden")
        if not cleanup and (workflow.retired or tab.retired):
            raise AccessError("stale_tab")
        return tab_id, tab

    async def check_generation(self, workflow):
        if await self.generation() != workflow.generation:
            workflow.retired = True
            raise AccessError("stale_workflow")

    async def operation(self, request):
        # Leave time to return an explicit unknown outcome before outer ingress expiry.
        request["browser_mutation_started"] = False
        try:
            async with asyncio.timeout(29):
                return await self.perform(request)
        except TimeoutError:
            code = "remote_outcome_unknown" if request["browser_mutation_started"] else "deadline"
            raise AccessError(code) from None

    async def perform(self, request):
        action = request.match_info.get("action", "tabs")
        # Explicit registered routes determine action, never a backend path from input.
        data = body_arguments(request, action)
        outgoing = operation_arguments(data, action)
        async with self.admission(request) as (_, workflow):
            if action == "close":
                tab_id, tab = self.owned_tab(workflow, data, cleanup=True)
                request["browser_mutation_started"] = True
                await self.close_tab(workflow, tab_id, tab)
                return web.json_response({"ok": True})
            if workflow.retired:
                raise AccessError("stale_workflow")
            tab_id, tab = None, None
            if action not in {"create", "tabs"}:
                tab_id, tab = self.owned_tab(workflow, data)
            if action == "create" and len(workflow.tabs) >= 4:
                raise AccessError("capacity")
            await self.check_generation(workflow)
            if action == "tabs":
                await self.check_generation(workflow)
                return web.json_response({"tabs": [{"tabId": key} for key, value
                                                   in workflow.tabs.items() if not value.retired]})
            request["browser_mutation_started"] = action in {"create", "navigate", "click", "type", "scroll"}
            return await self.execute(workflow, action, outgoing, tab_id, tab)

    async def execute(self, workflow, action, outgoing, tab_id, tab):
        mutating = action in {"create", "navigate", "click", "type", "scroll"}
        try:
            response = await self.dispatch(workflow, action, outgoing, tab_id)
            if response.status == 200:
                result = self.result(workflow, action, response)
                await self.check_generation(workflow)
                return result
            if response.status in {404, 410}:
                if tab:
                    tab.retired = True
                raise AccessError("stale_tab")
            if response.status == 429:
                raise AccessError("capacity")
            raise AccessError("upstream_error")
        except BaseException as error:
            if mutating:
                if isinstance(error, AccessError) and error.code == "capacity":
                    raise
                if tab:
                    tab.retired = True
                else:
                    workflow.retired = True
                if isinstance(error, AccessError) and error.code in {
                    "stale_tab", "stale_workflow"
                }:
                    raise
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise AccessError("remote_outcome_unknown") from None
            raise

    async def dispatch(self, workflow, action, outgoing, tab_id):
        outgoing = {**outgoing, "userId": workflow.user}
        if action == "create":
            outgoing.update(sessionKey=workflow.group, nonDestructive=True)
            return await self.upstream("POST", "/tabs", body=outgoing)
        path = "/tabs/" + tab_id + "/" + action
        if action in {"snapshot", "screenshot"}:
            params = {"userId": workflow.user}
            params.update({"fullPage": "false"} if action == "screenshot" else
                          {"offset": "0", "includeScreenshot": "false"})
            return await self.upstream("GET", path, params=params, image=action == "screenshot")
        return await self.upstream("POST", path, body=outgoing)

    def result(self, workflow, action, response):
        if action == "screenshot":
            return png_response(response)
        data = decode_json(response)
        if action == "create":
            tab_id = data.get("tabId")
            if (not isinstance(tab_id, str) or not TAB_ID.fullmatch(tab_id)
                    or any(tab_id in item.tabs for item in self.workflows.values())):
                raise AccessError("upstream_error")
            # Retain exact known IDs even if the postflight probe fails: cleanup only.
            workflow.tabs[tab_id] = Tab()
            return web.json_response({"tabId": tab_id})
        if action == "snapshot":
            return web.json_response(snapshot_response(data))
        if data.get("ok") is not True:
            raise AccessError("upstream_error")
        output = {"ok": True}
        if action in {"navigate", "click"} and isinstance(data.get("url"), str):
            output["url"] = data["url"]
        return web.json_response(output)

    async def close_tab(self, workflow, tab_id, tab):
        try:
            response = await self.upstream("DELETE", "/tabs/" + tab_id,
                                           params={"userId": workflow.user})
            if response.status not in {404, 410}:
                if response.status != 200 or decode_json(response).get("ok") is not True:
                    raise AccessError("upstream_error")
            del workflow.tabs[tab_id]
        except BaseException as error:
            tab.retired = True
            if isinstance(error, asyncio.CancelledError):
                raise
            raise AccessError("remote_outcome_unknown") from None

    async def cleanup(self, request):
        body_arguments(request, "cleanup")
        try:
            async with asyncio.timeout(29), self.admission(request) as (token, workflow):
                for tab_id, tab in list(workflow.tabs.items()):
                    request["browser_mutation_started"] = True
                    await self.close_tab(workflow, tab_id, tab)
                del self.workflows[token]
                return web.json_response({"ok": True})
        except TimeoutError:
            raise AccessError("remote_outcome_unknown") from None


def register_routes(app):
    browser = Browser(app)
    app.router.add_post("/v1/workflows", browser.mint)
    app.router.add_delete("/v1/workflows", browser.cleanup)
    app.router.add_get("/v1/browser/tabs", browser.operation, allow_head=False)
    for action in FIELDS.keys() - {"tabs", "cleanup"}:
        app.router.add_post("/v1/browser/{action:" + action + "}", browser.operation)
