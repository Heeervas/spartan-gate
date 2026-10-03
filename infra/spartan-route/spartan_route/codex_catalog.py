from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import ClientTimeout

from .codex import CODEX_AUTH_MAX_BYTES, CODEX_BASE_URL, derive_account_id, read_auth_file
from .config import ModelEntry

# This is the newest Codex wire version validated with Spartan Route's
# stateless Responses transport. A newer minimum version needs a transport test.
VERIFIED_CODEX_CLIENT_VERSION = (0, 155, 1)
# Catalog visibility depends on the requesting CLI version, independently of
# each model's minimum transport version. Newer cache hints never lift the
# validated transport ceiling above.
CATALOG_CLIENT_VERSION = (0, 159, 0)
CODEX_CATALOG_URL = f"{CODEX_BASE_URL}/models"
REFRESH_SECONDS = 3600
MAX_AGE_SECONDS = 24 * 3600
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MODELS = 256
MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
ACCOUNT_GATED_BUNDLED_MODELS = frozenset({"codex/gpt-6-sol", "codex/gpt-6-luna"})


@dataclass(frozen=True)
class DiscoveredModel:
    entry: ModelEntry
    efforts: frozenset[str]


@dataclass(frozen=True)
class SlotCatalog:
    account_key: str
    source_fingerprint: str | None
    fetched_at: float
    models: dict[str, DiscoveredModel]


def _version(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}", value):
        return None
    parts = tuple(int(part) for part in value.split("."))
    return parts if all(part <= 9999 for part in parts) else None


def cached_client_version(auth_path: str | None) -> tuple[int, int, int] | None:
    if not auth_path:
        return None
    try:
        path = Path(auth_path).with_name("models_cache.json")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESPONSE_BYTES:
                return None
            raw = stream.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return None
        payload = json.loads(raw)
        return _version(payload.get("client_version")) if isinstance(payload, dict) else None
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def parse_catalog(payload: Any) -> dict[str, DiscoveredModel]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("invalid_catalog_shape")
    rows = payload["models"]
    if len(rows) > MAX_MODELS:
        raise ValueError("catalog_too_large")
    found: dict[str, DiscoveredModel] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_model_row")
        if row.get("visibility") != "list" or row.get("supported_in_api") is not True:
            continue
        slug = row.get("slug")
        if not isinstance(slug, str) or not MODEL_ID.fullmatch(slug):
            raise ValueError("invalid_model_id")
        minimum = _version(row.get("minimal_client_version"))
        if minimum is None:
            raise ValueError("invalid_client_version")
        if minimum > VERIFIED_CODEX_CLIENT_VERSION:
            print(
                f"[spartan-route] Codex catalog skipped {slug}: minimum client "
                f"{row['minimal_client_version']} exceeds validated transport "
                f"{'.'.join(map(str, VERIFIED_CODEX_CLIENT_VERSION))}",
                file=sys.stderr,
            )
            continue
        modalities = row.get("input_modalities")
        if not isinstance(modalities, list) or not all(isinstance(item, str) for item in modalities):
            raise ValueError("invalid_modalities")
        if "text" not in modalities:
            continue
        if row.get("tool_mode") not in (None, "code_mode_only"):
            continue
        context = row.get("max_context_window")
        if type(context) is not int or not 1 <= context <= 2_000_000:
            raise ValueError("invalid_context_window")
        reasoning = row.get("supported_reasoning_levels")
        if not isinstance(reasoning, list):
            raise ValueError("invalid_reasoning_levels")
        efforts = frozenset(
            item.get("effort") for item in reasoning
            if isinstance(item, dict) and item.get("effort") in {"none", "low", "medium", "high", "xhigh", "max", "ultra"}
        )
        if not efforts:
            raise ValueError("missing_reasoning_levels")
        model_id = f"codex/{slug}"
        if model_id in found:
            raise ValueError("duplicate_model_id")
        found[model_id] = DiscoveredModel(
            ModelEntry(model_id, "codex", max_context=context, tool_capable=True, multimodal="image" in modalities),
            efforts,
        )
    return found


