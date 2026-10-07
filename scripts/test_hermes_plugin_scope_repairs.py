import contextvars
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("hermes_plugin_scope_repairs", Path(__file__).with_name("hermes_plugin_scope_repairs.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

TALK_SOURCE = '''def start(claimed, transcript_fd, lease):
    worker = threading.Thread(
        target=_finish_claim,
        args=(claimed, transcript_fd, lease, _default_run_agent),
        daemon=True,
        name="talk-memory-handoff",
    )
    worker.start()
    return worker
'''
NODE_SOURCE = '''import os
import yaml
def roots():
    return os.getenv("NODE_SEARCH_ALLOWED_ROOTS", ""), os.getenv("NODE_SEARCH_ROOT", "")
def policy():
    return os.getenv("NODE_SEARCH_ALLOWED_ROOTS", ""), os.getenv("NODE_SEARCH_ROOT", ""), os.getenv("NODE_SEARCH_CACHE", "")
'''


class ScopeRepairTests(unittest.TestCase):
    def test_two_overlapping_transcript_workers_keep_their_own_profiles(self):
        profile = contextvars.ContextVar("profile", default="unscoped")
        ready = threading.Barrier(3)
        release = threading.Event()
        observed = {}

        def finish(claimed, *unused):
            ready.wait(timeout=3)
            release.wait(timeout=3)
            observed[claimed] = profile.get()

        namespace = {"threading": threading, "_finish_claim": finish, "_default_run_agent": object()}
        source = MODULE.patch_talk_transcript(TALK_SOURCE)
        self.assertEqual(MODULE.patch_talk_transcript(source), source)
        exec(source, namespace)
        workers = []
        try:
            for owner in ["alpha", "beta"]:
                token = profile.set(owner)
                try:
                    workers.append(namespace["start"](owner, None, None))
                finally:
                    profile.reset(token)
            ready.wait(timeout=3)
            self.assertEqual(profile.get(), "unscoped")
        finally:
            release.set()
            for worker in workers:
                worker.join(timeout=3)
        self.assertEqual(observed, {"alpha": "alpha", "beta": "beta"})
        self.assertEqual(profile.get(), "unscoped")

    def test_node_search_uses_profile_policy_without_process_fallback(self):
        authority = contextvars.ContextVar("authority", default={})
        secret_scope = ModuleType("agent.secret_scope")
        secret_scope.get_secret = lambda name: authority.get().get(name)
        namespace = {}
        source = MODULE.patch_node_search(NODE_SOURCE)
        self.assertEqual(MODULE.patch_node_search(source), source)
        with patch.dict(sys.modules, {"agent": ModuleType("agent"), "agent.secret_scope": secret_scope}), patch.dict("os.environ", {"NODE_SEARCH_ALLOWED_ROOTS": "/launch-secret"}):
            exec(source, namespace)
            for owner in ["alpha", "beta"]:
                token = authority.set({"NODE_SEARCH_ALLOWED_ROOTS": f"/{owner}"})
                try:
                    self.assertEqual(namespace["roots"](), (f"/{owner}", ""))
                finally:
                    authority.reset(token)
            self.assertEqual(namespace["roots"](), ("", ""))

    def test_node_search_scope_failure_propagates(self):
        secret_scope = ModuleType("agent.secret_scope")

        def refuse(name):
            raise RuntimeError("profile scope missing")

        secret_scope.get_secret = refuse
        namespace = {}
        with patch.dict(sys.modules, {"agent": ModuleType("agent"), "agent.secret_scope": secret_scope}):
            exec(MODULE.patch_node_search(NODE_SOURCE), namespace)
            with self.assertRaisesRegex(RuntimeError, "profile scope"):
                namespace["roots"]()

    def test_existing_authority_with_missing_export_never_borrows_process_policy(self):
        namespace = {}
        with patch.dict(sys.modules, {"agent": ModuleType("agent"), "agent.secret_scope": ModuleType("agent.secret_scope")}), patch.dict("os.environ", {"NODE_SEARCH_ALLOWED_ROOTS": "/launch-secret"}):
            exec(MODULE.patch_node_search(NODE_SOURCE), namespace)
            with self.assertRaises(ImportError):
                namespace["roots"]()

    def test_drift_and_partial_repairs_are_refused(self):
        for patcher, source, marker in [(MODULE.patch_talk_transcript, TALK_SOURCE, MODULE.TALK_MARKER), (MODULE.patch_node_search, NODE_SOURCE, MODULE.NODE_MARKER)]:
            with self.subTest(marker=marker):
                with self.assertRaises(ValueError):
                    patcher("changed upstream source")
                with self.assertRaises(ValueError):
                    patcher(marker + "\n" + source)


if __name__ == "__main__":
    unittest.main()
