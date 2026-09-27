from __future__ import annotations

import asyncio
import base64
import json
import multiprocessing
import os
import stat
import tempfile
import struct
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spartan_route.codex import (
    CodexAuth,
    CodexAuthStoreError,
    CodexCallResult,
    CodexEventState,
    CodexRouter,
    CodexSlot,
    build_codex_request_body,
    build_codex_responses_body,
    build_content_prompt_cache_key,
    chat_messages_to_responses_input,
    derive_account_id,
    normalize_tool_choice,
    normalize_reset_credits,
    read_auth_file,
    resolve_auth_paths,
    resolve_prompt_cache_key,
)


def sse_event(event: str | None, data: dict[str, Any]) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {data_json(data)}\n\n".encode("utf-8")


def data_json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


class FakeContent:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.index = 0

    async def iter_chunked(self, _size: int):
        while self.index < len(self.chunks):
            chunk = self.chunks[self.index]
            self.index += 1
            yield chunk


class FailingContent:
    def __init__(self, exc: BaseException, chunks: list[bytes] | None = None):
        self.exc = exc
        self.chunks = chunks or []
        self.index = 0

    async def iter_chunked(self, _size: int):
        while self.index < len(self.chunks):
            chunk = self.chunks[self.index]
            self.index += 1
            yield chunk
        raise self.exc


class FakeResponse:
    def __init__(
        self,
        events: list[tuple[str | None, dict[str, Any]]] | None = None,
        chunks: list[bytes] | None = None,
        status: int = 200,
        text: str = "",
    ):
        self.content = FakeContent(chunks if chunks is not None else [sse_event(event, data) for event, data in events or []])
        self.released = False
        self.status = status
        self._text = text

    def release(self) -> None:
        self.released = True

    async def text(self) -> str:
        return self._text


class FailingResponse(FakeResponse):
    def __init__(self, exc: BaseException, chunks: list[bytes] | None = None):
        self.content = FailingContent(exc, chunks)
        self.released = False
        self.status = 200
        self._text = ""


class CapturePostSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.timeout = None

    async def post(self, *_args, **kwargs):
        self.timeout = kwargs.get("timeout")
        return self.response


class FakeJsonContextResponse:
    def __init__(self, status: int, payload: dict[str, Any]):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None

    async def json(self):
        return self.payload


class OAuthSession:
    def __init__(self, status: int = 200, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, delay: float = 0):
        self.status = status
        self.payload = payload or {}
        self.headers = headers or {}
        self.delay = delay
        self.calls = 0
        self.data: dict[str, str] | None = None
        self.kwargs: dict[str, Any] = {}

    def post(self, _url: str, **kwargs):
        owner = self
        owner.calls += 1
        owner.data = kwargs.get("data")
        owner.kwargs = kwargs

        class Response:
            status = owner.status
            headers = owner.headers

            async def __aenter__(self):
                if owner.delay:
                    await asyncio.sleep(owner.delay)
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

            async def json(self):
                return owner.payload

        return Response()


class NoCallOAuthSession:
    def post(self, *_args, **_kwargs):
        raise AssertionError("OAuth refresh should not be called")


class RaisingOAuthSession:
    def __init__(self, error: BaseException):
        self.error = error
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        raise self.error


class SharedOAuthSession:
    def __init__(self, counter: Any, access_token: str):
        self.counter = counter
        self.access_token = access_token

    def post(self, _url: str, **_kwargs):
        owner = self
        with owner.counter.get_lock():
            owner.counter.value += 1

        class Response:
            status = 200
            headers: dict[str, str] = {}

            async def __aenter__(self):
                await asyncio.sleep(0.1)
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

            async def json(self):
                return {"access_token": owner.access_token, "refresh_token": "refresh-rotated"}

        return Response()


def run_refresh_process(source: str, store: str, access: str, counter: Any, start: Any, results: Any) -> None:
    router = CodexRouter.__new__(CodexRouter)
    router.session = SharedOAuthSession(counter, access)
    router.telemetry = None
    router.slots = []
    router.leases = {}
    router.refresh_locks = {}
    router.auth_store_root = Path(store)
    router._availability_event = None
    router._availability_event_loop = None
    slot = CodexSlot(0, path=source)
    start.wait()
    auth = asyncio.run(router.load_auth(slot))
    results.put((auth.access_token if auth else None, slot.auth_error_code))


class FakeUsageSession:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.urls.append(url)
        if "rate-limit-reset-credits" in url:
            return FakeJsonContextResponse(200, {"available_count": 1, "credits": []})
        return FakeJsonContextResponse(200, {
            "primary_window": {"used_percent": 25, "reset_at": "2026-06-27T10:00:00Z"},
            "secondary_window": {"used_percent": 40, "reset_at": "2026-07-01T10:00:00Z"},
        })


class PayloadUsageSession(FakeUsageSession):
    def __init__(self, payload: dict[str, Any]):
        super().__init__()
        self.payload = payload

    def get(self, url: str, **_kwargs):
        self.urls.append(url)
        if "rate-limit-reset-credits" in url:
            return FakeJsonContextResponse(200, {"available_count": 1, "credits": []})
        return FakeJsonContextResponse(200, self.payload)


class RetryRouter(CodexRouter):
    def __init__(self):
        self.slots = [
            CodexSlot(0, auth=CodexAuth("token-a", account_id="acct-a")),
            CodexSlot(1, auth=CodexAuth("token-b", account_id="acct-b")),
        ]
        self.leases = {}
        self.calls: list[int] = []

    async def choose_slot(self, _affinity_key, excluded_slots, excluded_accounts):
        for slot in self.slots:
            auth = slot.auth
            if not auth:
                continue
            if slot.index in excluded_slots or auth.account_key in excluded_accounts:
                continue
            return slot, auth
        return None

    async def _single_chat_call(self, _body, _model_id, _model_name, _wants_stream, slot, _auth):
        self.calls.append(slot.index)
        if slot.index == 0:
            return CodexCallResult(
                status=502,
                body={"error": {"type": "upstream_error", "code": "codex_empty_response"}},
                error="codex_empty_response",
            )
        return CodexCallResult(status=200, body={"ok": True}, output_tokens=1, completed=True)


class StreamingCloseRouter(CodexRouter):
    def __init__(self):
        self.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="acct"))]
        self.leases = {}
        self.refresh_locks = {}

    async def choose_slot(self, _affinity_key, _excluded_slots, _excluded_accounts):
        return self.slots[0], self.slots[0].auth

    async def _single_chat_call(self, _body, _model_id, _model_name, _wants_stream, _slot, _auth):
        result = CodexCallResult(status=200)

        async def stream():
            try:
                yield b"data: {}\n\n"
            finally:
                result.input_tokens = 1000
                result.cached_input_tokens = 512
                result.output_tokens = 7
                result.usage_seen = True
                result.usage_event_type = "response.completed"
                result.completed = True

        result.stream = stream()
        return result


class CaptureModelNameRouter(CodexRouter):
    def __init__(self):
        self.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="acct"))]
        self.leases = {}
        self.refresh_locks = {}
        self.telemetry = None
        self.captured: dict[str, Any] | None = None

    async def choose_slot(self, _affinity_key, _excluded_slots, _excluded_accounts):
        return self.slots[0], self.slots[0].auth

    async def _single_chat_call(self, body, model_id, model_name, _wants_stream, _slot, _auth):
        self.captured = {"body": body, "model_id": model_id, "model_name": model_name}
        return CodexCallResult(status=200, body={"ok": True}, completed=True)


class RecoveringRouter(CodexRouter):
    def __init__(self, *, transient_until: float = 0, rate_limited_until: float = 0):
        self.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="acct"))]
        self.slots[0].transient_until = transient_until
        self.slots[0].rate_limited_until = rate_limited_until
        self.leases = {}
        self.refresh_locks = {}
        self.telemetry = None
        self._usage_cache = None
        self._availability_event = None
        self._availability_event_loop = None
        self.calls = 0

    async def _single_chat_call(self, _body, _model_id, _model_name, _wants_stream, _slot, _auth):
        self.calls += 1
        return CodexCallResult(status=200, body={"ok": True}, output_tokens=1, completed=True)

    async def _single_image_call(self, _request, _slot, _auth):
        self.calls += 1
        return CodexCallResult(status=200, body={"data": [{"b64_json": "abc"}]}, completed=True)

    async def _probe_rate_limit_recovery(self) -> bool:
        return False


class MixedPoolFailureRouter(CodexRouter):
    def __init__(self, statuses: list[int]):
        self.slots = [
            CodexSlot(
                index,
                auth=CodexAuth(f"token-{index}", account_id=f"acct-{index}"),
                auth_fingerprint=f"fingerprint-{index}",
            )
            for index in range(len(statuses))
        ]
        self.statuses = statuses
        self.calls: list[int] = []
        self.recovery_probes = 0
        self.leases = {}
        self.refresh_locks = {}
        self.telemetry = None
        self._usage_cache = None
        self._availability_event = None
        self._availability_event_loop = None

    async def _failure(self, slot: CodexSlot) -> CodexCallResult:
        self.calls.append(slot.index)
        status = self.statuses[slot.index]
        if status == 429:
            return CodexCallResult(
                status=429,
                body={
                    "error": {
                        "message": "usage exhausted",
                        "type": "usage_limit_reached",
                        "code": "usage_limit_reached",
                        "resets_in_seconds": 3600,
                    }
                },
                error="usage_limit_reached",
            )
        return CodexCallResult(
            status=401,
            body={
                "error": {
                    "message": "token invalidated",
                    "type": "invalid_request_error",
                    "code": "token_invalidated",
                }
            },
            error="token_invalidated",
        )

    async def _single_chat_call(self, _body, _model_id, _model_name, _wants_stream, slot, _auth):
        return await self._failure(slot)

    async def _single_image_call(self, _request, slot, _auth):
        return await self._failure(slot)

    async def _probe_rate_limit_recovery(self) -> bool:
        self.recovery_probes += 1
        return False


class RequestRecoveryRouter(RecoveringRouter):
    def __init__(self):
        super().__init__(rate_limited_until=time.time() + 3600)
        self.recovery_probes = 0

    async def _probe_rate_limit_recovery(self) -> bool:
        self.recovery_probes += 1
        self.slots[0].rate_limited_until = 0
        return True


