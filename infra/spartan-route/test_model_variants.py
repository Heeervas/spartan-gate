from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from test_app import test_config, CaptureChatCodex
from test_codex_catalog import row
from spartan_route.app import STATE_KEY, create_app, route_request, InvalidContextVariant
from spartan_route.config import ModelEntry
from spartan_route.model_variants import VariantPolicy, variant_id
from spartan_route.telemetry import TelemetryStore
from spartan_route.codex import CodexCallResult, CodexAuth, CodexSlot
from spartan_route.codex_catalog import parse_catalog

AUTH = {"Authorization": "Bearer secret"}
PATH = "/api/config/model-variants"
BASE = "codex/gpt-6-astra"


class PolicyTests(unittest.TestCase):
    def test_persistence_absent_empty_nonempty_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "config.db")
            store = TelemetryStore(path)
            self.assertIsNone(VariantPolicy.load(store).rows)
            store.replace_model_variants([])
            self.assertEqual(VariantPolicy.load(store).rows, ())
            store.replace_model_variants([{"model": BASE, "contextWindow": 128001}])
            store.close()
            store = TelemetryStore(path)
            policy = VariantPolicy.load(store)
            self.assertEqual(policy.rows, ((BASE + "-128001context", BASE, 128001),))
            self.assertEqual(variant_id(BASE, 128001), BASE + "-128001context")
            self.assertEqual(variant_id(BASE, 128000), BASE + "-128kcontext")
            self.assertEqual(route_request(test_config(), {"model": BASE + "-128001context"}, variants=policy.snapshot(test_config())[1]).actual_model, BASE)
            store.close()

    def test_corrupt_or_read_failure_disables_defaults(self):
        store = TelemetryStore(":memory:")
        for document in ('{', 'null', '{"version":2,"variants":[],"updatedAt":"now"}',
                         '{"version":1,"variants":[{"model":"x","contextWindow":true}],"updatedAt":"now"}'):
            store._conn.execute("INSERT OR REPLACE INTO model_variant_config VALUES (1, ?)", (document,))
            store._conn.commit()
            policy = VariantPolicy.load(store)
            self.assertEqual(policy.snapshot(test_config())[1], {})
            self.assertTrue(policy.warning)
        with patch.object(store, "model_variant_config", side_effect=RuntimeError("read failed")):
            app = create_app(test_config(), store)
            policy = app[STATE_KEY].model_variants
            self.assertEqual(policy.rows, ())
            self.assertEqual(policy.snapshot(test_config())[1], {})
            self.assertTrue(policy.warning)
        store.close()

    def test_unknown_malformed_removed_ids_fail_closed_in_all_modes(self):
        for enabled, dry_run in ((True, False), (False, False), (True, True)):
            config = replace(test_config(), enabled=enabled, dry_run=dry_run)
            for model in (BASE + "-128kcontext", "openai/unknown-12.5kcontext", "openai/unknown-badcontext"):
                with self.subTest(enabled=enabled, dry_run=dry_run, model=model):
                    with self.assertRaises(InvalidContextVariant):
                        route_request(config, {"model": model}, variants={})
            real = BASE + "-128kcontext"
            config.models[real] = ModelEntry(real, "codex")
            self.assertEqual(route_request(config, {"model": real}, variants={}).actual_model, real)

    def test_v1_migrates_ids_v2_persists_ids_and_tombstones(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "variants.db")
            store = TelemetryStore(path)
            legacy = {"version": 1, "variants": [{"model": BASE, "contextWindow": 128000}], "updatedAt": "legacy"}
            store._conn.execute("INSERT INTO model_variant_config VALUES (1, ?)", (json.dumps(legacy),))
            store._conn.commit()
            policy = VariantPolicy.load(store)
            self.assertEqual(policy.rows, ((BASE + "-128kcontext", BASE, 128000),))
            store.replace_model_variants([{"id": "friendly-name", "model": BASE, "contextWindow": 412000}], retired_ids=[BASE + "-128kcontext"])
            store.close()
            store = TelemetryStore(path)
            policy = VariantPolicy.load(store)
            self.assertEqual(store.model_variant_config()["version"], 2)
            self.assertEqual(policy.rows, (("friendly-name", BASE, 412000),))
            self.assertEqual(policy.retired_ids, frozenset({BASE + "-128kcontext"}))
            self.assertEqual(route_request(test_config(), {"model": "friendly-name"}, variants=policy.snapshot(test_config())[1]).actual_model, BASE)
            with self.assertRaises(InvalidContextVariant):
                route_request(test_config(), {"model": BASE + "-128kcontext"}, variants=policy.snapshot(test_config())[1])
            store.close()

    def test_long_v1_generated_alias_survives_migration_and_v2_roundtrip(self):
        model = "openai/" + "a" * 249
        store = TelemetryStore(":memory:")
        legacy = {"version": 1, "variants": [{"model": model, "contextWindow": 282000}], "updatedAt": "legacy"}
        store._conn.execute("INSERT INTO model_variant_config VALUES (1, ?)", (json.dumps(legacy),))
        store._conn.commit()
        policy = VariantPolicy.load(store)
        self.assertIsNone(policy.warning)
        config = test_config()
        config.models[model] = ModelEntry(model, "openai", max_context=500000)
        rows = policy.snapshot(config)[0]
        alias = rows[0]["id"]
        self.assertGreater(len(alias), 256)
        self.assertTrue(rows[0]["available"])
        self.assertEqual(policy.validate(config, {"variants": [{"id": alias, "model": model, "contextWindow": 412000}]}), ((alias, model, 412000),))
        store.replace_model_variants([{"id": alias, "model": model, "contextWindow": 412000}])
        self.assertIsNone(VariantPolicy.load(store).warning)
        store.close()

    def test_corrupt_v2_and_read_failure_deny_unknown_but_preserve_real_models(self):
        store = TelemetryStore(":memory:")
        documents = [
            {"version": 2, "variants": [{"id": "friendly", "model": BASE, "contextWindow": 0}], "retiredIds": [], "updatedAt": "now"},
            {"version": 2, "variants": [], "retiredIds": ["friendly", "friendly"], "updatedAt": "now"},
            {"version": 2, "variants": [], "retiredIds": [], "updatedAt": "now", "extra": True},
            {"version": 2, "variants": [{"model": BASE, "contextWindow": 128000}], "retiredIds": [], "updatedAt": "now"},
        ]
        for document in documents:
            store._conn.execute("INSERT OR REPLACE INTO model_variant_config VALUES (1, ?)", (json.dumps(document),))
            store._conn.commit()
            policy = VariantPolicy.load(store)
            self.assertTrue(policy.warning)
            active = policy.snapshot(test_config())[1]
            self.assertEqual(active, {})
            with self.assertRaises(InvalidContextVariant):
                route_request(test_config(), {"model": "friendly"}, variants=active)
            self.assertEqual(route_request(test_config(), {"model": BASE}, variants=active).actual_model, BASE)
            self.assertTrue(route_request(test_config(), {"model": "auto"}, variants=active).actual_model)
        with patch.object(store, "model_variant_config", side_effect=RuntimeError("read failed")):
            active = VariantPolicy.load(store).snapshot(test_config())[1]
            with self.assertRaises(InvalidContextVariant):
                route_request(test_config(), {"model": "unrecoverable-alias"}, variants=active)
        healthy = VariantPolicy(rows=()).snapshot(test_config())[1]
        self.assertEqual(route_request(test_config(), {"model": "ordinary-unknown"}, variants=healthy).actual_model, "ordinary-unknown")
        store.close()


class VariantApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        signing = patch.dict('os.environ', SPARTAN_REASONING_BINDING_SECRET='41' * 32)
        signing.start()
        self.addCleanup(signing.stop)
        self.store = TelemetryStore(":memory:")
        self.app = create_app(test_config(), self.store)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.state = self.app[STATE_KEY]
        await self.state.codex_catalog.stop()

    async def asyncTearDown(self):
        await self.client.close()

    async def get(self):
        response = await self.client.get(PATH, headers=AUTH)
        self.assertEqual(response.status, 200)
        document = await response.json()
        self.assertEqual(response.headers["ETag"], document["etag"])
        return document

    async def save(self, rows, etag=None):
        etag = etag or (await self.get())["etag"]
        return await self.client.put(PATH, headers={**AUTH, "If-Match": etag}, json={"variants": rows})

    async def test_stable_name_keeps_hermes_alias_when_context_changes(self):
        alias = "codex/gpt-6.1-sol-282kcontext"
        model = "codex/gpt-6.1-sol"
        # A discovered model needs a validated offering even in alias-only tests.
        catalog = self.state.codex_catalog
        catalog.router.slots = [CodexSlot(0, token="synthetic", auth=CodexAuth("synthetic", account_id="synthetic"))]
        catalog._fetch = AsyncMock(return_value=parse_catalog({"models": [row("gpt-6.1-sol")]}))
        await catalog.refresh()
        result = await self.save([{"id": alias, "model": model, "contextWindow": 412000}])
        self.assertEqual(result.status, 200)
        detail = await self.client.get("/v1/models/" + alias)
        self.assertEqual(detail.status, 200)
        self.assertEqual((await detail.json())["max_context"], 412000)
        fake = CaptureChatCodex()
        self.state.codex = fake
        result = await self.client.post("/v1/chat/completions", headers=AUTH, json={"model": alias, "messages": []})
        self.assertEqual(result.status, 200)
        self.assertEqual(fake.calls[-1]["model_id"], model)

    async def test_arbitrary_stable_ids_context_and_base_edits_and_duplicate_pairs(self):
        rows = [{"id": "friendly", "model": BASE, "contextWindow": 128000},
                {"id": "codex/friendly", "model": BASE, "contextWindow": 128000}]
        self.assertEqual((await self.save(rows)).status, 200)
        for context, base in ((412000, BASE), (100000, "openai/gpt-test")):
            rows[0].update(model=base, contextWindow=context)
            self.assertEqual((await self.save(rows)).status, 200)
            saved = next(row for row in (await self.get())["variants"] if row["id"] == "friendly")
            self.assertEqual((saved["model"], saved["contextWindow"]), (base, context))
            route = route_request(self.state.config, {"model": "friendly"}, variants=self.state.model_variants.snapshot(self.state.config)[1])
            self.assertEqual(route.actual_model, base)
        self.assertEqual((await self.save([rows[0], {**rows[0], "contextWindow": 1}])).status, 400)
        self.assertEqual((await self.save([{"model": BASE, "contextWindow": 128000}])).status, 200)
        self.assertEqual((await self.get())["variants"][0]["id"], BASE + "-128kcontext")

    async def test_stable_id_validation_and_special_namespace_reservations(self):
        row = {"model": BASE, "contextWindow": 128000}
        for alias in ("", "x" * 273, "friendly name", "é", "a?b", "a#b", "a\n", "/a", "a/", "a//b", ".", "..", "a/../b", "a/./b",
                      "auto", "clawroute/auto", "custom-1/friendly", "spartan-route/friendly", "gpt-image-2", "gpt-realtime-2", BASE, None, 12):
            with self.subTest(alias=alias):
                self.assertEqual((await self.save([{**row, "id": alias}])).status, 400)
        self.state.variant_reserved_ids = frozenset({"customvoice"})
        self.assertEqual((await self.save([{**row, "id": "customvoice"}])).status, 400)
        self.assertEqual((await self.save([{**row, "id": "valid_name.v2:chat/a-b"}])).status, 200)

    async def test_retired_and_inactive_custom_ids_deny_chat_responses_and_restart(self):
        row = {"id": "friendly", "model": BASE, "contextWindow": 412000}
        self.assertEqual((await self.save([row])).status, 200)
        self.state.config.models[BASE] = replace(self.state.config.models[BASE], max_context=300000)
        self.assertFalse((await self.get())["variants"][0]["available"])
        self.assertEqual((await self.save([row, {"id": "other", "model": BASE, "contextWindow": 100000}])).status, 200)
        self.assertEqual((await self.save([{**row, "contextWindow": 413000}])).status, 400)
        self.assertEqual((await self.save([{**row, "id": "renamed"}])).status, 400)
        for endpoint, payload in (("/v1/chat/completions", {"messages": []}), ("/v1/responses", {"input": "hello"})):
            self.assertEqual((await self.client.post(endpoint, headers=AUTH, json={"model": "friendly", **payload})).status, 404)
        self.assertEqual((await self.save([])).status, 200)
        self.assertIn("friendly", self.state.model_variants.retired_ids)
        self.state.model_variants = VariantPolicy.load(self.store)
        for endpoint, payload in (("/v1/chat/completions", {"messages": []}), ("/v1/responses", {"input": "hello"})):
            self.assertEqual((await self.client.post(endpoint, headers=AUTH, json={"model": "friendly", **payload})).status, 404)
        self.assertEqual((await self.save([{**row, "contextWindow": 100000}])).status, 200)
        self.assertNotIn("friendly", self.state.model_variants.retired_ids)
        self.assertEqual((await self.client.get("/v1/models/friendly")).status, 200)
        # Real model IDs retain priority even when a retired name later appears.
        self.assertEqual((await self.save([])).status, 200)
        self.state.config.models["friendly"] = ModelEntry("friendly", "openai")
        self.assertEqual(route_request(self.state.config, {"model": "friendly"}, variants=self.state.model_variants.snapshot(self.state.config)[1]).actual_model, "friendly")

    async def test_arbitrary_alias_responses_keeps_original_body_and_routes_saved_base(self):
        self.assertEqual((await self.save([{"id": "friendly", "model": BASE, "contextWindow": 412000}])).status, 200)
        with patch("spartan_route.app.execute_codex_responses", return_value=CodexCallResult(status=200, body={"status": "completed", "output": []})) as execute:
            result = await self.client.post("/v1/responses", headers=AUTH, json={"model": "friendly", "input": "hello"})
            self.assertEqual(result.status, 200)
            self.assertEqual(execute.call_args.args[2], BASE)
            self.assertEqual(execute.call_args.args[1]["model"], "friendly")

    async def test_auth_preconditions_defaults_and_authoritative_empty(self):
        self.assertEqual((await self.client.get(PATH)).status, 401)
        self.assertEqual((await self.client.put(PATH, json={"variants": []})).status, 401)
        doc = await self.get()
        self.assertFalse(doc["configured"])
        self.assertIn(BASE + "-282kcontext", [v["id"] for v in doc["variants"]])
        missing = await self.client.put(PATH, headers=AUTH, json={"variants": []})
        self.assertEqual(missing.status, 428)
        saved = await self.save([])
        self.assertEqual(saved.status, 200)
        current = await saved.json()
        self.assertTrue(current["configured"])
        self.assertEqual(current["variants"], [])
        self.assertIsNotNone(current["updatedAt"])
        self.assertEqual((await self.save([], doc["etag"])).status, 412)
        self.state.config.models["openai/new"] = ModelEntry("openai/new", "openai", max_context=500000)
        self.assertEqual((await self.get())["variants"], [])
        self.assertNotIn(BASE + "-282kcontext", [r["id"] for r in (await (await self.client.get("/v1/models")).json())["data"]])
        self.assertEqual((await self.client.get("/v1/models/" + BASE + "-282kcontext")).status, 404)

    async def test_strict_validation_bounded_payload_and_collisions(self):
        self.state.config.models.update({
            "openai/gpt-image-test": ModelEntry("openai/gpt-image-test", "openai", max_context=500000),
            "openai/gpt-realtime-test": ModelEntry("openai/gpt-realtime-test", "openai", max_context=500000),
            "openai/disabled": ModelEntry("openai/disabled", "openai", enabled=False),
            "openai/no-tools": ModelEntry("openai/no-tools", "openai", tool_capable=False),
            BASE + "-42context": ModelEntry(BASE + "-42context", "codex"),
        })
        valid = {"model": BASE, "contextWindow": 128000}
        for rows in (None, {}, [valid, valid], [{**valid, "unknown": 1}],
                     *[[{**valid, "contextWindow": value}] for value in (True, 1.0, "1", 0, -1, 2_000_001, 872001)],
                     *[[{**valid, "model": value}] for value in ("missing", "auto", "clawroute/auto", "anthropic/test", "openai/gpt-image-test", "openai/gpt-realtime-test", "openai/disabled", "openai/no-tools", BASE + "-42context")],
                     [{"model": BASE, "contextWindow": 42}]):
            with self.subTest(rows=rows):
                self.assertEqual((await self.save(rows)).status, 400)
        etag = (await self.get())["etag"]
        extra = await self.client.put(PATH, headers={**AUTH, "If-Match": etag}, json={"variants": [], "extra": 1})
        self.assertEqual(extra.status, 400)
        oversized = await self.client.put(PATH, headers={**AUTH, "If-Match": etag}, data=b" " * 16385)
        self.assertEqual(oversized.status, 413)
        self.assertEqual((await self.save([{"model": BASE, "contextWindow": 1}, {"model": BASE, "contextWindow": 872000}])).status, 200)

    async def test_stale_catalog_concurrent_saves_and_write_failure(self):
        doc = await self.get()
        self.state.config.models[BASE] = replace(self.state.config.models[BASE], max_context=500000)
        self.assertEqual((await self.save([], doc["etag"])).status, 412)
        etag = (await self.get())["etag"]
        responses = await asyncio.gather(self.save([], etag), self.save([{"model": BASE, "contextWindow": 100000}], etag))
        self.assertEqual(sorted(r.status for r in responses), [200, 412])
        before = await self.get()
        with patch.object(self.store, "replace_model_variants", side_effect=RuntimeError("write failed")):
            self.assertEqual((await self.save([{ "model": BASE, "contextWindow": 200000}])).status, 503)
        self.assertEqual(await self.get(), before)
        self.assertEqual(VariantPolicy.load(self.store).rows, self.state.model_variants.rows)

    async def test_queued_save_revalidates_catalog_under_publication_lock(self):
        etag = (await self.get())["etag"]
        await self.state.routing_policy_lock.acquire()
        try:
            task = asyncio.create_task(self.save([{"model": BASE, "contextWindow": 400000}], etag))
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            self.state.config.models[BASE] = replace(self.state.config.models[BASE], max_context=300000)
        finally:
            self.state.routing_policy_lock.release()
        self.assertEqual((await task).status, 412)
        self.assertIsNone(self.store.model_variant_config())

    async def test_sqlite_abort_rolls_back_previous_config_and_publication(self):
        self.assertEqual((await self.save([{ "model": BASE, "contextWindow": 128000}])).status, 200)
        before = await self.get()
        persisted = self.store.model_variant_config()
        self.store._conn.execute("CREATE TRIGGER reject_variants BEFORE INSERT ON model_variant_config BEGIN SELECT RAISE(ABORT, 'test write failure'); END")
        self.store._conn.commit()
        result = await self.save([])
        self.assertEqual(result.status, 503)
        self.assertEqual((await result.json())["error"]["code"], "config_persistence_failed")
        self.assertEqual(await self.get(), before)
        self.assertEqual(self.store.model_variant_config(), persisted)

    async def test_custom_variants_route_chat_responses_and_deletion_denies(self):
        rows = [{"model": BASE, "contextWindow": 128000}, {"model": BASE, "contextWindow": 128001}]
        self.assertEqual((await self.save(rows)).status, 200)
        fake = CaptureChatCodex()
        self.state.codex = fake
        for alias in (BASE + "-128kcontext", BASE + "-128001context"):
            detail = await self.client.get("/v1/models/" + alias)
            self.assertEqual(detail.status, 200)
            self.assertEqual((await detail.json())["max_context"], 128000 if "128k" in alias else 128001)
            result = await self.client.post("/v1/chat/completions", headers=AUTH, json={"model": alias, "messages": []})
            self.assertEqual(result.status, 200)
            self.assertEqual(fake.calls[-1]["model_id"], BASE)
            with patch("spartan_route.app.execute_codex_responses", return_value=CodexCallResult(status=200, body={"status": "completed", "output": []})) as execute:
                result = await self.client.post("/v1/responses", headers=AUTH, json={"model": alias, "input": "hello"})
                self.assertEqual(result.status, 200)
                self.assertEqual(execute.call_args.args[2], BASE)
                self.assertEqual(execute.call_args.args[1]["model"], alias)
        self.assertEqual((await self.save([])).status, 200)
        for endpoint, payload in (("/v1/chat/completions", {"messages": []}), ("/v1/responses", {"input": "hello"})):
            result = await self.client.post(endpoint, headers=AUTH, json={"model": BASE + "-128kcontext", **payload})
            self.assertEqual(result.status, 404)
        self.assertEqual(len(fake.calls), 2)
        # Alias IDs never become candidates for auto routing.
        routing = await self.client.get("/api/config/routing", headers=AUTH)
        doc = await routing.json()
        tiers = {t["id"]: BASE + "-128kcontext" for t in doc["editableTiers"]}
        self.assertEqual((await self.client.put("/api/config/routing", headers={**AUTH, "If-Match": doc["etag"]}, json={"tiers": tiers})).status, 400)
        self.assertEqual(self.state.config.models[BASE].max_context, 872000)

    async def test_catalog_publication_shrink_remove_restore_and_preservation(self):
        catalog = self.state.codex_catalog
        self.state.codex.slots = [CodexSlot(0, token="token", auth=CodexAuth("token", account_id="account"))]
        model = "codex/gpt-discovered"
        row = {"slug": "gpt-discovered", "visibility": "list", "supported_in_api": True,
               "max_context_window": 800000, "input_modalities": ["text"], "minimal_client_version": "0.153.0", "supported_reasoning_levels": [{"effort": "max"}]}
        async def publish(value):
            catalog._fetch = AsyncMock(return_value=parse_catalog({"models": [value]}) if value else {})
            await catalog.refresh()
        await publish(row)
        chosen = {"model": model, "contextWindow": 400000}
        self.assertEqual((await self.save([chosen])).status, 200)
        before = await self.get()
        await publish({**row, "max_context_window": 300000})
        self.assertEqual((await self.save([chosen], before["etag"])).status, 412)
        doc = await self.get()
        self.assertFalse(doc["variants"][0]["available"])
        self.assertEqual(doc["variants"][0]["unavailableReason"], "context_exceeds_maximum")
        alias = model + "-400kcontext"
        self.assertEqual((await self.client.get("/v1/models/" + alias)).status, 404)
        for endpoint, payload in (("/v1/chat/completions", {"messages": []}), ("/v1/responses", {"input": "hello"})):
            self.assertEqual((await self.client.post(endpoint, headers=AUTH, json={"model": alias, **payload})).status, 404)
        self.assertEqual((await self.save([chosen, {"model": BASE, "contextWindow": 128000}])).status, 200)
        await publish(None)
        doc = await self.get()
        self.assertEqual(next(v for v in doc["variants"] if v["model"] == model)["unavailableReason"], "model_missing")
        await publish(row)
        self.assertEqual((await self.client.get("/v1/models/" + alias)).status, 200)
        self.assertEqual(len((await self.get())["variants"]), 2)
        self.assertEqual((await self.save([])).status, 200)
