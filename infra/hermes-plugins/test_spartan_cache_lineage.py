from __future__ import annotations

import importlib.util
import contextvars
import os
import pathlib
import sys
import threading
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


PLUGIN_PATH = pathlib.Path(__file__).parent / "spartan-cache-lineage" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("spartan_cache_lineage", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    module._reset_for_tests()
    return module


class FakeContext:
    def __init__(self) -> None:
        self.middleware: list[tuple[str, object]] = []
        self.hooks: list[tuple[str, object]] = []

    def register_middleware(self, kind: str, callback: object) -> None:
        self.middleware.append((kind, callback))

    def register_hook(self, kind: str, callback: object) -> None:
        self.hooks.append((kind, callback))


class SpartanCacheLineagePluginTests(unittest.TestCase):
    def test_registers_llm_request_middleware(self) -> None:
        plugin = load_plugin()
        ctx = FakeContext()

        plugin.register(ctx)

        self.assertEqual(len(ctx.middleware), 1)
        self.assertEqual(ctx.middleware[0][0], "llm_request")
        self.assertEqual(ctx.hooks[0][0], "post_api_request")

    def test_injects_cache_key_lineage_and_reasoning_for_spartan_route(self) -> None:
        plugin = load_plugin()
        request = {
            "model": "custom-1/clawroute/auto",
            "messages": [{"role": "user", "content": "hello"}],
            "extra_body": {"reasoning": {"effort": "minimal"}},
        }

        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://spartan-route:18790/v1"}, clear=False):
            result = plugin._llm_request_middleware(
                request=request,
                session_id="session-123",
                base_url="http://spartan-route:18790/v1",
                api_mode="chat_completions",
            )

        self.assertIsInstance(result, dict)
        updated = result["request"]
        self.assertEqual(updated["extra_body"]["prompt_cache_key"], "hermes:session-123")
        self.assertEqual(updated["extra_body"]["spartan_cache_lineage"], 0)
        self.assertEqual(updated["extra_body"]["spartan_cache_lineage_reason"], "baseline")
        self.assertEqual(updated["reasoning_effort"], "low")

    def test_tracks_retries_prefixes_rewrites_and_tool_changes(self) -> None:
        plugin = load_plugin()

        def lineage(messages, tools=None):
            with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://spartan-route:18790/v1"}, clear=False):
                result = plugin._llm_request_middleware(
                    request={
                        "messages": messages,
                        "tools": tools or [{"type": "function", "function": {"name": "read_file"}}],
                    },
                    session_id="session",
                    base_url="http://spartan-route:18790/v1",
                    api_mode="chat_completions",
                )
            return (
                result["request"]["extra_body"]["spartan_cache_lineage"],
                result["request"]["extra_body"]["spartan_cache_lineage_reason"],
            )

        base = [{"role": "system", "content": "stable"}, {"role": "user", "content": "first"}]

        self.assertEqual(lineage(base), (0, "baseline"))
        self.assertEqual(lineage([*base, {"role": "assistant", "content": "answer"}]), (0, "prefix"))
        self.assertEqual(lineage([*base, {"role": "assistant", "content": "answer"}]), (0, "retry"))
        self.assertEqual(lineage([{"role": "system", "content": "changed"}, {"role": "user", "content": "first"}]), (1, "history_rewrite"))
        self.assertEqual(
            lineage(
                [{"role": "system", "content": "changed"}, {"role": "user", "content": "first"}],
                tools=[{"type": "function", "function": {"name": "write_file"}}],
            ),
            (2, "tool_schema_changed"),
        )

    def test_preserves_existing_prompt_cache_key_and_does_not_store_raw_key(self) -> None:
        plugin = load_plugin()
        request = {
            "messages": [{"role": "user", "content": "secret prompt"}],
            "extra_body": {"prompt_cache_key": "raw-secret-cache-key"},
        }

        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://spartan-route:18790/v1"}, clear=False):
            result = plugin._llm_request_middleware(
                request=request,
                session_id="session",
                base_url="http://spartan-route:18790/v1",
                api_mode="chat_completions",
            )

        self.assertEqual(result["request"]["extra_body"]["prompt_cache_key"], "raw-secret-cache-key")
        serialized_state = repr(plugin._state)
        self.assertNotIn("raw-secret-cache-key", serialized_state)
        self.assertNotIn("secret prompt", serialized_state)

    def test_responses_lineage_tracks_instructions_input_tools_and_transport(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "09" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }

        def lineage(instructions, input_items, tools=None):
            with patch.dict(os.environ, env, clear=False):
                result = plugin._llm_request_middleware(
                    request={
                        "instructions": instructions,
                        "input": input_items,
                        "tools": tools or [{"type": "function", "name": "read_file"}],
                    },
                    session_id="responses-session",
                    base_url=env["OPENAI_BASE_URL"],
                    api_mode="codex_responses",
                )
            return (
                result["request"]["extra_body"]["spartan_cache_lineage"],
                result["request"]["extra_body"]["spartan_cache_lineage_reason"],
            )

        base = [{"role": "user", "content": "first secret prompt"}]
        extended = [*base, {"role": "assistant", "content": "answer"}]
        self.assertEqual(lineage("stable secret instructions", base), (0, "baseline"))
        self.assertEqual(lineage("stable secret instructions", base), (0, "retry"))
        self.assertEqual(lineage("stable secret instructions", extended), (0, "prefix"))
        self.assertEqual(lineage("changed secret instructions", extended), (1, "history_rewrite"))
        self.assertEqual(lineage("changed secret instructions", base), (2, "history_rewrite"))
        self.assertEqual(
            lineage(
                "changed secret instructions",
                base,
                tools=[{"type": "function", "name": "write_file"}],
            ),
            (3, "tool_schema_changed"),
        )

        with patch.dict(os.environ, env, clear=False):
            chat = plugin._llm_request_middleware(
                request={"messages": [{"role": "user", "content": "chat"}]},
                session_id="responses-session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="chat_completions",
            )
        self.assertEqual(chat["request"]["extra_body"]["spartan_cache_lineage"], 0)
        self.assertEqual(chat["request"]["extra_body"]["spartan_cache_lineage_reason"], "baseline")
        def scalar_lineage(value):
            with patch.dict(os.environ, env, clear=False):
                result = plugin._llm_request_middleware(
                    request={"instructions": "scalar", "input": value},
                    session_id="scalar-responses-session",
                    base_url=env["OPENAI_BASE_URL"],
                    api_mode="codex_responses",
                )
            return (
                result["request"]["extra_body"]["spartan_cache_lineage"],
                result["request"]["extra_body"]["spartan_cache_lineage_reason"],
            )

        self.assertEqual(scalar_lineage("first scalar secret"), (0, "baseline"))
        self.assertEqual(scalar_lineage("first scalar secret"), (0, "retry"))
        self.assertEqual(scalar_lineage("changed scalar secret"), (1, "history_rewrite"))

        serialized_state = repr(plugin._state)
        self.assertNotIn("secret instructions", serialized_state)
        self.assertNotIn("secret prompt", serialized_state)
        self.assertNotIn("scalar secret", serialized_state)

    def test_codex_responses_normalizes_legacy_reasoning_effort_before_adapter(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "10" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value="/opt/data"):
            result = plugin._llm_request_middleware(
                request={"input": [], "reasoning_effort": "HIGH"},
                session_id="session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )

        request = result["request"]
        self.assertNotIn("reasoning_effort", request)
        self.assertEqual(request["reasoning"], {"effort": "high"})

    def test_same_session_id_uses_distinct_chain_per_active_profile(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "11" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }

        def chain_for(home: str) -> str:
            with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value=home):
                result = plugin._llm_request_middleware(
                    request={"input": [{"role": "user", "content": "hello"}]},
                    session_id="shared-session",
                    base_url=env["OPENAI_BASE_URL"],
                    api_mode="codex_responses",
                )
            return result["request"]["extra_body"]["spartan_chain_id"]

        self.assertNotEqual(chain_for("/opt/data/profiles/work"), chain_for("/opt/data/profiles/reviewer"))

    def test_profile_switch_drops_foreign_reasoning_and_assistant_item_id(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "22" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value="/opt/data/profiles/work"):
            foreign = plugin._reasoning_binding("same-session")
        reasoning = {
            "type": "reasoning",
            "encrypted_content": "opaque",
            "_spartan_chain_id": foreign["spartan_chain_id"],
            "_spartan_reasoning_realm": foreign["spartan_expected_realm"],
            "_spartan_binding_mac": foreign["spartan_binding_mac"],
        }
        with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value="/opt/data/profiles/reviewer"):
            result = plugin._llm_request_middleware(
                request={"input": [reasoning, {"type": "message", "id": "msg_old", "role": "assistant", "content": []}]},
                session_id="same-session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )
        self.assertEqual(result["reason"], "reasoning_chain_reset")
        self.assertEqual(result["request"]["input"], [{"type": "message", "role": "assistant", "content": []}])

    def test_post_hook_stamps_and_next_request_strips_private_metadata(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "33" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }
        class ReadOnlyResponse:
            def __init__(self) -> None:
                self.provider_data = {
                    "codex_reasoning_items": [{"type": "reasoning", "encrypted_content": "cipher"}],
                }

            @property
            def codex_reasoning_items(self):
                return self.provider_data["codex_reasoning_items"]

        message = ReadOnlyResponse()
        with patch.dict(os.environ, env, clear=False), patch.object(plugin, "_active_hermes_home", return_value="/opt/data/profiles/work"):
            plugin._post_api_request(
                assistant_message=message,
                session_id="session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )
            self.assertIn("_spartan_chain_id", message.codex_reasoning_items[0])
            result = plugin._llm_request_middleware(
                request={"input": message.codex_reasoning_items},
                session_id="session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )
        replay = result["request"]["input"][0]
        self.assertEqual(replay, {"type": "reasoning", "encrypted_content": "cipher"})
        self.assertEqual(result["request"]["extra_body"]["spartan_expected_realm"], "codex_subscription")

    def test_missing_secret_injects_fail_closed_binding(self) -> None:
        plugin = load_plugin()
        env = {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "",
            "SPARTAN_REASONING_REALM": "codex_subscription",
        }
        with patch.dict(os.environ, env, clear=False):
            result = plugin._llm_request_middleware(
                request={"input": [{"role": "user", "content": "hello"}]},
                session_id="session",
                base_url=env["OPENAI_BASE_URL"],
                api_mode="codex_responses",
            )
        self.assertEqual(result["request"]["extra_body"]["spartan_chain_id"], "invalid")

    def test_ignores_direct_provider_requests(self) -> None:
        plugin = load_plugin()

        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://spartan-route:18790/v1"}, clear=False):
            result = plugin._llm_request_middleware(
                request={"messages": [{"role": "user", "content": "hello"}]},
                session_id="session",
                base_url="https://api.openai.com/v1",
                api_mode="chat_completions",
            )

        self.assertIsNone(result)


class BackgroundReviewLineageTests(unittest.TestCase):
    def setUp(self):
        self.plugin = load_plugin()
        self.origin = contextvars.ContextVar("test_write_origin", default="foreground")
        provenance = ModuleType("tools.skill_provenance")
        provenance.get_current_write_origin = self.origin.get
        self.modules = patch.dict(sys.modules, {
            "tools": ModuleType("tools"), "tools.skill_provenance": provenance,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.env = patch.dict(os.environ, {
            "OPENAI_BASE_URL": "http://spartan-route:18790/v1",
            "SPARTAN_REASONING_BINDING_SECRET": "44" * 32,
            "SPARTAN_REASONING_REALM": "codex_subscription",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.home = patch.object(self.plugin, "_active_hermes_home", return_value="/synthetic/profile")
        self.home.start()
        self.addCleanup(self.home.stop)
        self.base = [{"role": "user", "content": "synthetic shared history"}]

    def send(self, items, *, origin="foreground", task_id="task", turn_id="turn",
             instructions="stable", tools=None, model="codex/synthetic", **context):
        token = self.origin.set(origin)
        try:
            return self.plugin._llm_request_middleware(
                request={"model": model, "instructions": instructions, "input": items,
                         "tools": tools if tools is not None else [{"type": "function", "name": "read_file"}],
                         "prompt_cache_key": "synthetic-shared-key"},
                session_id="session", base_url="http://spartan-route:18790/v1",
                api_mode="codex_responses", task_id=task_id, turn_id=turn_id, **context,
            )["request"]
        finally:
            self.origin.reset(token)

    def lineage(self, request):
        body = request["extra_body"]
        return body["spartan_cache_lineage"], body["spartan_cache_lineage_reason"]

    def test_review_end_does_not_rewrite_foreground_history(self):
        first = self.send(self.base)
        review = self.send([*self.base, {"role": "user", "content": "temporary review"}],
                           origin="background_review", task_id="review-task", turn_id="review-turn")
        continued = self.send([*self.base, {"role": "user", "content": "next live question"}],
                              task_id="next-task", turn_id="next-turn")
        self.assertEqual(self.lineage(first)[0], self.lineage(review)[0])
        self.assertEqual(self.lineage(continued), (0, "prefix"))

    def test_review_rewrite_cannot_advance_nonzero_foreground_lineage(self):
        self.send(self.base)
        compressed = [{"role": "user", "content": "actual compressed history"}]
        self.assertEqual(self.lineage(self.send(compressed)), (1, "history_rewrite"))
        self.assertEqual(self.lineage(self.send(compressed, origin="background_review"))[0], 1)
        self.send([{ "role": "user", "content": "review-only rewrite"}], origin="background_review")
        self.assertEqual(self.lineage(self.send([*compressed, {"role": "user", "content": "live"}])),
                         (1, "prefix"))

    def test_recognized_review_without_ids_never_updates_foreground(self):
        self.send(self.base)
        snapshot = repr(self.plugin._state)
        review = self.send([*self.base, {"role": "user", "content": "review"}],
                           origin="background_review", task_id="", turn_id="")
        self.assertEqual(self.lineage(review)[0], 0)
        self.assertEqual(repr(self.plugin._state), snapshot)
        self.assertEqual(self.lineage(self.send([*self.base, {"role": "user", "content": "live"}])),
                         (0, "prefix"))

    def test_interleaved_tool_loops_and_retries_keep_shared_starting_key(self):
        first = self.send(self.base)
        review_items = [*self.base, {"role": "user", "content": "review"}]
        review = self.send(review_items, origin="background_review", task_id="review")
        live_items = [*self.base, {"role": "assistant", "content": "live answer"}]
        self.assertEqual(self.lineage(self.send(live_items)), (0, "prefix"))
        self.assertEqual(self.lineage(self.send(review_items, origin="background_review", task_id="review")),
                         (0, "retry"))
        review_items.append({"type": "function_call_output", "call_id": "synthetic", "output": "review result"})
        self.assertEqual(self.lineage(self.send(review_items, origin="background_review", task_id="review")),
                         (0, "prefix"))
        self.assertEqual(self.lineage(self.send([*live_items, {"role": "user", "content": "next"}])),
                         (0, "prefix"))
        for field in ("spartan_chain_id", "spartan_expected_realm", "spartan_binding_mac", "prompt_cache_key"):
            self.assertEqual(first["extra_body"][field], review["extra_body"][field])
        self.assertEqual(review["input"][:len(self.base)], first["input"])
        self.assertEqual(review["instructions"], first["instructions"])
        self.assertEqual(review["tools"], first["tools"])

    def test_aborted_review_request_cannot_change_foreground_baseline(self):
        self.send(self.base)
        foreground_key = next(iter(self.plugin._state))
        before = repr(self.plugin._state[foreground_key])
        # Provider failure/cancellation occurs after middleware, with no response hook.
        def cancelled_provider(request):
            raise RuntimeError("synthetic review cancellation")
        with self.assertRaisesRegex(RuntimeError, "cancellation"):
            cancelled_provider(self.send([*self.base, {"role": "user", "content": "aborted review"}],
                                         origin="background_review", task_id="cancelled"))
        self.assertEqual(repr(self.plugin._state[foreground_key]), before)
        self.assertEqual(self.lineage(self.send([*self.base, {"role": "user", "content": "live"}])), (0, "prefix"))

    def test_late_review_inherits_matching_nonzero_snapshot_and_pins_it(self):
        self.send(self.base)
        snapshot = [{"role": "user", "content": "compressed once"}]
        self.assertEqual(self.lineage(self.send(snapshot))[0], 1)
        newer = [{"role": "user", "content": "compressed twice"}]
        self.assertEqual(self.lineage(self.send(newer))[0], 2)
        review_items = [*snapshot, {"role": "user", "content": "late review"}]
        self.assertEqual(self.lineage(self.send(review_items, origin="background_review")), (1, "baseline"))
        self.assertEqual(self.lineage(self.send(self.base))[0], 3)
        self.assertEqual(self.lineage(self.send([*review_items, {"role": "assistant", "content": "review answer"}],
                                               origin="background_review")), (1, "prefix"))
        self.assertEqual(self.lineage(self.send([*self.base, {"role": "user", "content": "live"}])), (3, "prefix"))

    def test_sibling_reviews_with_same_turn_id_have_independent_histories(self):
        self.send(self.base)
        a = [*self.base, {"role": "user", "content": "review A"}]
        b = [*self.base, {"role": "user", "content": "review B"}]
        self.send(a, origin="background_review", task_id="A", turn_id="parent-turn")
        self.assertEqual(self.lineage(self.send(b, origin="background_review", task_id="B", turn_id="parent-turn"))[0], 0)
        self.assertEqual(self.lineage(self.send(a, origin="background_review", task_id="A", turn_id="parent-turn")), (0, "retry"))
        self.assertEqual(self.lineage(self.send([{ "role": "user", "content": "A rewrite"}],
                                               origin="background_review", task_id="A", turn_id="parent-turn"))[0], 1)
        self.assertEqual(self.lineage(self.send(b, origin="background_review", task_id="B", turn_id="parent-turn")), (0, "retry"))

    def test_review_seed_requires_exact_tools_model_and_sanitized_history(self):
        self.send(self.base)
        current = [{"role": "user", "content": "replacement"}]
        self.send(current)  # Foreground counter is now 1; old snapshot counter is 0.
        for i, overrides in enumerate(({"model": "codex/other"}, {"tools": []}, {"instructions": "other"})):
            review = self.send(self.base, origin="background_review", task_id=str(i), **overrides)
            self.assertEqual(self.lineage(review)[0], 1)  # Incompatible old snapshot cannot supply 0.
        contaminated = [*self.base, {"type": "reasoning", "encrypted_content": "foreign",
                                    "_spartan_chain_id": "foreign"}]
        review = self.send(contaminated, origin="background_review", task_id="sanitized")
        self.assertEqual(review["input"], self.base)
        self.assertEqual(self.lineage(review)[0], 0)

    def test_real_rewrites_instructions_and_tools_still_increment_each_lane(self):
        self.send(self.base)
        self.send(self.base, origin="background_review")
        for origin in ("foreground", "background_review"):
            changed = self.send(self.base, origin=origin, instructions="updated instructions")
            self.assertEqual(self.lineage(changed), (1, "history_rewrite"))
            tools = [{"type": "function", "name": "write_file"}]
            changed = self.send(self.base, origin=origin, instructions="updated instructions", tools=tools)
            self.assertEqual(self.lineage(changed), (2, "tool_schema_changed"))
            changed = self.send([], origin=origin, instructions="updated instructions", tools=tools)
            self.assertEqual(self.lineage(changed), (3, "history_rewrite"))

    def test_origin_is_captured_once_and_never_guessed_from_text(self):
        provenance = sys.modules["tools.skill_provenance"]
        with patch.object(provenance, "get_current_write_origin", side_effect=["background_review", "foreground"]) as get:
            self.send([{ "role": "user", "content": "arbitrary review text"}])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len(self.plugin._state), 1)
        # Without an actual marker, review-like words are ordinary foreground content.
        self.send(self.base)
        self.assertEqual(self.lineage(self.send([{ "role": "user", "content": "background_review"}]))[0], 1)

    def test_missing_origin_api_retains_legacy_tracking(self):
        provenance = sys.modules["tools.skill_provenance"]
        with patch.object(provenance, "get_current_write_origin", side_effect=ImportError("older Hermes")):
            self.send(self.base)
            self.send([*self.base, {"role": "user", "content": "review"}], origin="background_review")
            self.assertEqual(self.lineage(self.send(self.base)), (1, "history_rewrite"))

    def test_review_without_foreground_and_single_identifier_fallbacks(self):
        for task, turn in (("task-only", ""), ("", "turn-only"), ("", "")):
            request = self.send(self.base, origin="background_review", task_id=task, turn_id=turn)
            self.assertEqual(self.lineage(request)[0], 0)
        self.assertEqual(len(self.plugin._state), 2)
        self.assertEqual(self.lineage(self.send(self.base)), (0, "baseline"))

    def test_foreground_scope_isolation_survives_review_branching(self):
        self.send(self.base)
        self.send([{ "role": "user", "content": "rewrite"}])
        for home, realm, session, key in (("/synthetic/other", "codex_subscription", "session", "synthetic-shared-key"),
                                         ("/synthetic/profile", "openai_api", "session", "synthetic-shared-key"),
                                         ("/synthetic/profile", "codex_subscription", "other-session", "synthetic-shared-key"),
                                         ("/synthetic/profile", "codex_subscription", "session", "other-key")):
            with patch.object(self.plugin, "_active_hermes_home", return_value=home), patch.dict(os.environ, {"SPARTAN_REASONING_REALM": realm}):
                result = self.plugin._lineage_for_request(
                    session_id=session, logical_cache_key=key, request={"model": "codex/synthetic", "instructions": "stable", "input": self.base},
                    api_mode="codex_responses", review_origin=True, review_id="isolated")
            self.assertEqual(result, (0, "baseline"))

    def test_concurrent_contexts_cannot_overwrite_foreground(self):
        self.send(self.base)
        barrier = threading.Barrier(2)
        errors = []
        def run(origin, label):
            try:
                history = list(self.base)
                for i in range(20):
                    barrier.wait(timeout=5)
                    history.append({"role": "user", "content": f"{label}-{i}"})
                    value = self.lineage(self.send(history, origin=origin, task_id=label))
                    self.assertEqual(value[0], 0)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=run, args=("foreground", "live")),
                   threading.Thread(target=run, args=("background_review", "review"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_frontiers_deduplicate_and_expire_without_claiming_old_cache(self):
        self.send(self.base)
        for _ in range(20):
            self.send(self.base)
        foreground = next(iter(self.plugin._state.values()))
        self.assertEqual(len(foreground["frontiers"]), 1)
        for i in range(12):
            self.send([{ "role": "user", "content": f"rewrite-{i}"}])
        foreground = next(iter(self.plugin._state.values()))
        self.assertEqual(len(foreground["frontiers"]), 8)
        self.assertEqual(self.lineage(self.send(self.base, origin="background_review"))[0], 12)

    def test_all_lanes_share_key_and_actual_memory_limits(self):
        with patch.dict(os.environ, {"SPARTAN_CACHE_LINEAGE_MAX_KEYS": "3"}):
            self.send(self.base)
            for i in range(10):
                self.send(self.base, origin="background_review", task_id=f"review-{i}")
                self.assertLessEqual(len(self.plugin._state), 3)
        with patch.object(self.plugin, "MAX_STATE_BYTES", 12_000):
            for i in range(12):
                history = [{"role": "user", "content": f"{i}-{j}"} for j in range(20)]
                self.send(history, origin="background_review", task_id=f"bounded-{i}")
                actual = sys.getsizeof(self.plugin._state) + sum(
                    self.plugin._retained_size(k) + self.plugin._retained_size(v)
                    for k, v in self.plugin._state.items())
                self.assertLessEqual(actual, 12_000)
                self.assertLessEqual(self.plugin._state_bytes + sys.getsizeof(self.plugin._state), 12_000)
        state = repr(self.plugin._state)
        for raw in ("synthetic shared history", "synthetic-shared-key", "review-", "bounded-", "/synthetic/profile"):
            self.assertNotIn(raw, state)

    def test_oversized_history_keeps_only_counter_and_hash_metadata(self):
        self.send(self.base)
        huge = [{"role": "user", "content": f"synthetic-{i}"} for i in range(300)]
        with patch.object(self.plugin, "MAX_STATE_BYTES", 6_000):
            self.assertEqual(self.lineage(self.send(huge))[0], 1)
            foreground = next(iter(self.plugin._state.values()))
            self.assertIsNone(foreground["history"])
            self.assertEqual(foreground["frontiers"], ())
            self.assertEqual(self.lineage(self.send(huge))[0], 1)
            self.assertLessEqual(self.plugin._state_bytes + sys.getsizeof(self.plugin._state), 6_000)

    def test_chat_does_not_read_review_origin(self):
        provenance = sys.modules["tools.skill_provenance"]
        with patch.object(provenance, "get_current_write_origin", side_effect=AssertionError("must not read origin")) as get:
            for text in ("first", "rewritten"):
                result = self.plugin._llm_request_middleware(
                    request={"messages": [{"role": "user", "content": text}]}, session_id="chat",
                    base_url="http://spartan-route:18790/v1", api_mode="chat_completions")
            self.assertEqual(self.lineage(result["request"]), (1, "history_rewrite"))
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