class CodexModelDiscovery:
    def __init__(
        self,
        router: Any,
        publish: Callable[[dict[int, SlotCatalog], dict[str, ModelEntry]], Awaitable[None]],
        bundled_ids: set[str],
    ):
        self.router = router
        self.publish = publish
        self.bundled_ids = frozenset(bundled_ids)
        self.slots: dict[int, SlotCatalog] = {}
        self.discovered_ids: frozenset[str] = frozenset()
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._refresh_lock = asyncio.Lock()
        self.request_version = CATALOG_CLIENT_VERSION

    def catalog_request_version(self) -> tuple[int, int, int]:
        version = self.request_version
        for slot in self.router.slots:
            if self.router.slot_routing_status(slot)["routingEnabled"]:
                hint = cached_client_version(slot.path)
                if hint is not None:
                    version = max(version, hint)
        if version != self.request_version:
            print(f"[spartan-route] Codex catalog request version {'.'.join(map(str, version))}", file=sys.stderr)
        # Different CLI processes may overwrite a cache with an older version.
        self.request_version = version
        return version

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="codex-model-discovery")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def schedule_refresh(self) -> None:
        self._wake.set()

    def invalidate_slot(self, index: int) -> None:
        if index in self.slots:
            self.slots = {key: value for key, value in self.slots.items() if key != index}
            self.schedule_refresh()

    def reset(self) -> None:
        self.slots = {}
        self.schedule_refresh()

    def _source(self, slot: Any) -> tuple[str | None, str | None]:
        if not slot.path:
            return None, None
        try:
            if Path(slot.path).stat().st_size > CODEX_AUTH_MAX_BYTES:
                return "", None
        except OSError:
            return "", None
        snapshot = read_auth_file(slot.path)
        if snapshot is None:
            return "", None
        tokens = snapshot[0].get("tokens", snapshot[0])
        account_id = derive_account_id(tokens) if isinstance(tokens, dict) else None
        return snapshot[1], account_id

    def _current(self, slot: Any, auth: Any) -> SlotCatalog | None:
        catalog = self.slots.get(slot.index)
        source_fingerprint, source_account = self._source(slot)
        if (
            catalog is None
            or catalog.account_key != auth.account_key
            or (slot.path and source_account != auth.account_id)
            or catalog.source_fingerprint != source_fingerprint
            or time.monotonic() - catalog.fetched_at > MAX_AGE_SECONDS
        ):
            if catalog is not None and (
                catalog.account_key != auth.account_key
                or (slot.path and (catalog.source_fingerprint != source_fingerprint or source_account != auth.account_id))
            ):
                self.schedule_refresh()
            return None
        return catalog

    def supports(self, slot: Any, auth: Any, model_id: str, effort: str | None = None) -> bool:
        gated = model_id in self.discovered_ids or model_id in ACCOUNT_GATED_BUNDLED_MODELS
        if not gated:
            return True
        catalog = self._current(slot, auth)
        if catalog is None:
            return False
        model = catalog.models.get(model_id)
        return model is not None and (effort is None or effort in model.efforts)

    def known_unavailable(self, model_id: str, effort: str | None = None) -> bool:
        if model_id not in self.discovered_ids and model_id not in ACCOUNT_GATED_BUNDLED_MODELS:
            return False
        for slot in self.router.slots:
            auth = slot.auth
            if auth and self.router.slot_routing_status(slot)["routingEnabled"] and self.supports(slot, auth, model_id, effort):
                return False
        return True

    def supports_effort(self, model_id: str, effort: str) -> bool:
        return any(model_id in catalog.models and effort in catalog.models[model_id].efforts for catalog in self.slots.values())

    async def _run(self) -> None:
        while True:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[spartan-route] Codex model discovery failed ({type(exc).__name__})", file=sys.stderr)
            try:
                await asyncio.wait_for(self._wake.wait(), REFRESH_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _fetch(
        self, auth: Any, *, client_version: tuple[int, int, int] = CATALOG_CLIENT_VERSION,
    ) -> dict[str, DiscoveredModel]:
        headers = {
            "Authorization": f"Bearer {auth.access_token}",
            "chatgpt-account-id": auth.account_id,
        }
        async with self.router.session.get(
            CODEX_CATALOG_URL,
            params={"client_version": ".".join(map(str, client_version))},
            headers=headers,
            timeout=ClientTimeout(total=8),
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ValueError(f"catalog_http_{response.status}")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await response.content.read(min(8192, MAX_RESPONSE_BYTES + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ValueError("catalog_response_too_large")
                chunks.append(chunk)
            return parse_catalog(json.loads(b"".join(chunks)))

    async def refresh(self) -> None:
        async with self._refresh_lock:
            client_version = self.catalog_request_version()
            now = time.monotonic()
            refreshed: dict[int, SlotCatalog] = {}
            for slot in self.router.slots:
                if not self.router.slot_routing_status(slot)["routingEnabled"]:
                    continue
                try:
                    auth = await self.router.load_auth(slot)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    auth = None
                if auth is None or not auth.account_id or not auth.account_key:
                    continue
                source_fingerprint, source_account = self._source(slot)
                if slot.path and source_account != auth.account_id:
                    continue
                try:
                    try:
                        models = await self._fetch(auth, client_version=client_version)
                    except Exception as exc:
                        if client_version == CATALOG_CLIENT_VERSION:
                            raise
                        print(
                            f"[spartan-route] Codex model catalog slot {slot.index} "
                            f"retrying baseline version ({type(exc).__name__})", file=sys.stderr,
                        )
                        models = await self._fetch(auth, client_version=CATALOG_CLIENT_VERSION)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    old = self._current(slot, auth)
                    if old is not None:
                        refreshed[slot.index] = old
                    print(f"[spartan-route] Codex model catalog slot {slot.index} refresh failed ({type(exc).__name__})", file=sys.stderr)
                else:
                    refreshed[slot.index] = SlotCatalog(auth.account_key, source_fingerprint, now, models)
            merged: dict[str, ModelEntry] = {}
            for catalog in refreshed.values():
                for model_id, model in catalog.models.items():
                    previous = merged.get(model_id)
                    if previous is None:
                        merged[model_id] = model.entry
                    else:
                        merged[model_id] = ModelEntry(
                            model_id, "codex", max_context=min(previous.max_context, model.entry.max_context),
                            tool_capable=previous.tool_capable and model.entry.tool_capable,
                            multimodal=previous.multimodal and model.entry.multimodal,
                        )
            await self.publish(refreshed, merged)

    def install_snapshot(self, slots: dict[int, SlotCatalog], models: dict[str, ModelEntry]) -> None:
        self.slots = slots
        self.discovered_ids = frozenset(models) - self.bundled_ids
