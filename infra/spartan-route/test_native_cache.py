from __future__ import annotations

import copy
import json
import unittest

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