class CodexTests(unittest.TestCase):
    def jwt(self, expiry: float) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
        return f"x.{payload}.x"

    def auth_router(self, session: Any, store_root: Path) -> CodexRouter:
        router = CodexRouter.__new__(CodexRouter)
        router.session = session
        router.telemetry = None
        router.slots = []
        router.leases = {}
        router.refresh_locks = {}
        router.auth_store_root = store_root
        router._availability_event = None
        router._availability_event_loop = None
        return router

    def write_auth(
        self,
        path: Path,
        access: str,
        refresh: str = "refresh-old",
        account_id: str | None = "acct",
    ) -> bytes:
        tokens = {
            "access_token": access,
            "refresh_token": refresh,
        }
        if account_id is not None:
            tokens["account_id"] = account_id
        raw = (
            json.dumps(
                {"tokens": tokens},
                sort_keys=True,
            )
            + "\n"
        ).encode()
        path.write_bytes(raw)
        return raw

    def usage_router(self, session: Any, store: Any, slots: list[CodexSlot]) -> CodexRouter:
        with patch.dict(os.environ, {"OPENAI_CODEX_TOKEN": "test-token"}, clear=True):
            router = CodexRouter(session, store)
        router.slots = slots
        return router

    def live_usage_result(
        self,
        account_key: str,
        slots: list[CodexSlot],
        *,
        used_percent: float = 40,
    ) -> dict[str, Any]:
        updated_at = "2026-07-19T10:00:00Z"
        weekly = {
            "window": "weekly",
            "usedPercent": used_percent,
            "remainingPercent": 100 - used_percent,
            "resetAt": "2026-07-25T10:00:00Z",
            "windowMinutes": 10080,
            "updatedAt": updated_at,
        }
        return {
            "row": {
                "accountKey": account_key,
                "slotIndexes": [slot.index for slot in slots],
                "slotPaths": [slot.path for slot in slots if slot.path],
                "source": "live",
                "stale": False,
                "fiveHour": None,
                "weekly": weekly,
                "resetCredits": None,
            },
            "usageError": None,
            "resetError": None,
            "usageRecords": [{
                "account_key": account_key,
                "slot_index": slots[0].index,
                "window": "weekly",
                "used_percent": used_percent,
                "reset_at": weekly["resetAt"],

                "window_minutes": 10080,
            }],
            "resetRecord": None,
        }

    def test_refresh_persists_managed_state_without_mutating_bootstrap_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            original = self.write_auth(source, self.jwt(time.time() - 600))
            store = root / "codex-auth"
            session = OAuthSession(payload={
                "access_token": self.jwt(time.time() + 7200),
                "refresh_token": "refresh-new",
            })
            router = self.auth_router(session, store)
            slot = CodexSlot(0, path=str(source))

            auth = asyncio.run(router.load_auth(slot))

            self.assertIsNotNone(auth)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(session.calls, 1)
            self.assertEqual(session.data["grant_type"], "refresh_token")
            self.assertFalse(session.kwargs["allow_redirects"])
            self.assertNotIn("json", session.data)
            self.assertEqual(slot.auth_state_source, "managed")
            state_file = store / f"{router._source_identity(str(source))}.json"
            self.assertEqual(stat.S_IMODE(state_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o700)

            restarted = self.auth_router(NoCallOAuthSession(), store)
            restarted_slot = CodexSlot(0, path=str(source))
            restarted_auth = asyncio.run(restarted.load_auth(restarted_slot))
            self.assertEqual(restarted_auth.access_token, auth.access_token)
            self.assertEqual(restarted_slot.auth_state_source, "managed")

    def test_candidate_selection_prefers_expiry_then_bootstrap_and_enforces_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            store = root / "codex-auth"
            router = self.auth_router(NoCallOAuthSession(), store)
            bootstrap_expiry = time.time() + 3600
            self.write_auth(source, self.jwt(bootstrap_expiry))
            managed = router._persist_refresh(str(source), {
                "access_token": self.jwt(bootstrap_expiry + 3600),
                "refresh_token": "managed",
                "account_id": "acct",
            })
            self.assertEqual(router._load_candidate(str(source)).fingerprint, managed.fingerprint)

            self.write_auth(source, self.jwt(bootstrap_expiry + 7200), "bootstrap-new")
            selected = router._load_candidate(str(source))
            self.assertEqual(selected.source, "bootstrap")

            state_file = store / f"{router._source_identity(str(source))}.json"
            document = json.loads(state_file.read_text())
            document["sourcePathHash"] = "0" * 64
            state_file.write_text(json.dumps(document))
            with self.assertRaisesRegex(CodexAuthStoreError, "auth_state_binding_mismatch"):
                router._read_managed_candidate(str(source))

    def test_candidate_selection_rejects_newer_managed_state_for_another_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            expiry = time.time() + 3600
            self.write_auth(
                source,
                self.jwt(expiry),
                "bootstrap-refresh",
                "bootstrap-account",
            )
            router._persist_refresh(str(source), {
                "access_token": self.jwt(expiry + 7200),
                "refresh_token": "managed-refresh",
                "account_id": "managed-account",
            })

            selected = router._load_candidate(str(source))

            self.assertEqual(selected.source, "bootstrap")
            self.assertEqual(derive_account_id(selected.tokens), "bootstrap-account")

    def test_expired_bootstrap_for_another_account_refreshes_and_replaces_managed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            store = root / "codex-auth"
            self.write_auth(
                source,
                self.jwt(time.time() - 600),
                "bootstrap-refresh",
                "bootstrap-account",
            )
            setup = self.auth_router(NoCallOAuthSession(), store)
            setup._persist_refresh(str(source), {
                "access_token": self.jwt(time.time() + 7200),
                "refresh_token": "managed-refresh",
                "account_id": "managed-account",
            })
            session = OAuthSession(payload={
                "access_token": self.jwt(time.time() + 3600),
                "refresh_token": "bootstrap-refresh-next",
            })
            router = self.auth_router(session, store)
            slot = CodexSlot(0, path=str(source))

            auth = asyncio.run(router.load_auth(slot))

            self.assertIsNotNone(auth)
            self.assertEqual(auth.account_id, "bootstrap-account")
            self.assertEqual(session.data["refresh_token"], "bootstrap-refresh")
            managed = router._read_managed_candidate(str(source))
            self.assertEqual(derive_account_id(managed.tokens), "bootstrap-account")

    def test_terminal_bootstrap_refresh_does_not_fall_back_to_other_managed_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            store = root / "codex-auth"
            self.write_auth(
                source,
                self.jwt(time.time() - 600),
                "bootstrap-refresh",
                "bootstrap-account",
            )
            setup = self.auth_router(NoCallOAuthSession(), store)
            setup._persist_refresh(str(source), {
                "access_token": self.jwt(time.time() + 7200),
                "refresh_token": "managed-refresh",
                "account_id": "managed-account",
            })
            session = OAuthSession(status=400)
            router = self.auth_router(session, store)
            slot = CodexSlot(0, path=str(source))

            self.assertIsNone(asyncio.run(router.load_auth(slot)))
            self.assertEqual(slot.auth_error_code, "reauth_required")
            self.assertEqual(session.data["refresh_token"], "bootstrap-refresh")
            self.assertEqual(
                derive_account_id(router._read_managed_candidate(str(source)).tokens),
                "managed-account",
            )
            self.assertIsNone(asyncio.run(router.load_auth(slot)))
            self.assertEqual(session.calls, 1)

    def test_candidate_selection_uses_expiry_when_only_one_identity_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            expiry = time.time() + 3600

            self.write_auth(source, self.jwt(expiry), account_id="bootstrap-account")
            router._persist_refresh(str(source), {
                "access_token": self.jwt(expiry + 7200),
                "refresh_token": "managed-refresh",
            })
            self.assertEqual(router._load_candidate(str(source)).source, "bootstrap")

            self.write_auth(source, self.jwt(expiry), account_id=None)
            router._persist_refresh(str(source), {
                "access_token": self.jwt(expiry + 7200),
                "refresh_token": "managed-refresh",
                "account_id": "managed-account",
            })
            selected = router._load_candidate(str(source))
            self.assertEqual(selected.source, "managed")
            self.assertEqual(derive_account_id(selected.tokens), "managed-account")

    def test_managed_store_symlinks_are_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            self.write_auth(source, self.jwt(time.time() + 7200))
            actual_store = root / "actual-store"
            actual_store.mkdir(mode=0o700)
            store_link = root / "codex-auth"
            store_link.symlink_to(actual_store, target_is_directory=True)
            router = self.auth_router(NoCallOAuthSession(), store_link)
            slot = CodexSlot(0, path=str(source))

            auth = asyncio.run(router.load_auth(slot))

            self.assertIsNotNone(auth)
            self.assertFalse(slot.auth_persistence_healthy)
            self.assertEqual(slot.auth_error_code, "auth_store_unavailable")

            safe_store = root / "safe-store"
            safe_router = self.auth_router(NoCallOAuthSession(), safe_store)
            safe_router._persist_refresh(str(source), {
                "access_token": self.jwt(time.time() + 8000),
                "refresh_token": "managed",
                "account_id": "acct",
            })
            state_file = safe_store / f"{safe_router._source_identity(str(source))}.json"
            state_file.unlink()
            external = root / "external.json"
            external.write_text("{}")
            state_file.symlink_to(external)
            with self.assertRaisesRegex(CodexAuthStoreError, "auth_state_unavailable"):
                safe_router._read_managed_candidate(str(source))

    def test_two_processes_coordinate_refresh_with_one_oauth_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            store = root / "codex-auth"
            self.write_auth(source, self.jwt(time.time() - 600))
            access = self.jwt(time.time() + 7200)
            context = multiprocessing.get_context("fork")
            counter = context.Value("i", 0)
            start = context.Event()
            results = context.Queue()
            processes = [
                context.Process(
                    target=run_refresh_process,
                    args=(str(source), str(store), access, counter, start, results),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            observed = [results.get(timeout=2) for _ in processes]
            self.assertEqual(counter.value, 1)
            self.assertEqual(observed, [(access, None), (access, None)])

    def test_two_routers_coordinate_refresh_with_one_oauth_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            store = root / "codex-auth"
            self.write_auth(source, self.jwt(time.time() - 600))
            session = OAuthSession(payload={
                "access_token": self.jwt(time.time() + 7200),
                "refresh_token": "refresh-new",
            }, delay=0.05)
            first = self.auth_router(session, store)
            second = self.auth_router(session, store)
            first_slot = CodexSlot(0, path=str(source))
            second_slot = CodexSlot(0, path=str(source))

            async def exercise():
                return await asyncio.gather(first.load_auth(first_slot), second.load_auth(second_slot))

            auths = asyncio.run(exercise())
            self.assertEqual(session.calls, 1)
            self.assertEqual(auths[0].access_token, auths[1].access_token)
            self.assertEqual(second_slot.auth_state_source, "managed")

    def test_process_lock_times_out_without_blocking_event_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            source.write_text("{}")
            store = root / "codex-auth"
            first = self.auth_router(NoCallOAuthSession(), store)
            second = self.auth_router(NoCallOAuthSession(), store)

            async def exercise() -> None:
                held = await first._acquire_process_lock(str(source))
                ticked = False

                async def tick() -> None:
                    nonlocal ticked
                    await asyncio.sleep(0)
                    ticked = True

                try:
                    with patch("spartan_route.codex.CODEX_AUTH_LOCK_TIMEOUT_SECONDS", 0.01):
                        ticker = asyncio.create_task(tick())
                        with self.assertRaisesRegex(CodexAuthStoreError, "auth_lock_timeout"):
                            await second._acquire_process_lock(str(source))
                        await ticker
                finally:
                    first._release_process_lock(held)
                self.assertTrue(ticked)

            asyncio.run(exercise())

    def test_persist_failures_at_create_file_fsync_and_replace_cleanup_temps(self) -> None:
        tokens = {
            "access_token": self.jwt(time.time() + 7200),
            "refresh_token": "refresh-new",
            "account_id": "acct",
        }
        for failure in ("create", "file_fsync", "replace"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "auth.json"
                source.write_text("{}")
                store = root / "codex-auth"
                router = self.auth_router(NoCallOAuthSession(), store)
                real_open = os.open
                real_fsync = os.fsync

                def guarded_open(name, flags, mode=0o777, *, dir_fd=None):
                    if failure == "create" and isinstance(name, str) and name.endswith(".tmp"):
                        raise OSError("simulated temp create failure")
                    if dir_fd is None:
                        return real_open(name, flags, mode)
                    return real_open(name, flags, mode, dir_fd=dir_fd)

                def guarded_fsync(fd):
                    if failure == "file_fsync" and stat.S_ISREG(os.fstat(fd).st_mode):
                        raise OSError("simulated file fsync failure")
                    return real_fsync(fd)

                replace_effect = OSError("simulated replace failure") if failure == "replace" else None
                with patch("spartan_route.codex.os.open", side_effect=guarded_open), \
                     patch("spartan_route.codex.os.fsync", side_effect=guarded_fsync), \
                     patch("spartan_route.codex.os.replace", side_effect=replace_effect) as replace_call:
                    if failure != "replace":
                        replace_call.side_effect = None
                        replace_call.wraps = os.replace
                    with self.assertRaisesRegex(CodexAuthStoreError, "auth_persist_failed"):
                        router._persist_refresh(str(source), tokens)
                self.assertFalse(any(item.name.endswith(".tmp") for item in store.iterdir()))

    def test_directory_fsync_failure_does_not_adopt_refreshed_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            store = root / "codex-auth"
            self.write_auth(source, self.jwt(time.time() - 600))
            session = OAuthSession(payload={
                "access_token": self.jwt(time.time() + 7200),
                "refresh_token": "refresh-new",
            })
            router = self.auth_router(session, store)
            slot = CodexSlot(0, path=str(source))
            real_fsync = os.fsync
            directory_calls = 0

            def fail_final_directory_fsync(fd: int) -> None:
                nonlocal directory_calls
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_calls += 1
                    if directory_calls == 8:
                        raise OSError("simulated directory fsync failure")
                real_fsync(fd)

            with patch("spartan_route.codex.os.fsync", side_effect=fail_final_directory_fsync):
                auth = asyncio.run(router.load_auth(slot))
            self.assertIsNone(auth)
            self.assertIsNone(slot.auth)
            self.assertEqual(slot.auth_error_code, "refresh_persist_failed")
            self.assertFalse(slot.auth_persistence_healthy)

            restarted = self.auth_router(NoCallOAuthSession(), store)
            recovered = asyncio.run(restarted.load_auth(CodexSlot(0, path=str(source))))
            self.assertIsNotNone(recovered)

    def test_refresh_http_statuses_are_exhaustively_classified_and_terminal_is_quarantined(self) -> None:
        cases = [
            (400, "reauth_required", True),
            (401, "reauth_required", True),
            (403, "reauth_required", True),
            (418, "reauth_required", True),
            (429, "refresh_rate_limited", False),
            (503, "refresh_temporarily_unavailable", False),
            (302, "refresh_invalid_response", False),
            (204, "refresh_invalid_response", False),
            (101, "refresh_invalid_response", False),
            (600, "refresh_invalid_response", False),
        ]
        for status, code, terminal in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "auth.json"
                self.write_auth(source, self.jwt(time.time() - 600))
                session = OAuthSession(status=status)
                router = self.auth_router(session, root / "codex-auth")
                slot = CodexSlot(0, path=str(source))
                self.assertIsNone(asyncio.run(router.load_auth(slot)))
                self.assertEqual(slot.auth_error_code, code)
                self.assertEqual(slot.failed_auth_fingerprint is not None, terminal)
                self.assertEqual(slot.auth_retry_at > time.time(), not terminal)
                self.assertIsNone(asyncio.run(router.load_auth(slot)))
                self.assertEqual(session.calls, 1)

    def test_valid_bootstrap_serves_when_store_is_temporarily_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            self.write_auth(source, self.jwt(time.time() + 7200))
            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            slot = CodexSlot(0, path=str(source))
            with patch.object(
                router,
                "_read_managed_candidate",
                side_effect=CodexAuthStoreError("auth_store_unavailable"),
            ):
                auth = asyncio.run(router.load_auth(slot))
            self.assertIsNotNone(auth)
            self.assertFalse(slot.auth_persistence_healthy)
            self.assertEqual(slot.auth_error_code, "auth_store_unavailable")
            self.assertIsNone(slot.auth_unavailable_reason)

    def test_invalid_refresh_payload_is_retryable_and_retry_after_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            self.write_auth(source, self.jwt(time.time() - 600))
            invalid = self.auth_router(OAuthSession(status=200, payload={}), root / "state-a")
            invalid_slot = CodexSlot(0, path=str(source))
            self.assertIsNone(asyncio.run(invalid.load_auth(invalid_slot)))
            self.assertEqual(invalid_slot.auth_error_code, "refresh_invalid_response")
            self.assertGreater(invalid_slot.auth_retry_at, time.time())

            limited = self.auth_router(
                OAuthSession(status=429, headers={"Retry-After": "999999"}),
                root / "state-b",
            )
            limited_slot = CodexSlot(0, path=str(source))
            before = time.time()
            self.assertIsNone(asyncio.run(limited.load_auth(limited_slot)))
            self.assertLessEqual(limited_slot.auth_retry_at, before + 3601)

    def test_refresh_distinguishes_network_internal_and_invalid_identity_failures(self) -> None:
        cases = [
            (asyncio.TimeoutError(), "refresh_temporarily_unavailable"),
            (RuntimeError("bug"), "refresh_internal_error"),
        ]
        for error, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "auth.json"
                self.write_auth(source, self.jwt(time.time() - 600))
                session = RaisingOAuthSession(error)
                slot = CodexSlot(0, path=str(source))
                router = self.auth_router(session, root / "codex-auth")
                self.assertIsNone(asyncio.run(router.load_auth(slot)))
                self.assertEqual(slot.auth_error_code, code)
                self.assertGreater(slot.auth_retry_at, time.time())
                self.assertIsNone(slot.failed_auth_fingerprint)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            source.write_text(json.dumps({"tokens": {
                "access_token": self.jwt(time.time() - 600),
                "refresh_token": "refresh-old",
            }}))
            router = self.auth_router(
                OAuthSession(payload={"access_token": self.jwt(time.time() + 7200)}),
                root / "codex-auth",
            )
            slot = CodexSlot(0, path=str(source))
            self.assertIsNone(asyncio.run(router.load_auth(slot)))
            self.assertEqual(slot.auth_error_code, "refresh_invalid_response")
            self.assertGreater(slot.auth_retry_at, time.time())
            self.assertIsNone(slot.failed_auth_fingerprint)

    def test_store_state_and_lock_containment_ownership_and_mode_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            self.write_auth(source, self.jwt(time.time() + 7200))
            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            router._persist_refresh(str(source), {
                "access_token": self.jwt(time.time() + 8000),
                "refresh_token": "managed",
                "account_id": "acct",
            })
            store = root / "codex-auth"
            state_file = next(store.glob("*.json"))
            os.chmod(store, 0o755)
            os.chmod(state_file, 0o644)
            self.assertIsNotNone(router._read_managed_candidate(str(source)))
            lock_fd = asyncio.run(router._acquire_process_lock(str(source)))
            router._release_process_lock(lock_fd)
            lock_file = next(store.glob("*.lock"))
            os.chmod(lock_file, 0o644)
            lock_fd = asyncio.run(router._acquire_process_lock(str(source)))
            router._release_process_lock(lock_fd)
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(state_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(lock_file.stat().st_mode), 0o600)

            real_fstat = os.fstat

            def foreign_on_third_call():
                calls = 0
                def foreign(fd):
                    nonlocal calls
                    calls += 1
                    metadata = real_fstat(fd)
                    if calls == 3:
                        values = list(metadata)
                        values[4] = os.geteuid() + 1
                        return os.stat_result(values)
                    return metadata
                return foreign

            with patch("spartan_route.codex.os.fstat", side_effect=foreign_on_third_call()):
                with self.assertRaisesRegex(CodexAuthStoreError, "auth_state_unsafe"):
                    router._read_managed_candidate(str(source))
            with patch("spartan_route.codex.os.fstat", side_effect=foreign_on_third_call()):
                with self.assertRaisesRegex(CodexAuthStoreError, "auth_lock_unsafe"):
                    asyncio.run(router._acquire_process_lock(str(source)))

            unsafe_parent = root / "parent-file"
            unsafe_parent.write_text("not a directory")
            unsafe_router = self.auth_router(NoCallOAuthSession(), unsafe_parent / "codex-auth")
            with self.assertRaisesRegex(CodexAuthStoreError, "auth_store_parent_unavailable"):
                unsafe_router._read_managed_candidate(str(source))
            foreign_parent = self.auth_router(NoCallOAuthSession(), root / "other-store")
            parent_metadata = list(root.stat())
            parent_metadata[4] = os.geteuid() + 1
            with patch("spartan_route.codex.os.fstat", return_value=os.stat_result(parent_metadata)):
                with self.assertRaisesRegex(CodexAuthStoreError, "auth_store_parent_unsafe"):
                    foreign_parent._read_managed_candidate(str(source))

    def test_non_regular_store_state_and_lock_entries_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            self.write_auth(source, self.jwt(time.time() + 7200))

            store_file = root / "store-file"
            store_file.write_text("not a directory")
            file_router = self.auth_router(NoCallOAuthSession(), store_file)
            with self.assertRaisesRegex(CodexAuthStoreError, "auth_store_unavailable"):
                file_router._read_managed_candidate(str(source))

            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            router._persist_refresh(str(source), {
                "access_token": self.jwt(time.time() + 8000),
                "refresh_token": "managed",
                "account_id": "acct",
            })
            store = root / "codex-auth"
            state_file = next(store.glob("*.json"))
            state_file.unlink()
            state_file.mkdir()
            with self.assertRaisesRegex(CodexAuthStoreError, "auth_state_unsafe"):
                router._read_managed_candidate(str(source))

            lock_name = store / f"{router._source_identity(str(source))}.lock"
            lock_name.mkdir()
            with self.assertRaisesRegex(CodexAuthStoreError, "auth_lock_unavailable"):
                asyncio.run(router._acquire_process_lock(str(source)))

    def test_refresh_error_contract_never_exposes_upstream_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            self.write_auth(source, self.jwt(time.time() - 600))
            session = OAuthSession(status=400)
            session.raw_body = "sensitive upstream body"
            router = self.auth_router(session, root / "codex-auth")
            slot = CodexSlot(0, path=str(source))
            self.assertIsNone(asyncio.run(router.load_auth(slot)))
            router.slots = [slot]
            encoded = json.dumps(router.slot_status())
            self.assertNotIn(session.raw_body, encoded)
            self.assertIn("reauth_required", encoded)
            self.assertFalse(session.kwargs["allow_redirects"])

    def test_candidate_selection_rejects_invalid_expiry_and_prefers_bootstrap_ties(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            expiry = time.time() + 7200
            router._persist_refresh(str(source), {
                "access_token": self.jwt(expiry),
                "refresh_token": "managed",
                "account_id": "acct",
            })
            self.write_auth(source, self.jwt(expiry), "bootstrap")
            self.assertEqual(router._load_candidate(str(source)).source, "bootstrap")

            router._persist_refresh(str(source), {
                "access_token": self.jwt(float("nan")),
                "refresh_token": "managed-invalid",
                "account_id": "acct",
            })
            self.write_auth(source, "not-a-jwt", "bootstrap-no-expiry")
            self.assertEqual(router._load_candidate(str(source)).source, "bootstrap")

            router._persist_refresh(str(source), {
                "access_token": self.jwt(expiry),
                "refresh_token": "managed-finite",
                "account_id": "acct",
            })
            self.write_auth(source, "not-a-jwt", "bootstrap-no-expiry")
            self.assertEqual(router._load_candidate(str(source)).source, "managed")

    def test_terminal_quarantine_clears_when_bootstrap_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            expired = self.jwt(time.time() - 600)
            self.write_auth(source, expired, "dead-refresh")
            failed_session = OAuthSession(status=400)
            router = self.auth_router(failed_session, root / "codex-auth")
            slot = CodexSlot(0, path=str(source))
            self.assertIsNone(asyncio.run(router.load_auth(slot)))
            self.assertEqual(slot.auth_error_code, "reauth_required")
            self.assertIsNotNone(slot.failed_auth_fingerprint)

            self.write_auth(source, expired, "fresh-login-refresh")
            recovered_session = OAuthSession(payload={
                "access_token": self.jwt(time.time() + 7200),
                "refresh_token": "rotated-refresh",
            })
            router.session = recovered_session
            recovered = asyncio.run(router.load_auth(slot))
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered_session.calls, 1)
            self.assertIsNone(slot.failed_auth_fingerprint)

    def test_changed_bootstrap_recovers_quota_cooled_slot_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            old_access = self.jwt(time.time() + 7200)
            self.write_auth(source, old_access, account_id="old-account")
            snapshot = read_auth_file(str(source))
            self.assertIsNotNone(snapshot)
            assert snapshot is not None

            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            slot = CodexSlot(
                0,
                path=str(source),
                auth=CodexAuth(old_access, account_id="old-account"),
                auth_fingerprint=snapshot[1],
                auth_source_fingerprint=snapshot[1],
                rate_limited_until=time.time() + 3600,
            )
            other_slot = CodexSlot(
                1,
                auth=CodexAuth("other-token", account_id="other-account"),
                rate_limited_until=time.time() + 1800,
            )
            router.slots = [slot, other_slot]
            router._remember_lease("stale-session", slot)
            self.assertTrue(router.leases)

            new_access = self.jwt(time.time() + 10800)
            self.write_auth(source, new_access, account_id="new-account")

            selected = asyncio.run(router.choose_slot(None, set(), set()))

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(selected[1].account_id, "new-account")
            self.assertFalse(router.leases)
            self.assertEqual(slot.rate_limited_until, 0)
            self.assertEqual(other_slot.auth.account_id, "other-account")
            self.assertGreater(other_slot.rate_limited_until, time.time())

    def test_unchanged_or_touched_bootstrap_keeps_quota_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            access = self.jwt(time.time() + 7200)
            self.write_auth(source, access, account_id="account")
            snapshot = read_auth_file(str(source))
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            cooldown = time.time() + 3600
            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            slot = CodexSlot(
                0,
                path=str(source),
                auth=CodexAuth(access, account_id="account"),
                auth_fingerprint=snapshot[1],
                auth_source_fingerprint=snapshot[1],
                rate_limited_until=cooldown,
            )
            router.slots = [slot]

            os.utime(source, None)
            selected = asyncio.run(router.choose_slot(None, set(), set()))

            self.assertIsNone(selected)
            self.assertEqual(slot.rate_limited_until, cooldown)
            self.assertEqual(slot.auth.account_id, "account")

    def test_invalid_bootstrap_does_not_reuse_quota_cooled_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            access = self.jwt(time.time() + 7200)
            self.write_auth(source, access, account_id="old-account")
            snapshot = read_auth_file(str(source))
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            slot = CodexSlot(
                0,
                path=str(source),
                auth=CodexAuth(access, account_id="old-account"),
                auth_fingerprint=snapshot[1],
                auth_source_fingerprint=snapshot[1],
                rate_limited_until=time.time() + 3600,
            )
            router.slots = [slot]
            source.write_text("not json", encoding="utf-8")

            selected = asyncio.run(router.choose_slot(None, set(), set()))

            self.assertIsNone(selected)
            self.assertIsNone(slot.auth)
            self.assertEqual(slot.auth_unavailable_reason, "missing")

    def test_parent_and_read_fsync_failures_remain_retryable_until_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            self.write_auth(source, self.jwt(time.time() - 600))
            session = OAuthSession(payload={
                "access_token": self.jwt(time.time() + 7200),
                "refresh_token": "refresh-new",
            })
            router = self.auth_router(session, root / "codex-auth")
            slot = CodexSlot(0, path=str(source))
            real_fsync = os.fsync
            directory_calls = 0

            def fail_created_store_parent(fd):
                nonlocal directory_calls
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_calls += 1
                    if directory_calls == 2:
                        raise OSError("parent durability failure")
                return real_fsync(fd)

            with patch("spartan_route.codex.os.fsync", side_effect=fail_created_store_parent):
                self.assertIsNone(asyncio.run(router.load_auth(slot)))
            self.assertEqual(session.calls, 0)
            self.assertEqual(slot.auth_error_code, "auth_store_unavailable")
            self.assertIsNone(slot.failed_auth_fingerprint)

            slot.auth_retry_at = 0
            self.assertIsNotNone(asyncio.run(router.load_auth(slot)))
            self.assertEqual(session.calls, 1)
            managed_file = next((root / "codex-auth").glob("*.json"))
            self.assertTrue(managed_file.is_file())

            unavailable = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            unavailable_slot = CodexSlot(0, path=str(source))
            with patch("spartan_route.codex.os.fsync", side_effect=OSError("read durability failure")):
                self.assertIsNone(asyncio.run(unavailable.load_auth(unavailable_slot)))
            self.assertEqual(unavailable_slot.auth_error_code, "auth_store_unavailable")
            self.assertIsNone(unavailable_slot.failed_auth_fingerprint)

            recovered = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            self.assertIsNotNone(asyncio.run(recovered.load_auth(CodexSlot(0, path=str(source)))))

    def test_static_token_bypasses_managed_auth_store(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        auth = CodexAuth("static")
        slot = CodexSlot(0, token="static", auth=auth)
        self.assertIs(asyncio.run(router.load_auth(slot)), auth)

    def test_static_token_status_reports_static_lifecycle(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        router.telemetry = None
        router.leases = {}
        with patch.dict(os.environ, {"OPENAI_CODEX_TOKEN": self.jwt(time.time() + 7200)}, clear=True):
            router._init_slots()
        status = router.slot_status()[0]
        self.assertEqual(status["authStateSource"], "static")
        self.assertIsNotNone(status["authExpiresAt"])
        self.assertTrue(status["authPersistenceHealthy"])

    def test_cleanup_cancels_inflight_usage_refresh_without_resetting_slots(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        slot = CodexSlot(0, auth=CodexAuth("token", account_id="acct"))
        router.slots = [slot]

        async def exercise() -> None:
            started = asyncio.Event()

            async def refresh() -> None:
                started.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(refresh())
            router._usage_refresh_task = task
            await started.wait()
            await router.cleanup()
            self.assertTrue(task.cancelled())
            self.assertIsNone(router._usage_refresh_task)

        asyncio.run(exercise())
        self.assertEqual(router.slots, [slot])

    def test_cleanup_swallows_completed_usage_refresh_failure(self) -> None:
        router = CodexRouter.__new__(CodexRouter)

        async def exercise() -> None:
            async def refresh() -> None:
                raise RuntimeError("refresh failed")

            task = asyncio.create_task(refresh())
            router._usage_refresh_task = task
            await asyncio.sleep(0)
            with patch("builtins.print") as output:
                await router.cleanup()
            self.assertIsNone(router._usage_refresh_task)
            output.assert_called_once()

        asyncio.run(exercise())

    def test_stream_transport_error_preserves_observed_tool_calls(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        state = CodexEventState(
            tool_calls={"call-1": {"id": "call-1", "type": "function", "function": {"name": "lookup"}}}
        )
        slot = CodexSlot(0)

        result = router._stream_transport_result(RuntimeError("stream failed"), slot, state)

        self.assertEqual(result.actual_tool_call_count, 1)
        self.assertEqual(result.actual_tool_names, ["lookup"])

    def test_content_prompt_cache_key_is_stable_for_tool_order(self) -> None:
        base = {
            "messages": [{"role": "system", "content": "stable system"}],
            "tools": [
                {"type": "function", "function": {"name": "b", "parameters": {"type": "object"}}},
                {"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}},
            ],
        }
        reordered = {
            **base,
            "tools": list(reversed(base["tools"])),
        }
        self.assertEqual(build_content_prompt_cache_key(base), build_content_prompt_cache_key(reordered))

    def test_explicit_prompt_cache_key_is_namespaced_and_hashed(self) -> None:
        key = resolve_prompt_cache_key({"prompt_cache_key": "session-1"}, None, None)
        self.assertIsNotNone(key)
        self.assertTrue(key.startswith("clawroute:"))
        self.assertNotIn("session-1", key)

    def test_resolve_auth_paths_matches_clawroute_contract(self) -> None:
        env = {"OPENAI_CODEX_AUTH_PATHS": "/a,/b/auth.json"}
        self.assertEqual(resolve_auth_paths(env), ["/a/auth.json", "/b/auth.json"])
        self.assertEqual(resolve_auth_paths({"OPENAI_CODEX_AUTH_PATH": "/c"}), ["/c/auth.json"])
        self.assertEqual(resolve_auth_paths({"OPENAI_CODEX_TOKEN": "tok"}), [])
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(resolve_auth_paths()[0].endswith(".codex/auth.json"))

    def image_router(self, response: FakeResponse) -> CodexRouter:
        router = CodexRouter.__new__(CodexRouter)
        router.session = CapturePostSession(response)
        router.telemetry = None
        router.slots = [CodexSlot(0, auth=CodexAuth("token", account_id="acct"))]
        router.leases = {}
        router.refresh_locks = {}
        return router

    def png_b64(self, width: int, height: int) -> str:
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + (b"\x00" * 160)
        return base64.b64encode(raw).decode("ascii")

    def test_codex_image_payload_uses_minimal_builtin_tool_shape(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        body = router._build_codex_image_body({"prompt": "draw a gate", "size": "1536x1024", "quality": "high"})

        self.assertEqual(body["model"], "gpt-5.5")
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["input"][0]["type"], "message")
        tool = body["tools"][0]
        self.assertEqual(tool, {"type": "image_generation", "output_format": "png", "size": "1536x1024"})
        prompt_text = body["input"][0]["content"][-1]["text"]
        self.assertIn("Size: 1536x1024", prompt_text)
        self.assertIn("Quality: high", prompt_text)

    def test_codex_image_edit_payload_uses_mask_action_and_fidelity(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        mask = "data:image/png;base64,bWFzaw=="
        source = "data:image/png;base64,c291cmNl"

        body = router._build_codex_image_body({
            "prompt": "repair the grip",
            "size": "1024x1536",
            "quality": "high",
            "images": [source],
            "mask": mask,
            "input_fidelity": "high",
        })

        self.assertEqual(body["input"][0]["content"][0], {"type": "input_image", "image_url": source})
        self.assertEqual(body["tool_choice"], "required")
        self.assertEqual(body["tools"][0], {
            "type": "image_generation",
            "output_format": "png",
            "size": "1024x1536",
            "action": "edit",
            "input_image_mask": {"image_url": mask},
            "input_fidelity": "high",
        })

    def test_codex_image_passes_through_non_exact_portrait_results(self) -> None:
        for width, height in ((864, 1821), (941, 1672)):
            with self.subTest(actual=f"{width}x{height}"):
                image = self.png_b64(width, height)
                router = self.image_router(FakeResponse([
                    ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
                ]))

                result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1536"}))

                self.assertEqual(result.status, 200)
                self.assertEqual(result.body["data"][0]["b64_json"], image)
                self.assertEqual(result.body["data"][0]["actual_size"], f"{width}x{height}")

    def test_codex_image_passes_through_wrong_orientation_result(self) -> None:
        image = self.png_b64(1536, 1024)
        router = self.image_router(FakeResponse([
            ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a portrait gate", "size": "1024x1536"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["b64_json"], image)
        self.assertEqual(result.body["data"][0]["actual_size"], "1536x1024")

    def test_codex_image_passes_through_when_dimensions_are_unknown(self) -> None:
        image = base64.b64encode(b"not an image header").decode("ascii")
        router = self.image_router(FakeResponse([
            ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1536"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["b64_json"], image)
        self.assertNotIn("actual_size", result.body["data"][0])

    def test_codex_image_empty_result_is_still_an_error(self) -> None:
        router = self.image_router(FakeResponse([
            ("response.completed", {"response": {"status": "completed", "output": []}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1536"}))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_image_empty")

    def test_codex_image_allows_matching_non_square_result(self) -> None:
        image = self.png_b64(1536, 1024)
        router = self.image_router(FakeResponse([
            ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1536x1024"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["b64_json"], image)

    def test_codex_image_allows_same_aspect_scaled_square_result(self) -> None:
        image = self.png_b64(1254, 1254)
        router = self.image_router(FakeResponse([
            ("response.output_item.done", {"item": {"type": "image_generation_call", "result": image}}),
        ]))

        result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1024"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["data"][0]["actual_size"], "1254x1254")

    def test_codex_image_http_error_empty_body_is_non_empty(self) -> None:
        router = self.image_router(FakeResponse(status=500, text=""))

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
            result = asyncio_run(router.execute_image({"prompt": "draw a gate", "size": "1024x1024"}))

        self.assertEqual(result.status, 503)
        self.assertEqual(result.body["error"]["code"], "codex_transient_unavailable")
        self.assertTrue(result.body["error"]["message"])

    def test_chat_messages_to_responses_input_preserves_tools_and_images(self) -> None:
        output = chat_messages_to_responses_input([
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]},
            {"role": "assistant", "content": "done", "tool_calls": [
                {"id": "call_1", "function": {"name": "x", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        ])
        self.assertEqual(output[0]["role"], "user")
        self.assertEqual(output[1]["type"], "message")
        self.assertEqual(output[2]["type"], "function_call")
        self.assertEqual(output[3]["type"], "function_call_output")

    def test_build_codex_request_body_normalizes_chat_tool_choice(self) -> None:
        request = {
            "messages": [{"role": "user", "content": "use lookup"}],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
            "tool_choice": {"type": "function", "function": {"name": "lookup"}},
        }
        body = build_codex_request_body(request, "gpt-5.4-mini", "cache")

        self.assertEqual(normalize_tool_choice(request["tool_choice"]), {"type": "function", "name": "lookup"})
        self.assertEqual(body["tool_choice"], {"type": "function", "name": "lookup"})

    def test_astra_builders_preserve_model_and_max_reasoning(self) -> None:
        chat = build_codex_request_body(
            {"messages": [{"role": "user", "content": "solve"}], "reasoning_effort": "max"},
            "gpt-6-astra", None,
        )
        native = build_codex_responses_body(
            {"input": "solve", "reasoning": {"effort": "max"}}, "gpt-6-astra", None,
        )
        for body in (chat, native):
            self.assertEqual(body["model"], "gpt-6-astra")
            self.assertEqual(body["reasoning"]["effort"], "max")
            self.assertFalse(body["store"])
        self.assertEqual(native["reasoning"]["context"], "all_turns")

    def test_gpt56_max_is_sent_as_exact_reasoning_effort(self) -> None:
        body = build_codex_request_body(
            {
                "messages": [{"role": "user", "content": "solve"}],
                "reasoning_effort": " MAX ",
            },
            "gpt-5.6-sol",
            None,
        )

        self.assertEqual(body["reasoning"], {"effort": "max"})

    def test_normalize_reset_credits_builds_api_and_db_shapes(self) -> None:
        payload = {
            "available_count": 1,
            "credits": [
                {
                    "id": "credit-1",
                    "status": "available",
                    "type": "weekly",
                    "title": "Reset",
                    "expires_at": "2026-06-30T00:00:00Z",
                }
            ],
        }
        normalized = normalize_reset_credits(payload, "acct", 2)
        self.assertEqual(normalized["api"]["availableCount"], 1)
        self.assertEqual(normalized["api"]["slotIndex"], 2)
        self.assertEqual(normalized["api"]["credits"][0]["status"], "available")
        self.assertEqual(normalized["db"]["account_key"], "acct")
        self.assertEqual(normalized["db"]["credits"][0]["reset_type"], "weekly")

    def test_streaming_rate_limit_error_marks_slot_unavailable(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        slot = CodexSlot(0, auth=CodexAuth("token", account_id="acct"))
        router._mark_streaming_error(slot, {"type": "usage_limit_reached", "resets_in_seconds": 60})
        self.assertGreater(slot.rate_limited_until, time.time())

    def test_codex_event_parser_collects_done_text_without_delta(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        text_parts: list[str] = []
        chunks = router._apply_codex_event(
            "response.output_text.done",
            {"item_id": "msg_1", "text": "final"},
            text_parts,
            [],
            {},
            {},
            {},
            set(),
            set(),
            stream=True,
        )

        self.assertEqual(text_parts, ["final"])
        self.assertEqual(chunks, [{"content": "final"}])

    def test_codex_event_parser_maps_item_id_argument_done_to_call_id(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        tool_calls: dict[str, dict] = {}
        item_to_call: dict[str, str] = {}
        arg_delta_ids: set[str] = set()

        router._apply_codex_event(
            "response.output_item.added",
            {"item": {"id": "item_1", "type": "function_call", "call_id": "call_1", "name": "lookup"}},
            [],
            [],
            tool_calls,
            {},
            item_to_call,
            set(),
            arg_delta_ids,
        )
        router._apply_codex_event(
            "response.function_call_arguments.done",
            {"item_id": "item_1", "name": "lookup", "arguments": "{\"q\":\"x\"}"},
            [],
            [],
            tool_calls,
            {},
            item_to_call,
            set(),
            arg_delta_ids,
        )

        self.assertEqual(item_to_call["item_1"], "call_1")
        self.assertEqual(tool_calls["call_1"]["function"]["name"], "lookup")
        self.assertEqual(tool_calls["call_1"]["function"]["arguments"], "{\"q\":\"x\"}")

    async def collect(self, events: list[tuple[str | None, dict[str, Any]]]):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        return await router._collect_codex_response(FakeResponse(events), "codex/gpt-5.5", CodexSlot(2))

    async def collect_stream(self, events: list[tuple[str | None, dict[str, Any]]]):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        result = CodexCallResult(status=200)
        slot = CodexSlot(0)
        chunks = []
        async for chunk in router._codex_stream_to_chat(FakeResponse(events), "codex/gpt-5.5", result, slot):
            chunks.append(chunk.decode("utf-8"))
        return "".join(chunks), result, slot

    async def collect_stream_response(self, response: FakeResponse, slot: CodexSlot | None = None):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        result = CodexCallResult(status=200)
        selected_slot = slot or CodexSlot(0)
        chunks = []
        async for chunk in router._codex_stream_to_chat(response, "codex/gpt-5.5", result, selected_slot):
            chunks.append(chunk.decode("utf-8"))
        return "".join(chunks), result, selected_slot, response

    async def close_stream_early(self, events: list[tuple[str | None, dict[str, Any]]]):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        result = CodexCallResult(status=200)
        slot = CodexSlot(0)
        response = FakeResponse(events)
        stream = router._codex_stream_to_chat(response, "codex/gpt-5.5", result, slot)
        await stream.__anext__()
        await stream.aclose()
        return result, response

    async def close_stream_after_chunk_buffered_terminal(self):
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        result = CodexCallResult(status=200)
        slot = CodexSlot(0)
        response = FakeResponse(chunks=[b"".join([
            sse_event("response.output_text.delta", {"item_id": "msg_1", "delta": "visible"}),
            sse_event("response.completed", {
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 120000,
                        "input_tokens_details": {"cached_tokens": 90000},
                        "output_tokens": 20,
                    },
                },
            }),
        ])])
        stream = router._codex_stream_to_chat(response, "codex/gpt-5.5", result, slot)
        await stream.__anext__()
        await stream.__anext__()
        await stream.aclose()
        return result, response

    def test_collects_output_item_done_message_without_delta(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_item.done", {
                "item": {
                    "id": "msg_1",
                    "type": "message",
                    "content": [{"type": "output_text", "text": "fallback text"}],
                },
            }),
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 3}}}),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["choices"][0]["message"]["content"], "fallback text")
        self.assertEqual(result.output_tokens, 3)

    def test_eventless_completed_payload_uses_type_and_usage(self) -> None:
        result = asyncio_run(self.collect([
            (None, {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [{
                        "id": "msg_1",
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    }],
                    "usage": {
                        "input_tokens": 100,
                        "input_tokens_details": {"cached_tokens": 80},
                        "output_tokens": 12,
                    },
                },
            }),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.input_tokens, 100)
        self.assertEqual(result.cached_input_tokens, 80)
        self.assertEqual(result.output_tokens, 12)
        self.assertTrue(result.usage_seen)
        self.assertEqual(result.usage_event_type, "response.completed")

    def test_top_level_usage_is_collected_before_terminal_event(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_text.done", {
                "item_id": "msg_1",
                "text": "done",
                "usage": {
                    "input_tokens": 90,
                    "input_tokens_details": {"cached_tokens": 45},
                    "output_tokens": 6,
                },
            }),
            ("response.completed", {"response": {"status": "completed"}}),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.input_tokens, 90)
        self.assertEqual(result.cached_input_tokens, 45)
        self.assertEqual(result.output_tokens, 6)
        self.assertTrue(result.usage_seen)
        self.assertEqual(result.usage_event_type, "response.output_text.done")

    def test_collects_reasoning_text_done(self) -> None:
        result = asyncio_run(self.collect([
            ("response.reasoning_text.done", {"item_id": "rs_1", "content_index": 0, "text": "thinking"}),
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 9, "output_tokens": 2}}}),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["choices"][0]["message"]["reasoning_content"], "thinking")

    def test_raw_reasoning_only_gets_placeholder(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_item.done", {"item": {"id": "rs_raw_1", "type": "reasoning", "encrypted_content": "opaque"}}),
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 9, "output_tokens": 2}}}),
        ]))

        message = result.body["choices"][0]["message"]
        self.assertEqual(result.status, 200)
        self.assertEqual(message["content"], "<think> </think>")
        self.assertEqual(message["reasoning_content"], " ")

    def test_incomplete_maps_to_length_finish_reason(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_text.delta", {"item_id": "msg_1", "delta": "partial"}),
            ("response.incomplete", {"response": {"status": "incomplete", "usage": {"input_tokens": 5, "output_tokens": 7}}}),
        ]))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["choices"][0]["finish_reason"], "length")
        self.assertEqual(result.body["choices"][0]["message"]["content"], "partial")

    def test_incomplete_tool_call_is_retryable_502(self) -> None:
        result = asyncio_run(self.collect([
            ("response.output_item.added", {
                "item": {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup"},
            }),
            ("response.incomplete", {"response": {"status": "incomplete", "usage": {"input_tokens": 5, "output_tokens": 0}}}),
        ]))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_incomplete_response")
        self.assertEqual(result.error, "codex_incomplete_response")

    def test_streaming_buffers_incomplete_tool_call_until_terminal_success(self) -> None:
        body, result, slot = asyncio_run(self.collect_stream([
            ("response.output_item.added", {
                "item": {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup"},
            }),
            ("response.function_call_arguments.delta", {"item_id": "fc_1", "delta": "{\"q\":\"unterminated"}),
            ("response.incomplete", {"response": {"status": "incomplete", "usage": {"input_tokens": 5, "output_tokens": 0}}}),
        ]))

        self.assertNotIn("tool_calls", body)
        self.assertIn("event: error", body)
        self.assertIn("codex_incomplete_response", body)
        self.assertEqual(result.error, "codex_incomplete_response")
        self.assertGreater(slot.transient_until, time.time())

    def test_streaming_emits_completed_tool_call_after_terminal_success(self) -> None:
        body, result, slot = asyncio_run(self.collect_stream([
            ("response.output_item.added", {
                "item": {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup"},
            }),
            ("response.function_call_arguments.delta", {"item_id": "fc_1", "delta": "{\"q\":\"ok\"}"}),
            ("response.completed", {
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 50000,
                        "input_tokens_details": {"cached_tokens": 30000},
                        "output_tokens": 3,
                    },
                },
            }),
        ]))

        self.assertIn("tool_calls", body)
        self.assertIn("\"arguments\":\"{\\\"q\\\":\\\"ok\\\"}\"", body)
        self.assertIn("data: [DONE]", body)
        self.assertIsNone(result.error)
        self.assertEqual(result.input_tokens, 50000)
        self.assertEqual(result.cached_input_tokens, 30000)
        self.assertEqual(result.output_tokens, 3)
        self.assertTrue(result.usage_seen)
        self.assertEqual(result.actual_tool_call_count, 1)
        self.assertEqual(result.actual_tool_names, ["lookup"])
        self.assertEqual(slot.transient_until, 0)

    def test_streaming_drains_usage_after_client_stops_reading(self) -> None:
        result, response = asyncio_run(self.close_stream_early([
            ("response.output_text.delta", {"item_id": "msg_1", "delta": "visible"}),
            ("response.completed", {
                "response": {
                    "status": "completed",
                    "usage": {
                        "input_tokens": 90000,
                        "input_tokens_details": {"cached_tokens": 60000},
                        "output_tokens": 10,
                    },
                },
            }),
        ]))

        self.assertTrue(response.released)
        self.assertEqual(result.input_tokens, 90000)
        self.assertEqual(result.cached_input_tokens, 60000)
        self.assertEqual(result.output_tokens, 10)
        self.assertTrue(result.usage_seen)
        self.assertTrue(result.completed)

    def test_streaming_drains_buffered_terminal_usage_after_client_stops_reading(self) -> None:
        result, response = asyncio_run(self.close_stream_after_chunk_buffered_terminal())

        self.assertTrue(response.released)
        self.assertEqual(result.input_tokens, 120000)
        self.assertEqual(result.cached_input_tokens, 90000)
        self.assertEqual(result.output_tokens, 20)
        self.assertTrue(result.usage_seen)
        self.assertTrue(result.completed)

    def test_assistant_reasoning_is_not_replayed_upstream_by_default(self) -> None:
        output = chat_messages_to_responses_input([
            {"role": "assistant", "content": "visible", "reasoning_content": "volatile reasoning"},
        ])

        self.assertEqual(output, [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "visible"}],
        }])

    def test_assistant_reasoning_replay_can_be_enabled_for_rollback(self) -> None:
        with patch.dict(os.environ, {"SPARTAN_ROUTE_REPLAY_ASSISTANT_REASONING": "1"}):
            output = chat_messages_to_responses_input([
                {"role": "assistant", "content": "visible", "reasoning_content": "reasoning"},
            ])

        self.assertEqual(output[0], {"type": "reasoning", "summary": [{"type": "summary_text", "text": "reasoning"}]})
        self.assertEqual(output[1]["type"], "message")

    def test_empty_successful_codex_response_is_retryable_502(self) -> None:
        result = asyncio_run(self.collect([
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 155000, "output_tokens": 0}}}),
        ]))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_empty_response")
        self.assertEqual(result.error, "codex_empty_response")

    def test_terminal_error_event_maps_to_non_200(self) -> None:
        result = asyncio_run(self.collect([
            ("error", {"error": {"type": "usage_limit_reached", "code": "usage_limit_reached", "message": "limit"}}),
        ]))

        self.assertEqual(result.status, 429)
        self.assertIn("error", result.body)

    def test_terminal_token_invalidated_event_maps_to_auth_failover(self) -> None:
        result = asyncio_run(self.collect([
            ("error", {"error": {
                "type": "invalid_request_error",
                "code": "token_invalidated",
                "message": "token invalidated",
            }}),
        ]))

        self.assertEqual(result.status, 401)
        self.assertEqual(result.body["error"]["code"], "token_invalidated")

    def test_started_stream_quarantines_and_sanitizes_token_invalidated(self) -> None:
        slot = CodexSlot(
            0,
            path="/codex-0/auth.json",
            auth=CodexAuth("token", account_id="acct"),
            auth_fingerprint="rejected-fingerprint",
        )
        body, result, selected_slot, response = asyncio_run(self.collect_stream_response(
            FakeResponse([
                ("error", {"error": {
                    "type": "invalid_request_error",
                    "code": "token_invalidated",
                    "message": "private upstream auth detail",
                }}),
            ]),
            slot,
        ))

        self.assertTrue(response.released)
        self.assertEqual(result.error, "codex_unavailable")
        self.assertIn("codex_unavailable", body)
        self.assertNotIn("token_invalidated", body)
        self.assertNotIn("private upstream auth detail", body)
        self.assertEqual(selected_slot.auth_error_code, "reauth_required")
        self.assertEqual(selected_slot.failed_auth_fingerprint, "rejected-fingerprint")

    def test_non_streaming_read_timeout_is_retryable_transport_error(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        slot = CodexSlot(2)

        result = asyncio_run(router._collect_codex_response(FailingResponse(asyncio.TimeoutError()), "codex/gpt-5.5", slot))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_stream_timeout")
        self.assertEqual(result.error, "codex_stream_timeout")
        self.assertFalse(result.usage_seen)
        self.assertEqual(slot.transient_until, 0)

    def test_non_streaming_read_exception_is_retryable_transport_error(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        router.leases = {}
        slot = CodexSlot(2)

        result = asyncio_run(router._collect_codex_response(FailingResponse(RuntimeError("socket closed")), "codex/gpt-5.5", slot))

        self.assertEqual(result.status, 502)
        self.assertEqual(result.body["error"]["code"], "codex_stream_error")
        self.assertEqual(result.error, "codex_stream_error")
        self.assertEqual(slot.transient_until, 0)

    def test_streaming_read_timeout_emits_error_without_marking_slot(self) -> None:
        body, result, slot, response = asyncio_run(self.collect_stream_response(FailingResponse(asyncio.TimeoutError())))

        self.assertIn("event: error", body)
        self.assertIn("codex_stream_timeout", body)
        self.assertIn("data: [DONE]", body)
        self.assertEqual(result.error, "codex_stream_timeout")
        self.assertEqual(result.output_tokens, 0)
        self.assertFalse(result.usage_seen)
        self.assertTrue(response.released)
        self.assertEqual(slot.transient_until, 0)

    def test_streaming_read_exception_emits_error_without_marking_slot(self) -> None:
        body, result, slot, response = asyncio_run(self.collect_stream_response(FailingResponse(RuntimeError("socket closed"))))

        self.assertIn("event: error", body)
        self.assertIn("codex_stream_error", body)
        self.assertIn("data: [DONE]", body)
        self.assertEqual(result.error, "codex_stream_error")
        self.assertTrue(response.released)
        self.assertEqual(slot.transient_until, 0)

    def test_streaming_terminal_server_error_marks_slot_transient(self) -> None:
        body, result, slot = asyncio_run(self.collect_stream([
            ("error", {"error": {"message": "upstream failed", "type": "server_error", "code": "server_error"}}),
        ]))

        self.assertIn("event: error", body)
        self.assertEqual(result.error, "server_error")
        self.assertGreater(slot.transient_until, time.time())

    def test_execute_chat_strips_codex_prefix_for_selectable_models(self) -> None:
        for model in ("gpt-5.6-luna", "gpt-6-astra"):
            with self.subTest(model=model):
                router = CaptureModelNameRouter()
                result = asyncio_run(router.execute_chat(
                    {"messages": [{"role": "user", "content": "hi"}]},
                    f"codex/{model}", False, "session", None, "request",
                ))
                self.assertEqual(result.status, 200)
                self.assertIsNotNone(router.captured)
                self.assertEqual(router.captured["model_id"], f"codex/{model}")
                self.assertEqual(router.captured["model_name"], model)
                self.assertEqual(router.captured["body"]["model"], model)

    def test_single_chat_call_uses_header_timeout_without_body_total_timeout(self) -> None:
        response = FakeResponse([
            ("response.output_text.done", {"item_id": "msg_1", "text": "done"}),
            ("response.completed", {"response": {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ])
        router = CodexRouter.__new__(CodexRouter)
        router.session = CapturePostSession(response)

        result = asyncio_run(router._single_chat_call(
            {"input": [{"role": "user", "content": "hi"}], "stream": True},
            "codex/gpt-5.5",
            "gpt-5.5",
            False,
            CodexSlot(0),
            CodexAuth("token", account_id="acct"),
        ))

        self.assertEqual(result.status, 200)
        self.assertIsNotNone(router.session.timeout)
        self.assertIsNone(router.session.timeout.total)

    def test_all_transient_slots_return_retry_metadata(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        slot = CodexSlot(0, auth=CodexAuth("token", account_id="acct"))
        slot.transient_until = time.time() + 60
        router.slots = [slot]
        router.leases = {}
        router.telemetry = None

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
            result = asyncio_run(router.execute_chat(
                {"messages": [{"role": "user", "content": "hi"}]},
                "codex/gpt-5.5",
                False,
                "session",
                None,
                "request",
            ))

        self.assertEqual(result.status, 503)
        self.assertEqual(result.body["error"]["code"], "codex_transient_unavailable")
        self.assertGreater(result.body["error"]["retry_after_seconds"], 0)
        self.assertEqual(result.body["error"]["slot_diagnostics"]["transient_5xx"], 1)
        self.assertIn("Retry-After", result.headers)

    def test_all_transient_slots_wait_then_retry(self) -> None:
        router = RecoveringRouter(transient_until=time.time() + 0.02)

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "1000", "SPARTAN_ROUTE_RECOVERY_POLL_MS": "250"}):
            result = asyncio_run(router.execute_chat(
                {"messages": [{"role": "user", "content": "hi"}]},
                "codex/gpt-5.5",
                False,
                "session",
                None,
                "request",
            ))

        self.assertEqual(result.status, 200)
        self.assertEqual(router.calls, 1)

    def test_all_rate_limited_slots_return_429_metadata_when_wait_disabled(self) -> None:
        router = RecoveringRouter(rate_limited_until=time.time() + 60)

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
            result = asyncio_run(router.execute_chat(
                {"messages": [{"role": "user", "content": "hi"}]},
                "codex/gpt-5.5",
                False,
                "session",
                None,
                "request",
            ))

        self.assertEqual(result.status, 429)
        self.assertEqual(result.body["error"]["code"], "codex_429")
        self.assertGreater(result.body["error"]["resets_in_seconds"], 0)
        self.assertEqual(result.body["error"]["slot_diagnostics"]["rate_limited"], 1)
        self.assertIn("Retry-After", result.headers)

    def test_all_rate_limited_slots_do_not_wait_for_reset(self) -> None:
        router = RecoveringRouter(rate_limited_until=time.time() + 0.02)

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "1000", "SPARTAN_ROUTE_RECOVERY_POLL_MS": "250"}):
            result = asyncio_run(router.execute_chat(
                {"messages": [{"role": "user", "content": "hi"}]},
                "codex/gpt-5.5",
                False,
                "session",
                None,
                "request",
            ))

        self.assertEqual(result.status, 429)
        self.assertEqual(result.body["error"]["code"], "codex_429")
        self.assertEqual(router.calls, 0)

    def test_mixed_quota_and_auth_failures_return_pool_429_in_either_order(self) -> None:
        for statuses in ([429, 401], [401, 429]):
            with self.subTest(statuses=statuses):
                router = MixedPoolFailureRouter(statuses)

                with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
                    result = asyncio_run(router.execute_chat(
                        {"messages": [{"role": "user", "content": "hi"}]},
                        "codex/gpt-5.5",
                        False,
                        "session",
                        None,
                        "request",
                    ))

                self.assertEqual(router.calls, [0, 1])
                self.assertEqual(router.recovery_probes, 0)
                self.assertEqual(result.status, 429)
                self.assertEqual(result.body["error"]["code"], "codex_429")
                self.assertEqual(result.body["error"]["slot_diagnostics"]["rate_limited"], 1)
                self.assertEqual(result.body["error"]["slot_diagnostics"]["auth_unavailable"], 1)
                self.assertNotIn("token invalidated", data_json(result.body))

    def test_mixed_quota_and_auth_failures_have_image_parity(self) -> None:
        router = MixedPoolFailureRouter([429, 401])

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
            result = asyncio_run(router.execute_image({"prompt": "draw a gate"}))

        self.assertEqual(router.calls, [0, 1])
        self.assertEqual(router.recovery_probes, 0)
        self.assertEqual(result.status, 429)
        self.assertEqual(result.body["error"]["code"], "codex_429")
        self.assertNotIn("token invalidated", data_json(result.body))

    def test_later_request_probes_usage_and_routes_recovered_slot(self) -> None:
        router = RequestRecoveryRouter()

        result = asyncio_run(router.execute_chat(
            {"messages": [{"role": "user", "content": "continue"}]},
            "codex/gpt-5.5",
            False,
            "session",
            None,
            "request",
        ))

        self.assertEqual(router.recovery_probes, 1)
        self.assertEqual(router.calls, 1)
        self.assertEqual(result.status, 200)

    def test_upstream_401_quarantines_exact_fingerprint_until_login_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auth.json"
            self.write_auth(source, self.jwt(time.time() + 7200), "refresh-old")
            router = self.auth_router(NoCallOAuthSession(), root / "codex-auth")
            slot = CodexSlot(0, path=str(source))
            self.assertIsNotNone(asyncio_run(router.load_auth(slot)))
            rejected_fingerprint = slot.auth_fingerprint
            router.slots = [slot]

            async def reject(_body, _model_id, _model_name, _wants_stream, _slot, _auth):
                return CodexCallResult(
                    status=401,
                    body={
                        "error": {
                            "message": "token invalidated",
                            "type": "invalid_request_error",
                            "code": "token_invalidated",
                        }
                    },
                    error="token_invalidated",
                )

            router._single_chat_call = reject
            with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "0"}):
                result = asyncio_run(router.execute_chat(
                    {"messages": [{"role": "user", "content": "hi"}]},
                    "codex/gpt-5.5",
                    False,
                    "session",
                    None,
                    "request",
                ))

            self.assertEqual(result.status, 503)
            self.assertEqual(result.body["error"]["code"], "codex_unavailable")
            self.assertEqual(slot.auth_error_code, "reauth_required")
            self.assertEqual(slot.failed_auth_fingerprint, rejected_fingerprint)
            self.assertIsNone(asyncio_run(router.load_auth(slot)))

            self.write_auth(source, self.jwt(time.time() + 7200), "fresh-login")
            recovered = asyncio_run(router.load_auth(slot))
            self.assertIsNotNone(recovered)
            self.assertIsNone(slot.failed_auth_fingerprint)

    def test_image_generation_uses_recovery_wait(self) -> None:
        router = RecoveringRouter(transient_until=time.time() + 0.02)

        with patch.dict(os.environ, {"SPARTAN_ROUTE_RECOVERY_WAIT_MS": "1000", "SPARTAN_ROUTE_RECOVERY_POLL_MS": "250"}):
            result = asyncio_run(router.execute_image({"prompt": "draw a gate"}))

        self.assertEqual(result.status, 200)
        self.assertEqual(router.calls, 1)

    def test_codex_empty_response_is_retryable(self) -> None:
        from spartan_route.codex import should_retry

        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_empty_response"}})))
        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_incomplete_response"}})))
        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_stream_timeout"}})))
        self.assertTrue(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_stream_error"}})))
        self.assertFalse(should_retry(502, data_json({"error": {"type": "upstream_error", "code": "codex_image_empty"}})))

    def test_execute_chat_retries_empty_response_and_remembers_successful_slot(self) -> None:
        router = RetryRouter()
        result = asyncio_run(router.execute_chat(
            {"messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "hi"}]},
            "codex/gpt-5.5",
            False,
            "session",
            None,
            "request",
        ))

        self.assertEqual(router.calls, [0, 1])
        self.assertEqual(result.status, 200)
        self.assertEqual(result.slot_index, 1)
        self.assertEqual(len(router.leases), 1)
        self.assertEqual(next(iter(router.leases.values())).slot_index, 1)
        self.assertGreater(router.slots[0].transient_until, time.time())

    def test_execute_chat_stream_close_preserves_usage_from_inner_stream(self) -> None:
        router = StreamingCloseRouter()
        result = asyncio_run(router.execute_chat(
            {"messages": [{"role": "user", "content": "hi"}]},
            "codex/gpt-5.4-mini",
            True,
            "session",
            "cache",
            "request",
        ))

        async def close_after_first_chunk():
            stream = result.stream
            self.assertIsNotNone(stream)
            await stream.__anext__()
            await stream.aclose()

        asyncio_run(close_after_first_chunk())

        self.assertEqual(result.input_tokens, 1000)
        self.assertEqual(result.cached_input_tokens, 512)
        self.assertEqual(result.output_tokens, 7)
        self.assertTrue(result.usage_seen)
        self.assertEqual(router.slots[0].in_flight, 0)
        self.assertEqual(next(iter(router.leases.values())).slot_index, 0)

    def test_disabled_slot_is_not_selected(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            router = CodexRouter.__new__(CodexRouter)
            router.session = None
            router.telemetry = store
            router.slots = [
                CodexSlot(0, auth=CodexAuth("token-a", account_id="acct-a")),
                CodexSlot(1, auth=CodexAuth("token-b", account_id="acct-b")),
            ]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None

            asyncio_run(router.set_slot_routing_enabled(0, False))
            chosen = asyncio_run(router.choose_slot(None, set(), set()))
            self.assertIsNotNone(chosen)
            self.assertEqual(chosen[0].index, 1)
        finally:
            store.close()

    def test_rotated_auth_fingerprint_does_not_bypass_disabled_slot_override(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            router = CodexRouter.__new__(CodexRouter)
            router.telemetry = store
            slot = CodexSlot(
                0,
                path="/codex-a/auth.json",
                auth=CodexAuth("token", account_id="acct-a"),
                auth_fingerprint="old-generation",
            )
            identity = router._slot_identity(slot)
            store.set_slot_routing_enabled(
                slot.index,
                False,
                account_key=identity["accountKey"],
                slot_path_hash=identity["slotPathHash"],
                auth_fingerprint_hash=identity["authFingerprintHash"],
            )
            slot.auth_fingerprint = "rotated-generation"

            status = router.slot_routing_status(slot)

            self.assertFalse(status["routingEnabled"])
            self.assertFalse(status["identityMismatch"])
            self.assertFalse(status["identityUnverified"])
        finally:
            store.close()

    def test_durable_slot_identity_change_ignores_stale_disable(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            router = CodexRouter.__new__(CodexRouter)
            router.telemetry = store
            original = CodexSlot(
                0,
                path="/codex-a/auth.json",
                auth=CodexAuth("token", account_id="acct-a"),
                auth_fingerprint="generation",
            )
            identity = router._slot_identity(original)
            store.set_slot_routing_enabled(
                original.index,
                False,
                account_key=identity["accountKey"],
                slot_path_hash=identity["slotPathHash"],
                auth_fingerprint_hash=identity["authFingerprintHash"],
            )

            for changed in (
                CodexSlot(
                    0,
                    path=original.path,
                    auth=CodexAuth("token", account_id="acct-b"),
                    auth_fingerprint=original.auth_fingerprint,
                ),
                CodexSlot(
                    0,
                    path="/codex-b/auth.json",
                    auth=original.auth,
                    auth_fingerprint=original.auth_fingerprint,
                ),
            ):
                with self.subTest(path=changed.path, account=changed.account_key()):
                    status = router.slot_routing_status(changed)
                    self.assertTrue(status["routingEnabled"])
                    self.assertTrue(status["identityMismatch"])
        finally:
            store.close()

    def test_missing_current_account_keeps_path_bound_disable_fail_closed(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            router = CodexRouter.__new__(CodexRouter)
            router.telemetry = store
            original = CodexSlot(
                0,
                path="/codex-a/auth.json",
                auth=CodexAuth("token", account_id="acct-a"),
            )
            identity = router._slot_identity(original)
            store.set_slot_routing_enabled(
                original.index,
                False,
                account_key=identity["accountKey"],
                slot_path_hash=identity["slotPathHash"],
                auth_fingerprint_hash=identity["authFingerprintHash"],
            )

            status = router.slot_routing_status(CodexSlot(0, path=original.path))

            self.assertFalse(status["routingEnabled"])
            self.assertFalse(status["identityMismatch"])
            self.assertTrue(status["identityUnverified"])
        finally:
            store.close()

    def test_usage_windows_are_classified_by_duration_before_position(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        cases = [
            (
                "weekly-only primary",
                {
                    "primary_window": {
                        "limit_window_seconds": 604800,
                        "used_percent": 41,
                        "reset_at": "2026-07-25T10:00:00Z",
                    },
                },
                None,
                41,
            ),
            (
                "reversed positions",
                {
                    "primary_window": {
                        "limit_window_seconds": 604800,
                        "used_percent": 42,
                        "reset_at": "2026-07-25T10:00:00Z",
                    },
                    "secondary_window": {
                        "limit_window_seconds": 18000,
                        "used_percent": 12,
                        "reset_at": "2026-07-19T15:00:00Z",
                    },
                },
                12,
                42,
            ),
            (
                "legacy positional fallback",
                {
                    "primary_window": {
                        "used_percent": 13,
                        "reset_at": "2026-07-19T15:00:00Z",
                    },
                    "secondary_window": {
                        "used_percent": 43,
                        "reset_at": "2026-07-25T10:00:00Z",
                    },
                },
                13,
                43,
            ),
            (
                "unknown duration ignored",
                {
                    "primary_window": {
                        "limit_window_seconds": 3600,
                        "used_percent": 99,
                        "reset_at": "2026-07-19T11:00:00Z",
                    },
                    "secondary_window": {
                        "limit_window_seconds": 604800,
                        "used_percent": 44,
                        "reset_at": "2026-07-25T10:00:00Z",
                    },
                },
                None,
                44,
            ),
            (
                "nested recognized candidate survives top-level unknown",
                {
                    "primary_window": {
                        "limit_window_seconds": 3600,
                        "used_percent": 99,
                        "reset_at": "2026-07-19T11:00:00Z",
                    },
                    "rate_limit": {
                        "secondary_window": {
                            "limit_window_seconds": 604800,
                            "used_percent": 45,
                            "reset_at": "2026-07-25T10:00:00Z",
                        },
                    },
                },
                None,
                45,
            ),
        ]

        for label, upstream, expected_five, expected_weekly in cases:
            with self.subTest(label=label):
                store = TelemetryStore(":memory:")
                try:
                    auth = CodexAuth("token", account_id="acct")
                    store.upsert_usage_snapshots([
                        {
                            "account_key": auth.account_key,
                            "slot_index": 0,
                            "window": "fiveHour",
                            "used_percent": 20,
                            "reset_at": "2026-07-19T15:00:00Z",
                            "window_minutes": 300,
                        },
                        {
                            "account_key": auth.account_key,
                            "slot_index": 0,
                            "window": "weekly",
                            "used_percent": 20,
                            "reset_at": "2026-07-25T10:00:00Z",
                            "window_minutes": 10080,
                        },
                    ])
                    router = self.usage_router(
                        PayloadUsageSession(upstream),
                        store,
                        [CodexSlot(0, path="/codex-a/auth.json", auth=auth)],
                    )

                    payload = asyncio_run(
                        router.usage_snapshot(force=True, slot_indexes={0})
                    )

                    account = payload["accounts"][0]
                    self.assertEqual(
                        account["fiveHour"]["usedPercent"] if account["fiveHour"] else None,
                        expected_five,
                    )
                    self.assertEqual(account["weekly"]["usedPercent"], expected_weekly)
                    expected_windows = ["weekly"] if expected_five is None else ["fiveHour", "weekly"]
                    self.assertEqual(
                        sorted(row["window"] for row in store.usage_snapshots()),
                        expected_windows,
                    )
                finally:
                    store.close()

    def test_persisted_usage_and_reset_credits_only_include_current_account_identity(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            old_auth = CodexAuth("old-token", account_id="old-account")
            current_auth = CodexAuth("new-token", account_id="current-account")
            for account_key, used in (
                (old_auth.account_key, 91),
                (current_auth.account_key, 21),
            ):
                store.upsert_usage_snapshots([{
                    "account_key": account_key,
                    "slot_index": 0,
                    "window": "weekly",
                    "used_percent": used,
                    "reset_at": "2026-07-25T10:00:00Z",
                    "window_minutes": 10080,
                }])
                store.upsert_reset_credits([{
                    "account_key": account_key,
                    "slot_index": 0,
                    "available_count": 1,
                    "details_available": False,
                    "credits": [],
                }])

            router = self.usage_router(
                FakeUsageSession(),
                store,
                [CodexSlot(0, path="/current/auth.json", auth=current_auth)],
            )

            payload = router._persisted_usage_snapshot()

            self.assertEqual(
                [account["accountKey"] for account in payload["accounts"]],
                [current_auth.account_key],
            )
            self.assertEqual(payload["accounts"][0]["slotIndexes"], [0])
            self.assertEqual(payload["accounts"][0]["slotPaths"], ["/current/auth.json"])
            self.assertEqual(
                payload["accounts"][0]["resetCredits"]["accountKey"],
                current_auth.account_key,
            )
            self.assertNotIn(old_auth.account_key, str(payload))
            self.assertEqual(len(store.usage_snapshots()), 2)
        finally:
            store.close()

    def test_usage_refresh_publishes_discovering_before_auth_work_finishes(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            auth = CodexAuth("token", account_id="acct")
            router = self.usage_router(
                FakeUsageSession(),
                store,
                [CodexSlot(0, path="/codex-a/auth.json", auth=auth)],
            )

            async def exercise() -> None:
                gate = asyncio.Event()

                async def load_auth(_slot: CodexSlot) -> CodexAuth:
                    await gate.wait()
                    return auth

                async def fetch_account(account_key, _auth, slots, _persisted, _clear):
                    return self.live_usage_result(account_key, slots)

                router.load_auth = load_auth
                router._fetch_usage_account = fetch_account
                first = await router.usage_snapshot()
                try:
                    self.assertTrue(first["refreshing"])
                    self.assertEqual(
                        first["refreshProgress"],
                        {
                            "status": "discovering",
                            "completedAccounts": 0,
                            "totalAccounts": None,
                            "pendingSlotIndexes": [0],
                        },
                    )
                finally:
                    gate.set()
                    await asyncio.shield(router._usage_refresh_task)

            asyncio_run(exercise())
        finally:
            store.close()

    def test_usage_auth_discovery_is_concurrent_and_capped_at_four(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            slots = [
                CodexSlot(index, path=f"/codex-{index}/auth.json", auth=CodexAuth(f"token-{index}", account_id=f"acct-{index}"))
                for index in range(6)
            ]
            router = self.usage_router(FakeUsageSession(), store, slots)

            async def exercise() -> None:
                gate = asyncio.Event()
                active = 0
                max_active = 0

                async def load_auth(slot: CodexSlot) -> CodexAuth:
                    nonlocal active, max_active
                    active += 1
                    max_active = max(max_active, active)
                    try:
                        await gate.wait()
                        return slot.auth
                    finally:
                        active -= 1

                async def fetch_account(account_key, _auth, group_slots, _persisted, _clear):
                    return self.live_usage_result(account_key, group_slots)

                router.load_auth = load_auth
                router._fetch_usage_account = fetch_account
                await router.usage_snapshot()
                try:
                    for _ in range(50):
                        if max_active >= 4:
                            break
                        await asyncio.sleep(0)
                    observed = max_active
                finally:
                    gate.set()
                    await asyncio.shield(router._usage_refresh_task)
                self.assertEqual(observed, 4)

            asyncio_run(exercise())
        finally:
            store.close()

    def test_progress_groups_duplicate_accounts_and_publishes_out_of_order(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            auth_a = CodexAuth("token-a", account_id="acct-a")
            auth_b = CodexAuth("token-b", account_id="acct-b")
            slots = [
                CodexSlot(0, path="/codex-0/auth.json", auth=auth_a),
                CodexSlot(1, path="/codex-1/auth.json", auth=auth_a),
                CodexSlot(2, path="/codex-2/auth.json", auth=auth_b),
                CodexSlot(3, path="/codex-3/auth.json"),
            ]
            router = self.usage_router(FakeUsageSession(), store, slots)

            async def exercise() -> None:
                gates = {
                    auth_a.account_key: asyncio.Event(),
                    auth_b.account_key: asyncio.Event(),
                }
                started: set[str] = set()

                async def load_auth(slot: CodexSlot) -> CodexAuth | None:
                    if slot.index == 3:
                        slot.auth_unavailable_reason = "auth unavailable"
                        return None
                    return slot.auth

                async def fetch_account(account_key, _auth, group_slots, _persisted, _clear):
                    started.add(account_key)
                    await gates[account_key].wait()
                    return self.live_usage_result(
                        account_key,
                        group_slots,
                        used_percent=20 if account_key == auth_b.account_key else 40,
                    )

                router.load_auth = load_auth
                router._fetch_usage_account = fetch_account
                await router.usage_snapshot()
                try:
                    for _ in range(50):
                        if len(started) == 2:
                            break
                        await asyncio.sleep(0)
                    self.assertEqual(started, {auth_a.account_key, auth_b.account_key})

                    gates[auth_b.account_key].set()
                    mixed = None
                    for _ in range(100):
                        candidate = await router.usage_snapshot()
                        if candidate.get("refreshProgress", {}).get("completedAccounts") == 1:
                            mixed = candidate
                            break
                        await asyncio.sleep(0)

                    self.assertIsNotNone(mixed)
                    self.assertEqual(mixed["source"], "mixed")
                    self.assertFalse(mixed["cached"])
                    self.assertTrue(mixed["refreshing"])
                    self.assertEqual(
                        mixed["refreshProgress"],
                        {
                            "status": "running",
                            "completedAccounts": 1,
                            "totalAccounts": 2,
                            "pendingSlotIndexes": [0, 1],
                        },
                    )
                    self.assertEqual(
                        [account["accountKey"] for account in mixed["accounts"]],
                        [auth_b.account_key],
                    )
                    self.assertEqual(mixed["accounts"][0]["source"], "live")
                    self.assertFalse(mixed["accounts"][0]["stale"])
                    self.assertEqual(
                        [error["slotIndex"] for error in mixed["slotErrors"]],
                        [3],
                    )

                    gates[auth_a.account_key].set()
                    terminal = await asyncio.shield(router._usage_refresh_task)
                    self.assertFalse(terminal["refreshing"])
                    self.assertEqual(terminal["source"], "live")
                    self.assertEqual(
                        terminal["refreshProgress"],
                        {
                            "status": "complete",
                            "completedAccounts": 2,
                            "totalAccounts": 2,
                            "pendingSlotIndexes": [],
                        },
                    )
                finally:
                    for gate in gates.values():
                        gate.set()
                    task = router._usage_refresh_task
                    if task is not None and not task.done():
                        await asyncio.shield(task)

            asyncio_run(exercise())
        finally:
            store.close()

    def test_zero_resolved_accounts_completes_with_sanitized_slot_errors(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            slots = [
                CodexSlot(0, path="/codex-0/auth.json"),
                CodexSlot(1, path="/codex-1/auth.json"),
            ]
            router = self.usage_router(FakeUsageSession(), store, slots)

            async def exercise() -> None:
                async def load_auth(slot: CodexSlot) -> None:
                    slot.auth_unavailable_reason = f"private auth detail {slot.index}"
                    return None

                router.load_auth = load_auth
                await router.usage_snapshot()
                terminal = await asyncio.shield(router._usage_refresh_task)
                self.assertFalse(terminal["refreshing"])
                self.assertTrue(terminal["cached"])
                self.assertEqual(terminal["source"], "persisted")
                self.assertEqual(
                    terminal["refreshProgress"],
                    {
                        "status": "complete",
                        "completedAccounts": 0,
                        "totalAccounts": 0,
                        "pendingSlotIndexes": [],
                    },
                )
                self.assertEqual(
                    [error["slotIndex"] for error in terminal["slotErrors"]],
                    [0, 1],
                )
                self.assertNotIn("private auth detail", str(terminal))

            asyncio_run(exercise())
        finally:
            store.close()

    def test_cancelling_force_waiter_does_not_cancel_single_flight_refresh(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            auth = CodexAuth("token", account_id="acct")
            router = self.usage_router(
                FakeUsageSession(),
                store,
                [CodexSlot(0, auth=auth)],
            )

            async def exercise() -> None:
                gate = asyncio.Event()
                started = asyncio.Event()
                calls = 0

                async def fetch_account(account_key, _auth, group_slots, _persisted, _clear):
                    nonlocal calls
                    calls += 1
                    started.set()
                    await gate.wait()
                    return self.live_usage_result(account_key, group_slots)

                router._fetch_usage_account = fetch_account
                waiter = asyncio.create_task(router.usage_snapshot(force=True))
                await started.wait()
                shared = router._usage_refresh_task
                waiter.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiter

                self.assertIs(shared, router._usage_refresh_task)
                self.assertFalse(shared.done())
                self.assertEqual(calls, 1)
                gate.set()
                terminal = await router.usage_snapshot(force=True)
                self.assertEqual(calls, 1)
                self.assertEqual(terminal["refreshProgress"]["status"], "complete")
                self.assertEqual(terminal["accounts"][0]["accountKey"], auth.account_key)

            asyncio_run(exercise())
        finally:
            store.close()

    def test_reset_generation_prevents_old_finally_overwriting_new_refresh(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            old_auth = CodexAuth("old-token", account_id="old-account")
            new_auth = CodexAuth("new-token", account_id="new-account")
            router = self.usage_router(
                FakeUsageSession(),
                store,
                [CodexSlot(0, auth=old_auth)],
            )

            async def exercise() -> None:
                old_started = asyncio.Event()
                old_cancelled = asyncio.Event()
                release_old = asyncio.Event()

                async def fetch_account(account_key, _auth, group_slots, _persisted, _clear):
                    if account_key == old_auth.account_key:
                        old_started.set()
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            old_cancelled.set()
                            await release_old.wait()
                    return self.live_usage_result(account_key, group_slots)

                router._fetch_usage_account = fetch_account
                await router.usage_snapshot()
                old_task = router._usage_refresh_task
                await old_started.wait()

                with patch.object(
                    router,
                    "_init_slots",
                    side_effect=lambda: setattr(
                        router,
                        "slots",
                        [CodexSlot(0, auth=new_auth)],
                    ),
                ):
                    router.reset_state()

                await old_cancelled.wait()
                await router.usage_snapshot()
                new_task = router._usage_refresh_task
                self.assertIsNot(new_task, old_task)
                new_terminal = await asyncio.shield(new_task)
                self.assertEqual(
                    [account["accountKey"] for account in new_terminal["accounts"]],
                    [new_auth.account_key],
                )

                release_old.set()
                await old_task
                latest = await router.usage_snapshot()
                self.assertEqual(
                    [account["accountKey"] for account in latest["accounts"]],
                    [new_auth.account_key],
                )
                self.assertEqual(latest["refreshProgress"]["status"], "complete")

            asyncio_run(exercise())
        finally:
            store.close()

    def test_controller_failure_cancels_siblings_before_explicit_retry(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            failing_auth = CodexAuth("failing-token", account_id="failing-account")
            slow_auth = CodexAuth("slow-token", account_id="slow-account")
            router = self.usage_router(
                FakeUsageSession(),
                store,
                [
                    CodexSlot(0, auth=failing_auth),
                    CodexSlot(1, auth=slow_auth),
                ],
            )

            async def exercise() -> None:
                slow_started = asyncio.Event()
                release_orphan = asyncio.Event()
                slow_cancelled = asyncio.Event()
                slow_tasks: list[asyncio.Task[Any]] = []
                failing_calls = 0
                slow_calls = 0
                active_slow = 0
                max_active_slow = 0

                async def fetch_account(account_key, _auth, group_slots, _persisted, _clear):
                    nonlocal failing_calls, slow_calls, active_slow, max_active_slow
                    if account_key == failing_auth.account_key:
                        failing_calls += 1
                        if failing_calls == 1:
                            await slow_started.wait()
                            raise RuntimeError("unexpected account failure")
                        return self.live_usage_result(account_key, group_slots)

                    slow_calls += 1
                    active_slow += 1
                    max_active_slow = max(max_active_slow, active_slow)
                    slow_tasks.append(asyncio.current_task())
                    try:
                        if slow_calls == 1:
                            slow_started.set()
                            try:
                                await release_orphan.wait()
                            except asyncio.CancelledError:
                                slow_cancelled.set()
                                raise
                        return self.live_usage_result(account_key, group_slots)
                    finally:
                        active_slow -= 1

                router._fetch_usage_account = fetch_account
                await router.usage_snapshot()
                failed = await router._usage_refresh_task
                self.assertEqual(failed["refreshProgress"]["status"], "failed")
                with self.subTest("owned sibling drained before failed terminal"):
                    self.assertTrue(slow_cancelled.is_set())
                    self.assertTrue(slow_tasks[0].done())

                retry = router.trigger_usage_refresh()
                self.assertEqual(retry["refreshProgress"]["status"], "discovering")
                completed = await router._usage_refresh_task
                self.assertEqual(completed["refreshProgress"]["status"], "complete")
                with self.subTest("explicit retry does not overlap orphaned work"):
                    self.assertEqual(max_active_slow, 1)
                    self.assertEqual((failing_calls, slow_calls), (2, 2))

                release_orphan.set()
                await asyncio.gather(*slow_tasks, return_exceptions=True)

            asyncio_run(exercise())
        finally:
            store.close()

    def test_failed_terminal_remains_observable_until_explicit_retry(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            auth = CodexAuth("token", account_id="acct")
            router = self.usage_router(
                FakeUsageSession(),
                store,
                [CodexSlot(0, auth=auth)],
            )

            async def exercise() -> None:
                discovery_calls = 0

                async def fail_once(_slot: CodexSlot) -> CodexAuth:
                    nonlocal discovery_calls
                    discovery_calls += 1
                    if discovery_calls == 1:
                        raise RuntimeError("private controller failure")
                    return auth

                router.load_auth = fail_once
                await router.usage_snapshot()
                failed_task = router._usage_refresh_task
                terminal = await failed_task
                self.assertEqual(terminal["refreshProgress"]["status"], "failed")
                self.assertEqual(
                    terminal["refreshError"],
                    {"code": "usage_refresh_failed"},
                )
                self.assertNotIn("private controller failure", str(terminal))

                observed = await router.usage_snapshot()
                self.assertEqual(observed["refreshProgress"]["status"], "failed")
                self.assertIs(router._usage_refresh_task, failed_task)
                self.assertEqual(discovery_calls, 1)

                retry = router.trigger_usage_refresh()
                retry_task = router._usage_refresh_task
                self.assertIsNot(retry_task, failed_task)
                self.assertEqual(retry["refreshProgress"]["status"], "discovering")
                completed = await asyncio.shield(retry_task)
                self.assertEqual(completed["refreshProgress"]["status"], "complete")
                self.assertEqual(discovery_calls, 2)

            asyncio_run(exercise())
        finally:
            store.close()

    def test_usage_snapshot_uses_short_lived_cache(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            session = FakeUsageSession()
            router = CodexRouter.__new__(CodexRouter)
            router.session = session
            router.telemetry = store
            router.slots = [CodexSlot(0, path="/codex-a/auth.json", auth=CodexAuth("token", account_id="acct"))]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None

            first = asyncio_run(router.usage_snapshot(force=True))
            second = asyncio_run(router.usage_snapshot())
            self.assertFalse(first["cached"])
            self.assertEqual(first["accounts"][0]["slotPaths"], ["/codex-a/auth.json"])
            self.assertTrue(second["cached"])
            self.assertEqual(second["accounts"][0]["slotPaths"], ["/codex-a/auth.json"])
            self.assertEqual(len(session.urls), 2)

            forced = asyncio_run(router.usage_snapshot(force=True))
            self.assertFalse(forced["cached"])
            self.assertEqual(len(session.urls), 4)
        finally:
            store.close()

    def test_usage_snapshot_returns_persisted_while_background_refreshes(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            auth = CodexAuth("token", account_id="acct")
            store.upsert_usage_snapshots([{
                "account_key": auth.account_key,
                "slot_index": 0,
                "window": "weekly",
                "used_percent": 15,
                "reset_at": "2026-07-25T10:00:00Z",
                "window_minutes": 10080,
            }])
            session = FakeUsageSession()
            router = CodexRouter.__new__(CodexRouter)
            router.session = session
            router.telemetry = store
            router.slots = [CodexSlot(0, path="/codex-a/auth.json", auth=auth)]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None
            router._usage_refresh_task = None

            async def exercise() -> None:
                first = await router.usage_snapshot()
                self.assertEqual(first["source"], "persisted")
                self.assertTrue(first["refreshing"])
                self.assertEqual(first["accounts"][0]["weekly"]["remainingPercent"], 85)
                await router._usage_refresh_task
                second = await router.usage_snapshot()
                self.assertEqual(second["source"], "memory")
                self.assertFalse(second["refreshing"])
                self.assertEqual(second["accounts"][0]["weekly"]["remainingPercent"], 60)

            asyncio_run(exercise())
            self.assertEqual(len(session.urls), 2)
        finally:
            store.close()

    def test_weekly_only_live_usage_removes_obsolete_five_hour_until_it_returns(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        class WeeklyOnlySession(FakeUsageSession):
            def get(self, url: str, **_kwargs):
                self.urls.append(url)
                if "rate-limit-reset-credits" in url:
                    return FakeJsonContextResponse(200, {"available_count": 1, "credits": []})
                return FakeJsonContextResponse(200, {
                    "secondary_window": {"used_percent": 40, "reset_at": "2026-07-25T10:00:00Z"},
                })

        store = TelemetryStore(":memory:")
        try:
            auth = CodexAuth("token", account_id="acct")
            base = {
                "account_key": auth.account_key,
                "slot_index": 0,
                "used_percent": 20,
                "reset_at": "2026-07-25T10:00:00Z",
            }
            store.upsert_usage_snapshots([
                {**base, "window": "fiveHour", "window_minutes": 300},
                {**base, "window": "weekly", "window_minutes": 10080},
            ])
            router = CodexRouter.__new__(CodexRouter)
            router.session = WeeklyOnlySession()
            router.telemetry = store
            router.slots = [CodexSlot(0, auth=auth)]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None
            router._usage_refresh_task = None

            weekly_only = asyncio_run(router.usage_snapshot(force=True))
            self.assertIsNone(weekly_only["accounts"][0]["fiveHour"])
            self.assertEqual(
                [row["window"] for row in store.usage_snapshots()],
                ["weekly"],
            )

            router.session = FakeUsageSession()
            restored = asyncio_run(router.usage_snapshot(force=True))
            self.assertIsNotNone(restored["accounts"][0]["fiveHour"])
            self.assertEqual(
                sorted(row["window"] for row in store.usage_snapshots()),
                ["fiveHour", "weekly"],
            )
        finally:
            store.close()

    def test_usage_endpoints_refresh_concurrently(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        class DelayedResponse(FakeJsonContextResponse):
            def __init__(self, owner, status, payload):
                super().__init__(status, payload)
                self.owner = owner

            async def __aenter__(self):
                self.owner.active += 1
                self.owner.max_active = max(self.owner.max_active, self.owner.active)
                await asyncio.sleep(0.01)
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                self.owner.active -= 1

        class ConcurrentSession(FakeUsageSession):
            def __init__(self):
                super().__init__()
                self.active = 0
                self.max_active = 0

            def get(self, url: str, **_kwargs):
                self.urls.append(url)
                payload = (
                    {"available_count": 1, "credits": []}
                    if "rate-limit-reset-credits" in url
                    else {"secondary_window": {"used_percent": 40, "reset_at": "2026-07-25T10:00:00Z"}}
                )
                return DelayedResponse(self, 200, payload)

        store = TelemetryStore(":memory:")
        try:
            session = ConcurrentSession()
            router = CodexRouter.__new__(CodexRouter)
            router.session = session
            router.telemetry = store
            router.slots = [
                CodexSlot(0, auth=CodexAuth("token-a", account_id="acct-a")),
                CodexSlot(1, auth=CodexAuth("token-b", account_id="acct-b")),
            ]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None
            router._usage_refresh_task = None

            payload = asyncio_run(router.usage_snapshot(force=True))
            self.assertEqual(len(payload["accounts"]), 2)
            self.assertEqual(len(session.urls), 4)
            self.assertGreaterEqual(session.max_active, 4)
        finally:
            store.close()


    def test_usage_snapshot_keeps_live_payload_when_persistence_fails(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            session = FakeUsageSession()
            router = CodexRouter.__new__(CodexRouter)
            router.session = session
            router.telemetry = store
            router.slots = [
                CodexSlot(
                    0,
                    path="/codex-a/auth.json",
                    auth=CodexAuth("token", account_id="acct"),
                )
            ]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None

            with patch.object(
                store,
                "upsert_usage_snapshots",
                side_effect=RuntimeError("private sqlite path and detail"),
            ):
                payload = asyncio_run(router.usage_snapshot(force=True))

            self.assertEqual(len(payload["accounts"]), 1)
            self.assertTrue(payload["partial"])
            self.assertEqual(
                payload["telemetryErrors"],
                [
                    {
                        "code": "telemetry_persistence_failed",
                        "operation": "usage_snapshots",
                    }
                ],
            )
            self.assertNotIn("private sqlite", str(payload))
            self.assertFalse(store.health_status()["writeHealthy"])

            recovered = asyncio_run(router.usage_snapshot(force=True))
            self.assertFalse(recovered["partial"])
            self.assertNotIn("telemetryErrors", recovered)
            self.assertTrue(store.health_status()["writeHealthy"])
        finally:
            store.close()

    def test_usage_snapshot_sanitizes_reset_credit_storage_failures(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            router = CodexRouter.__new__(CodexRouter)
            router.session = FakeUsageSession()
            router.telemetry = store
            router.slots = [
                CodexSlot(0, auth=CodexAuth("token", account_id="acct"))
            ]
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None

            with (
                patch.object(
                    store,
                    "reset_credit_snapshots",
                    side_effect=RuntimeError("private reset read detail"),
                ),
                patch.object(
                    store,
                    "upsert_reset_credits",
                    side_effect=RuntimeError("private reset write detail"),
                ),
            ):
                payload = asyncio_run(router.usage_snapshot(force=True))

            self.assertEqual(len(payload["accounts"]), 1)
            self.assertTrue(payload["partial"])
            self.assertEqual(
                payload["telemetryErrors"],
                [
                    {
                        "code": "telemetry_read_failed",
                        "operation": "reset_credit_snapshots",
                    },
                    {
                        "code": "telemetry_persistence_failed",
                        "operation": "reset_credit_snapshots",
                    },
                ],
            )
            self.assertNotIn("private reset", str(payload))
            status = store.health_status()
            self.assertFalse(status["readHealthy"])
            self.assertFalse(status["writeHealthy"])
        finally:
            store.close()

    def test_recovery_usage_probe_is_single_flight_and_throttled(self) -> None:
        router = CodexRouter.__new__(CodexRouter)
        slot = CodexSlot(0, auth=CodexAuth("token", account_id="acct"))
        slot.rate_limited_until = time.time() + 3600
        router.slots = [slot]
        router.leases = {}
        router.telemetry = None
        router._usage_cache = None
        router._usage_refresh_task = None
        router._last_recovery_probe_at = 0.0
        gate = asyncio.Event()
        started = asyncio.Event()
        refresh_calls = 0

        async def refresh() -> dict[str, Any]:
            nonlocal refresh_calls
            refresh_calls += 1
            started.set()
            await gate.wait()
            slot.rate_limited_until = 0
            return {}

        def ensure_refresh() -> asyncio.Task[dict[str, Any]]:
            task = router._usage_refresh_task
            if task is None or task.done():
                task = asyncio.create_task(refresh())
                router._usage_refresh_task = task
            return task

        router._ensure_usage_refresh = ensure_refresh

        async def exercise() -> tuple[list[bool], bool]:
            first = asyncio.create_task(router._probe_rate_limit_recovery())
            await started.wait()
            second = asyncio.create_task(router._probe_rate_limit_recovery())
            await asyncio.sleep(0)
            gate.set()
            results = await asyncio.gather(first, second)
            slot.rate_limited_until = time.time() + 3600
            throttled = await router._probe_rate_limit_recovery()
            return results, throttled

        results, throttled = asyncio_run(exercise())

        self.assertEqual(results, [True, True])
        self.assertFalse(throttled)
        self.assertEqual(refresh_calls, 1)

    def test_full_single_flight_refresh_clears_recovered_rate_limit(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            router = self.usage_router(
                FakeUsageSession(),
                store,
                [CodexSlot(0, path="/codex-a/auth.json", auth=CodexAuth("token", account_id="acct"))],
            )
            router.slots[0].rate_limited_until = time.time() + 60

            payload = asyncio_run(router.usage_snapshot(force=True))

            self.assertFalse(payload["partial"])
            self.assertEqual(router.slots[0].rate_limited_until, 0)
        finally:
            store.close()

    def test_usage_refresh_keeps_cooldown_for_full_malformed_and_failed_checks(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        class FailedUsageSession(FakeUsageSession):
            def get(self, url: str, **kwargs):
                if "rate-limit-reset-credits" not in url:
                    raise RuntimeError("usage unavailable")
                return super().get(url, **kwargs)

        cases = (
            (PayloadUsageSession({
                "primary_window": {
                    "limit_window_seconds": 604800,
                    "used_percent": 100,
                    "reset_at": "2026-07-30T10:00:00Z",
                },
            }), "full"),
            (PayloadUsageSession({"unexpected": "shape"}), "malformed"),
            (PayloadUsageSession({"secondary_window": {
                "limit_window_seconds": 604800,
                "reset_at": "2026-07-30T10:00:00Z",
            }}), "missing_percent"),
            (PayloadUsageSession({"secondary_window": {
                "limit_window_seconds": 604800,
                "used_percent": "invalid",
                "reset_at": "2026-07-30T10:00:00Z",
            }}), "invalid_percent"),
            (PayloadUsageSession({"secondary_window": {
                "limit_window_seconds": 604800,
                "used_percent": 0,
                "reset_at": "invalid",
            }}), "invalid_reset_at"),
            (PayloadUsageSession({"secondary_window": {
                "limit_window_seconds": 604800,
                "used_percent": float("nan"),
                "reset_at": "2026-07-30T10:00:00Z",
            }}), "nonfinite_percent"),
            (PayloadUsageSession({"secondary_window": {
                "limit_window_seconds": 604800,
                "used_percent": -1,
                "reset_at": "2026-07-30T10:00:00Z",
            }}), "negative_percent"),
            (PayloadUsageSession({"secondary_window": {
                "limit_window_seconds": 604800,
                "used_percent": 10 ** 1000,
                "reset_at": "2026-07-30T10:00:00Z",
            }}), "overflow_percent"),
            (PayloadUsageSession({
                "primary_window": {
                    "limit_window_seconds": 18000,
                    "used_percent": "invalid",
                    "reset_at": "2026-07-30T10:00:00Z",
                },
                "secondary_window": {
                    "limit_window_seconds": 604800,
                    "used_percent": 0,
                    "reset_at": "2026-07-30T10:00:00Z",
                },
            }), "invalid_five_hour"),
            (PayloadUsageSession({
                "primary_window": {
                    "limit_window_seconds": 3600,
                    "used_percent": 100,
                    "reset_at": "2026-07-30T10:00:00Z",
                },
                "secondary_window": {
                    "limit_window_seconds": 604800,
                    "used_percent": 0,
                    "reset_at": "2026-07-30T10:00:00Z",
                },
            }), "unknown_full_window"),
            (PayloadUsageSession({
                "secondary_window": {
                    "limit_window_seconds": 604800,
                    "used_percent": 0,
                    "reset_at": "2026-07-30T10:00:00Z",
                },
                "rate_limit": {"secondary_window": {
                    "limit_window_seconds": 604800,
                    "used_percent": 100,
                    "reset_at": "2026-07-30T10:00:00Z",
                }},
            }), "conflicting_full_window"),
            (FailedUsageSession(), "failed"),
        )
        for session, label in cases:
            with self.subTest(label=label):
                store = TelemetryStore(":memory:")
                try:
                    router = self.usage_router(
                        session,
                        store,
                        [CodexSlot(0, path="/codex-a/auth.json", auth=CodexAuth("token", account_id="acct"))],
                    )
                    cooldown = time.time() + 60
                    router.slots[0].rate_limited_until = cooldown

                    asyncio_run(router.usage_snapshot(force=True))

                    self.assertEqual(router.slots[0].rate_limited_until, cooldown)
                finally:
                    store.close()

    def test_forced_slot_usage_refresh_clears_recovered_rate_limit(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            session = FakeUsageSession()
            router = CodexRouter.__new__(CodexRouter)
            router.session = session
            router.telemetry = store
            router.slots = [CodexSlot(0, path="/codex-a/auth.json", auth=CodexAuth("token", account_id="acct"))]
            router.slots[0].rate_limited_until = time.time() + 60
            router.leases = {}
            router.refresh_locks = {}
            router._usage_cache = None
            router._availability_event = None
            router._availability_event_loop = None

            payload = asyncio_run(router.usage_snapshot(force=True, slot_indexes={0}))

            self.assertFalse(payload["partial"])
            self.assertEqual(router.slots[0].rate_limited_until, 0)
        finally:
            store.close()

    def test_forced_slot_usage_returns_full_account_projection(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            auth_a = CodexAuth("token-a", account_id="acct-a")
            auth_b = CodexAuth("token-b", account_id="acct-b")
            store.upsert_usage_snapshots([
                {
                    "account_key": auth_a.account_key,
                    "slot_index": 0,
                    "window": "weekly",
                    "used_percent": 10,
                    "reset_at": "2026-07-28T18:00:00Z",
                    "window_minutes": 10080,
                },
                {
                    "account_key": auth_b.account_key,
                    "slot_index": 1,
                    "window": "weekly",
                    "used_percent": 20,
                    "reset_at": "2026-07-28T18:00:00Z",
                    "window_minutes": 10080,
                },
            ], current_account_keys={auth_a.account_key, auth_b.account_key})
            router = self.usage_router(
                FakeUsageSession(),
                store,
                [
                    CodexSlot(0, path="/codex-a/auth.json", auth=auth_a),
                    CodexSlot(1, path="/codex-b/auth.json", auth=auth_b),
                ],
            )

            payload = asyncio_run(router.usage_snapshot(force=True, slot_indexes={0}))

            self.assertEqual(
                [account["accountKey"] for account in payload["accounts"]],
                [auth_a.account_key, auth_b.account_key],
            )
            self.assertEqual(
                [account["source"] for account in payload["accounts"]],
                ["live", "persisted"],
            )
            self.assertEqual(payload["source"], "mixed")
            self.assertEqual(len(router.session.urls), 2)
        finally:
            store.close()

    def test_forced_slot_usage_discovers_non_target_identities_before_projection(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            auth_a = CodexAuth("token-a", account_id="acct-a")
            auth_b = CodexAuth("token-b", account_id="acct-b")
            store.upsert_usage_snapshots([
                {
                    "account_key": auth_a.account_key,
                    "slot_index": 0,
                    "window": "weekly",
                    "used_percent": 10,
                    "reset_at": "2026-07-28T18:00:00Z",
                    "window_minutes": 10080,
                },
                {
                    "account_key": auth_b.account_key,
                    "slot_index": 1,
                    "window": "weekly",
                    "used_percent": 20,
                    "reset_at": "2026-07-28T18:00:00Z",
                    "window_minutes": 10080,
                },
            ], current_account_keys={auth_a.account_key, auth_b.account_key})
            slots = [
                CodexSlot(0, path="/codex-a/auth.json", auth=auth_a),
                CodexSlot(1, path="/codex-b/auth.json"),
            ]
            router = self.usage_router(FakeUsageSession(), store, slots)

            async def discover(slot: CodexSlot) -> CodexAuth | None:
                if slot.index == 1:
                    slot.auth = auth_b
                return slot.auth

            router.load_auth = discover

            payload = asyncio_run(router.usage_snapshot(force=True, slot_indexes={0}))

            self.assertEqual(
                [account["accountKey"] for account in payload["accounts"]],
                [auth_a.account_key, auth_b.account_key],
            )
            self.assertEqual(payload["accounts"][1]["source"], "persisted")
        finally:
            store.close()

    def test_forced_slot_identity_discovery_is_bounded_and_does_not_reload_target(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        store = TelemetryStore(":memory:")
        try:
            slots = [
                CodexSlot(0, path="/codex-0/auth.json", auth=CodexAuth("token-0", account_id="acct-0")),
                *(CodexSlot(index, path=f"/codex-{index}/auth.json") for index in range(1, 7)),
            ]
            router = self.usage_router(FakeUsageSession(), store, slots)
            calls = {slot.index: 0 for slot in slots}
            active = 0
            max_active = 0

            async def discover(slot: CodexSlot) -> CodexAuth | None:
                nonlocal active, max_active
                calls[slot.index] += 1
                if slot.auth:
                    return slot.auth
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)
                slot.auth = CodexAuth(f"token-{slot.index}", account_id=f"acct-{slot.index}")
                active -= 1
                return slot.auth

            router.load_auth = discover

            asyncio_run(router.usage_snapshot(force=True, slot_indexes={0}))

            self.assertEqual(calls[0], 1)
            self.assertEqual(max_active, 4)
            self.assertTrue(all(calls[index] == 1 for index in range(1, 7)))
        finally:
            store.close()

    def test_failed_forced_slot_usage_preserves_other_account_ring(self) -> None:
        from spartan_route.telemetry import TelemetryStore

        class FailingUsageSession(FakeUsageSession):
            def get(self, url: str, **_kwargs):
                self.urls.append(url)
                if "rate-limit-reset-credits" in url:
                    return FakeJsonContextResponse(200, {"available_count": 1, "credits": []})
                return FakeJsonContextResponse(500, {})

        store = TelemetryStore(":memory:")
        try:
            auth_a = CodexAuth("token-a", account_id="acct-a")
            auth_b = CodexAuth("token-b", account_id="acct-b")
            store.upsert_usage_snapshots([
                {
                    "account_key": auth_a.account_key,
                    "slot_index": 0,
                    "window": "weekly",
                    "used_percent": 10,
                    "reset_at": "2026-07-28T18:00:00Z",
                    "window_minutes": 10080,
                },
                {
                    "account_key": auth_b.account_key,
                    "slot_index": 1,
                    "window": "weekly",
                    "used_percent": 20,
                    "reset_at": "2026-07-28T18:00:00Z",
                    "window_minutes": 10080,
                },
            ], current_account_keys={auth_a.account_key, auth_b.account_key})
            router = self.usage_router(
                FailingUsageSession(),
                store,
                [
                    CodexSlot(0, path="/codex-a/auth.json", auth=auth_a),
                    CodexSlot(1, path="/codex-b/auth.json", auth=auth_b),
                ],
            )

            payload = asyncio_run(router.usage_snapshot(force=True, slot_indexes={0}))
            by_key = {account["accountKey"]: account for account in payload["accounts"]}

            self.assertTrue(payload["partial"])
            self.assertEqual(payload["slotErrors"][0]["slotIndex"], 0)
            self.assertIsNone(by_key[auth_a.account_key]["weekly"])
            self.assertEqual(by_key[auth_b.account_key]["weekly"]["usedPercent"], 20)
            self.assertEqual(by_key[auth_b.account_key]["source"], "persisted")
        finally:
            store.close()


class NativeResponsesTests(unittest.TestCase):
    class SequenceSession:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []

        async def post(self, url, **kwargs):
            self.calls.append({"url": url, **kwargs})
            return self.responses.pop(0)

    @staticmethod
    def router(responses):
        router = RetryRouter()
        router.session = NativeResponsesTests.SequenceSession(responses)
        router.telemetry = None
        router.refresh_locks = {}
        router._availability_event = None
        router._availability_event_loop = None
        return router

    def test_cooled_native_request_probes_live_usage_before_routing(self) -> None:
        from spartan_route.native_responses import execute_codex_responses
        from spartan_route.telemetry import TelemetryStore

        terminal = {"type": "response.completed", "response": {
            "id": "resp_recovered", "object": "response", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "recovered"},
            ]}],
        }}

        class RecoverySession(PayloadUsageSession):
            def __init__(self):
                super().__init__({"secondary_window": {
                    "limit_window_seconds": 604800,
                    "used_percent": 0,
                    "reset_at": "2026-09-30T14:22:11Z",
                }})
                self.post_calls = 0

            async def post(self, _url, **_kwargs):
                self.post_calls += 1
                return FakeResponse(events=[("response.completed", terminal)])

        for stream in (False, True):
            with self.subTest(stream=stream):
                store = TelemetryStore(":memory:")
                self.addCleanup(store.close)
                session = RecoverySession()
                router = CodexRouter(session, store)
                slot = CodexSlot(1, auth=CodexAuth("token", account_id="acct"))
                slot.rate_limited_until = time.time() + 3600
                router.slots = [slot]

                async def run():
                    result = await execute_codex_responses(
                        router, {"input": "continue"}, "codex/gpt-5.6-sol",
                        stream, "session", None,
                    )
                    if result.stream:
                        async for _ in result.stream:
                            pass
                    return result

                result = asyncio_run(run())
                self.assertEqual(result.status, 200)
                self.assertEqual(result.slot_index, 1)
                self.assertEqual(slot.rate_limited_until, 0)
                self.assertEqual(session.post_calls, 1)
                self.assertEqual(len(session.urls), 2)

    def test_cooled_native_request_keeps_cooldown_without_valid_live_usage(self) -> None:
        from spartan_route.native_responses import execute_codex_responses
        from spartan_route.telemetry import TelemetryStore

        class RecoverySession(PayloadUsageSession):
            def __init__(self, payload, fail_usage=False):
                super().__init__(payload)
                self.fail_usage = fail_usage
                self.post_calls = 0

            def get(self, url, **kwargs):
                if self.fail_usage and "rate-limit-reset-credits" not in url:
                    self.urls.append(url)
                    raise RuntimeError("usage unavailable")
                return super().get(url, **kwargs)

            async def post(self, _url, **_kwargs):
                self.post_calls += 1
                raise AssertionError("cooled account must not reach upstream model")

        reset_at = "2026-09-30T14:22:11Z"
        cases = (
            ("full", {"secondary_window": {"limit_window_seconds": 604800, "used_percent": 100, "reset_at": reset_at}}, False),
            ("malformed", {"secondary_window": {"limit_window_seconds": 604800, "reset_at": reset_at}}, False),
            ("failed", {}, True),
        )
        for label, payload, fail_usage in cases:
            with self.subTest(label=label):
                store = TelemetryStore(":memory:")
                try:
                    session = RecoverySession(payload, fail_usage)
                    router = CodexRouter(session, store)
                    slot = CodexSlot(1, auth=CodexAuth("token", account_id="acct"))
                    slot.rate_limited_until = time.time() + 3600
                    router.slots = [slot]

                    result = asyncio_run(execute_codex_responses(
                        router, {"input": "continue"}, "codex/gpt-5.6-sol",
                        False, "session", None,
                    ))
                    self.assertEqual(result.status, 429)
                    self.assertEqual(result.body["error"]["code"], "codex_429")
                    self.assertGreater(slot.rate_limited_until, time.time())
                    self.assertEqual(session.post_calls, 0)
                    self.assertTrue(any("/wham/usage" in url for url in session.urls))
                finally:
                    store.close()

    def test_builder_preserves_reasoning_items_and_requests_encrypted_content(self) -> None:
        reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}
        body = build_codex_responses_body(
            {
                "model": "codex/gpt-5.6-sol",
                "input": [reasoning_item, {"role": "user", "content": "continue"}],
                "reasoning": {"effort": "high"},
                "spartan_chain_id": "private-routing-field",
                "spartan_cache_lineage": 2,
                "spartan_cache_lineage_reason": "history_rewrite",
            },
            "gpt-5.6-sol",
            "cache-key",
        )
        self.assertEqual(body["input"][0], reasoning_item)
        self.assertEqual(body["reasoning"], {"effort": "high", "context": "all_turns"})
        self.assertEqual(body["include"], ["reasoning.encrypted_content"])
        self.assertFalse(body["store"])
        self.assertTrue(body["stream"])
        self.assertNotIn("spartan_chain_id", body)
        self.assertNotIn("spartan_cache_lineage", body)
        self.assertNotIn("spartan_cache_lineage_reason", body)

        string_body = build_codex_responses_body(
            {
                "model": "codex/gpt-5.6-sol",
                "input": "hello",
                "reasoning_effort": "max",
                "max_output_tokens": 100,
            },
            "gpt-5.6-sol",
            None,
        )
        self.assertEqual(string_body["input"], [{
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}],
        }])
        self.assertEqual(string_body["reasoning"], {"effort": "max", "context": "all_turns"})
        self.assertNotIn("max_output_tokens", string_body)

    def test_astra_native_transport_preserves_slug_and_max_for_both_stream_modes(self) -> None:
        from spartan_route.native_responses import execute_codex_responses

        terminal = {"type": "response.completed", "response": {
            "id": "resp_astra", "object": "response", "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "done"},
            ]}],
        }}
        for stream in (False, True):
            with self.subTest(stream=stream):
                router = self.router([FakeResponse(events=[("response.completed", terminal)])])

                async def run():
                    result = await execute_codex_responses(
                        router, {"input": "hello", "reasoning": {"effort": "max"}},
                        "codex/gpt-6-astra", stream, "astra-session", None,
                    )
                    if stream:
                        self.assertIsNotNone(result.stream)
                        chunks = [chunk async for chunk in result.stream]
                        self.assertIn(b"response.completed", b"".join(chunks))
                    else:
                        self.assertEqual(result.body["output"][0]["content"][0]["text"], "done")
                    self.assertEqual(result.status, 200)

                asyncio_run(run())
                body = router.session.calls[0]["json"]
                self.assertEqual(body["model"], "gpt-6-astra")
                self.assertEqual(body["reasoning"], {"effort": "max", "context": "all_turns"})
                self.assertFalse(body["store"])

    def test_nonstream_preserves_opaque_output_and_reasoning_usage_is_subset(self) -> None:
        from spartan_route.native_responses import execute_codex_responses

        terminal = {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "object": "response",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "id": "rs_2", "encrypted_content": "ciphertext"},
                    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
                ],
                "usage": {
                    "input_tokens": 100,
                    "input_tokens_details": {"cached_tokens": 60},
                    "output_tokens": 20,
                    "output_tokens_details": {"reasoning_tokens": 50},
                },
            },
        }
        router = self.router([FakeResponse(events=[("response.completed", terminal)])])
        result = asyncio_run(execute_codex_responses(
            router,
            {"model": "codex/gpt-5.6-sol", "input": "hello", "spartan_chain_id": "chain-a"},
            "codex/gpt-5.6-sol",
            False,
            "session-a",
            None,
        ))
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["output"][0]["encrypted_content"], "ciphertext")
        self.assertEqual(result.body["metadata"]["spartan_reasoning_realm"], "codex_subscription")
        self.assertEqual(result.output_tokens, 20)
        self.assertEqual(result.reasoning_tokens, 20)
        self.assertEqual(router.session.calls[0]["json"]["input"][0]["content"][0]["text"], "hello")

    def test_nonstream_reconstructs_output_from_completed_items(self) -> None:
        from spartan_route.native_responses import execute_codex_responses

        item = {"type": "reasoning", "id": "rs_done", "encrypted_content": "opaque"}
        router = self.router([FakeResponse(events=[
            ("response.output_item.done", {"type": "response.output_item.done", "item": item}),
            ("response.completed", {
                "type": "response.completed",
                "response": {
                    "id": "resp_done",
                    "object": "response",
                    "status": "completed",
                    "output": None,
                },
            }),
        ])])
        result = asyncio_run(execute_codex_responses(
            router,
            {"model": "codex/gpt-5.6-sol", "input": "hello"},
            "codex/gpt-5.6-sol",
            False,
            "session-output",
            None,
        ))

        self.assertEqual(result.status, 200)
        self.assertEqual(result.body["output"], [item])

    def test_stream_retries_only_before_first_material_event(self) -> None:
        from spartan_route.native_responses import execute_codex_responses

        created = sse_event("response.created", {"type": "response.created", "response": {"id": "resp_a", "object": "response", "status": "in_progress"}})
        failed = FailingResponse(ConnectionError("precommit"), chunks=[created])
        success = FakeResponse(events=[
            ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "ok"}),
            ("response.completed", {"type": "response.completed", "response": {"id": "resp_b", "object": "response", "status": "completed", "usage": {"input_tokens": 4, "output_tokens": 2}}}),
        ])
        router = self.router([failed, success])
        async def execute_and_collect():
            result = await execute_codex_responses(
                router,
                {"model": "codex/gpt-5.6-sol", "input": "hello", "spartan_chain_id": "chain-b"},
                "codex/gpt-5.6-sol",
                True,
                "session-b",
                None,
            )
            chunks = []
            async for chunk in result.stream:
                chunks.append(chunk)
            return result, b"".join(chunks)

        result, raw = asyncio_run(execute_and_collect())
        payload = raw.decode()
        self.assertEqual(len(router.session.calls), 2)
        self.assertEqual(result.slot_index, 1)
        self.assertIn("response.output_text.delta", payload)
        self.assertIn("spartan_reasoning_realm", payload)
        self.assertTrue(result.completed)

    def test_stream_does_not_retry_after_material_commit(self) -> None:
        from spartan_route.native_responses import execute_codex_responses

        material = sse_event("response.output_text.delta", {"type": "response.output_text.delta", "delta": "partial"})
        response = FailingResponse(ConnectionError("postcommit"), chunks=[material])
        router = self.router([response])
        async def execute_and_collect():
            result = await execute_codex_responses(
                router,
                {"model": "codex/gpt-5.6-sol", "input": "hello"},
                "codex/gpt-5.6-sol",
                True,
                "session-c",
                None,
            )
            chunks = []
            async for chunk in result.stream:
                chunks.append(chunk)
            return result, b"".join(chunks)

        result, raw = asyncio_run(execute_and_collect())
        payload = raw.decode()
        self.assertEqual(len(router.session.calls), 1)
        self.assertIn("partial", payload)
        self.assertIn("codex_stream_error", payload)



def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
