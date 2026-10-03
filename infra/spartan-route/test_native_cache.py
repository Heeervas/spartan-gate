from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from spartan_route.native_cache import NativeCacheDiagnostics


class NativeCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.cache = NativeCacheDiagnostics(clock=lambda: self.now)
        self.body = {
            "model": "gpt-6-astra", "instructions": "private instructions",
            "tools": [{"type": "function", "name": "one"}, {"type": "function", "name": "two"}],
            "reasoning": {"effort": "high"},
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": "stable private prefix"}]},
                {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque-secret"},
                {"type": "function_call", "call_id": "call_1", "name": "one", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "private result"},
                {"role": "user", "content": [{"type": "input_image", "image_url": "private:image"}]},
            ],
        }

    def begin(self, body=None, **kwargs):
        scope = dict(endpoint="subscription/responses", realm="codex_subscription", model="gpt-6-astra",
                     account="account-a", chain="chain-a", cache_key="key-a", slot=0)
        scope.update(kwargs)
        return self.cache.begin(self.body if body is None else body, **scope)

    def complete(self, observation, success=True):
        self.now += 1
        return self.cache.finish(observation, success=success)

    def test_conversation_trace_detects_cross_key_instruction_change(self):
        self.complete(self.begin())
        body = copy.deepcopy(self.body)
        body["instructions"] = "new instructions"
        body["input"].append({"role": "user", "content": "second turn"})
        observation = self.begin(body, cache_key="key-b")
        self.assertEqual(observation.metadata["comparison"], "no_predecessor")
        trace = self.complete(observation)["conversation_trace"]
        self.assertEqual(trace["comparison"], "continued")
        self.assertTrue(trace["cache_key_changed"])
        self.assertEqual(trace["changed_components"], ["instructions", "input"])
        self.assertEqual(self.begin(body, cache_key="key-b").metadata["comparison"], "replay")

    def test_native_changes_cannot_be_misclassified_as_replay(self):
        self.complete(self.begin())
        changes = [
            ("instructions", lambda b: b.update(instructions="changed")),
            ("tools", lambda b: b["tools"].reverse()),
            ("settings", lambda b: b["reasoning"].update(effort="low")),
            ("input", lambda b: b["input"][1].update(encrypted_content="different")),
            ("input", lambda b: b["input"][1].update(id="rs_2")),
            ("input", lambda b: b["input"][2].update(call_id="call_2")),
            ("input", lambda b: b["input"][3].update(output="changed")),
            ("input", lambda b: b["input"][4]["content"][0].update(image_url="other:image")),
        ]
        for component, change in changes:
            with self.subTest(component=component, change=change):
                body = copy.deepcopy(self.body)
                change(body)
                observation = self.begin(body)
                self.assertEqual(observation.metadata["comparison"], "changed")
                self.assertIn(component, observation.metadata["changed_components"])
                self.complete(observation, success=False)

    def test_replay_append_rewrite_and_developer_boundary(self):
        self.complete(self.begin())
        replay = self.begin(copy.deepcopy(self.body))
        self.assertEqual(replay.metadata["comparison"], "replay")
        self.complete(replay)
        append = copy.deepcopy(self.body)
        append["input"].append({"role": "user", "content": "next"})
        pending = self.begin(append)
        self.assertEqual(pending.metadata["comparison"], "append")
        self.complete(pending, False)
        changed = copy.deepcopy(self.body)
        changed["input"][-1] = {"role": "user", "content": "different suffix"}
        pending = self.begin(changed)
        self.assertEqual(pending.metadata["comparison"], "changed")
        self.assertTrue(pending.metadata["developer_boundary"]["reused"])
        self.assertTrue(pending.metadata["developer_boundary"]["changed_suffix"])

    def test_scope_isolation_and_duplicate_account_slots(self):
        self.complete(self.begin())
        for field in ("endpoint", "realm", "model", "account", "chain", "cache_key"):
            observation = self.begin(**{field: "different"})
            self.assertEqual(observation.metadata["comparison"], "no_predecessor", field)
            self.assertEqual(observation.metadata["conversation_trace"]["comparison"],
                             "continued" if field == "cache_key" else "no_predecessor", field)
            self.complete(observation, False)
        duplicate = self.begin(slot=9)
        self.assertEqual(duplicate.metadata["comparison"], "replay")
        self.assertEqual(duplicate.metadata["slot"], 9)

    def test_overlap_failures_and_out_of_order_completion(self):
        older = self.begin()
        newer_body = copy.deepcopy(self.body)
        newer_body["instructions"] = "newest"
        newer = self.begin(newer_body)
        self.assertTrue(newer.metadata["overlap"])
        self.assertEqual(newer.metadata["comparison"], "no_predecessor")
        self.complete(newer)
        older_metadata = self.complete(older)
        self.assertTrue(older_metadata["overlap"])
        probe = self.begin(newer_body)
        self.assertEqual(probe.metadata["comparison"], "replay")
        self.complete(probe, False)
        failed = self.begin(self.body)
        self.complete(failed, False)
        self.assertEqual(self.begin(newer_body).metadata["comparison"], "replay")

    def test_expiry_caps_missing_scope_and_privacy(self):
        self.complete(self.begin())
        self.now += 1801
        self.assertEqual(self.begin().metadata["comparison"], "no_predecessor")
        self.assertEqual(self.begin(chain=None).metadata["comparison"], "unattributed")
        self.cache = NativeCacheDiagnostics(max_payload_bytes=128)
        limited = self.begin()
        self.assertEqual(limited.metadata["comparison"], "unknown")
        self.assertEqual(limited.metadata["reason"], "observation_limit")
        self.complete(limited)
        self.assertEqual(self.begin().metadata["comparison"], "unknown")
        serialized = json.dumps(self.cache.finish(limited, success=True))
        for secret in ("private", "opaque-secret", "key-a", "chain-a"):
            self.assertNotIn(secret, serialized)
        self.assertLessEqual(len(serialized), 8192)

    def test_failed_requests_do_not_replace_conversation_predecessor(self):
        self.complete(self.begin(), False)
        self.assertEqual(self.complete(self.begin())["conversation_trace"]["comparison"], "no_predecessor")
        changed = dict(self.body, instructions="failed instructions")
        self.complete(self.begin(changed, cache_key="failed-key"), False)
        trace = self.complete(self.begin(cache_key="next-key"))["conversation_trace"]
        self.assertEqual(trace["comparison"], "continued")
        self.assertEqual(trace["changed_components"], [])
        self.assertTrue(trace["cache_key_changed"])

    def test_cross_key_overlap_cannot_publish_an_ambiguous_predecessor(self):
        for newest_first in (False, True):
            with self.subTest(newest_first=newest_first):
                self.cache = NativeCacheDiagnostics(clock=lambda: self.now)
                self.complete(self.begin())
                older = self.begin()
                newer = self.begin(dict(self.body, instructions="newest"), cache_key="key-b")
                for observation in ((newer, older) if newest_first else (older, newer)):
                    trace = self.complete(observation)["conversation_trace"]
                    self.assertEqual(trace["comparison"], "unknown")
                    self.assertTrue(trace["overlap"])
                    self.assertIsNone(trace["cache_key_changed"])
                    self.assertEqual(trace["changed_components"], [])
                probe = self.begin(cache_key="key-c")
                self.assertEqual(probe.metadata["conversation_trace"]["comparison"], "no_predecessor")
                self.complete(probe)
                self.assertEqual(self.begin(cache_key="key-d").metadata["conversation_trace"]["comparison"], "continued")

    def test_conversation_expiry_restart_and_eviction_boundaries(self):
        self.complete(self.begin())
        self.now += 1801
        self.assertEqual(self.complete(self.begin(cache_key="key-b"))["conversation_trace"]["comparison"], "no_predecessor")
        self.cache = NativeCacheDiagnostics(clock=lambda: self.now)
        pending = self.begin()
        self.now += 1801
        self.assertEqual(self.complete(pending)["conversation_trace"]["reason"], "scope_expired")
        self.assertEqual(self.complete(self.begin())["conversation_trace"]["comparison"], "no_predecessor")
        self.cache = NativeCacheDiagnostics(clock=lambda: self.now)
        self.assertEqual(self.complete(self.begin())["conversation_trace"]["comparison"], "no_predecessor")
        self.cache = NativeCacheDiagnostics(max_scopes=1, clock=lambda: self.now)
        evicted = self.begin(chain="first")
        self.complete(self.begin(chain="second"))
        self.assertEqual(self.complete(evicted)["conversation_trace"]["reason"], "scope_evicted")
        self.assertEqual(self.begin(chain="first").metadata["conversation_trace"]["comparison"], "no_predecessor")

    def test_conversation_entries_share_byte_budget_without_displacing_cache_scopes(self):
        self.cache = NativeCacheDiagnostics(max_state_bytes=2 * 16384, clock=lambda: self.now)
        self.complete(self.begin())
        self.assertEqual(len(self.cache._entries) + len(self.cache._conversation_entries), 2)
        pending = self.begin(chain="another")
        self.assertEqual(pending.metadata["conversation_trace"]["reason"], "state_limit")
        self.complete(pending)
        self.assertEqual(len(self.cache._entries) + len(self.cache._conversation_entries), 2)
        self.assertEqual(self.begin().metadata["comparison"], "replay")

    def test_conversation_diagnostic_failure_cannot_change_cache_comparison(self):
        self.complete(self.begin())
        with patch.object(self.cache, "_trim_conversations", side_effect=RuntimeError("diagnostics unavailable")):
            observation = self.begin()
        self.assertEqual(observation.metadata["comparison"], "replay")
        self.assertEqual(observation.metadata["conversation_trace"]["comparison"], "unknown")
        self.assertTrue(self.complete(observation)["completed"])

    def test_conversation_state_and_metadata_contain_no_raw_prompt_or_identity(self):
        self.complete(self.begin())
        result = self.complete(self.begin(cache_key="secret-key"))
        serialized = json.dumps(result) + repr(self.cache._entries) + repr(self.cache._conversation_entries)
        for secret in ("private instructions", "opaque-secret", "secret-key", "key-a", "chain-a", "private result", "private:image"):
            self.assertNotIn(secret, serialized)
        self.assertLessEqual(len(json.dumps(result)), 8192)

    def test_key_order_is_structured_equal_but_absent_null_are_distinct(self):
        self.complete(self.begin())
        reordered = dict(reversed(list(self.body.items())))
        self.assertEqual(self.begin(reordered).metadata["comparison"], "replay")
        reordered["text"] = None
        self.assertEqual(self.begin(reordered).metadata["comparison"], "changed")

    def test_diagnostic_only_comparison_id_is_ignored(self):
        self.complete(self.begin())
        body = copy.deepcopy(self.body)
        body["prompt_cache_options"] = {"comparison_response_id": "resp_private"}
        observation = self.begin(body)
        self.assertEqual(observation.metadata["comparison"], "replay")
        self.assertTrue(observation.metadata["provider_comparison_requested"])
        self.assertNotIn("resp_private", json.dumps(observation.metadata))

    def test_bounded_ledger_and_evicted_inflight_cannot_republish(self):
        self.cache = NativeCacheDiagnostics(max_scopes=2, clock=lambda: self.now)
        evicted = self.begin(chain="first")
        self.begin(chain="second")
        self.begin(chain="third")
        self.assertEqual(len(self.cache._entries), 2)
        self.complete(evicted)
        self.assertEqual(self.begin(chain="first").metadata["comparison"], "no_predecessor")
        self.assertEqual(len(self.cache._entries), 2)

    def test_extreme_nesting_and_large_scalars_are_unknown(self):
        nested = "value"
        for _ in range(100):
            nested = {"item": nested}
        body = dict(self.body, input=[nested])
        self.assertEqual(self.begin(body).metadata["comparison"], "unknown")
        self.cache = NativeCacheDiagnostics(max_payload_bytes=4096)
        body = dict(self.body, instructions="\\n" * 100000)
        self.assertEqual(self.begin(body).metadata["comparison"], "unknown")


if __name__ == "__main__":
    unittest.main()
