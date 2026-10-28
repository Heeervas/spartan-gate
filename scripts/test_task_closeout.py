"""Executable synthetic v1 example and real CLI/Git refusal controls.

All repositories, writer/restore attestations, files and workspaces are synthetic.
No native runtime or real operator cessation is simulated as automatic certainty.
"""

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CHECKER = Path(__file__).with_name("check_task_closeout.py")


class TaskCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="closeout-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / "config"),
                        GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
        self.git("init", "-q")
        (self.repo / "owned.txt").write_text("accepted source\n")
        self.git("add", "owned.txt")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "fixture")
        self.package = self.root / "tasks" / "example-task"
        self.package.mkdir(parents=True)
        self.task = self.package / "task.md"
        self.proof = self.package / "evidence.txt"
        self.proof.write_text("Synthetic controller: source accepted, writers ceased, consumers reconciled.\n"
                              "Synthetic copy restore compared byte-for-byte successfully.\n")
        self.destination = self.root / "tasks/archive/2026/example-task/task.md"
        self.reference = self.root / "current.md"
        self.link = "tasks/archive/2026/example-task/task.md"
        self.reference.write_text(f"Current task: [example]({self.link})\n")
        evidence = [self.file_identity(self.proof, "evidence.txt")]
        self.receipt = {
            "version": 1, "task_id": "example-task", "owner": "controller",
            "repository": {"common_dir": str(self.repo / ".git"), "head": self.git("rev-parse", "HEAD").strip()},
            "outcome": {"disposition": "accepted", "evidence": evidence},
            "scope": {"owned_paths": ["owned.txt"], "foreign_preserved": []},
            "writers": {"state": "ceased", "authority": "synthetic runtime controller",
                        "observed_at": datetime.now(timezone.utc).isoformat(), "evidence": evidence},
            "consumers": {"state": "reconciled", "authority": "synthetic controller",
                          "observed_at": datetime.now(timezone.utc).isoformat(), "evidence": evidence},
            "recovery": {"state": "verified", "evidence": evidence},
            "promotions": {"state": "reconciled", "reason": "No new durable lesson is necessary"},
            "resources": [], "unresolved": [],
            "archive": {"destination": str(self.destination),
                        "references": [{"path": str(self.reference), "target": self.link}]},
        }
        self.status, self.closeout, self.successor = "review", "pending", "null"

    def git(self, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.repo), *args],
                                env=self.env, capture_output=True, text=True, check=True)
        return result.stdout

    def file_identity(self, path, name=None):
        return {"path": name or str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def save(self):
        self.task.write_text(
            f"---\nplan_id: example-task\nowner: controller\nstatus: {self.status}\n"
            f"updated_at: {datetime.now(timezone.utc).date()}\nsuperseded_by: {self.successor}\n"
            f"closeout: {self.closeout}\n---\n# Synthetic example\n\n"
            "<!-- task-closeout:v1 -->\n```json\n" + json.dumps(self.receipt) + "\n```\n")

    def cli(self, phase="ready", inline=None):
        self.save()
        command = [sys.executable, str(CHECKER), "--repo", str(self.repo)]
        command += ["--inline", str(inline)] if inline else ["--task", str(self.task)]
        if phase is not None:
            command += ["--phase", phase]
        return subprocess.run(command, env=self.env, capture_output=True, text=True, check=False)

    def passes(self, phase="ready", inline=None):
        result = self.cli(phase, inline)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: example-task", result.stdout)
        self.assertEqual(result.stderr, "")

    def refuses(self, reason, phase="ready"):
        result = self.cli(phase)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def archive(self):
        self.status, self.closeout = "done", "complete"
        self.save()
        self.destination.parent.parent.mkdir(parents=True)
        self.package.rename(self.destination.parent)
        self.package, self.task = self.destination.parent, self.destination
        self.proof = self.package / "evidence.txt"

    def resource(self, path, kind="scratch", disposition="removed"):
        item = {"path": str(path), "kind": kind, "owner": "controller", "disposition": disposition,
                "evidence": copy.deepcopy(self.receipt["outcome"]["evidence"])}
        self.receipt["resources"].append(item)
        return item

    def test_ready_archive_closed_and_read_only_readback(self):
        self.passes()
        self.archive()
        # Snapshot all authored files and the Git index before the default closed CLI.
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.passes(None)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual((self.reference.parent / self.link).read_bytes(), self.task.read_bytes())

    def test_small_inline_default_closed(self):
        self.receipt["archive"] = {"inline": True, "reason": "One small source-only correction; no package"}
        inline = self.package / "receipt.json"
        inline.write_text(json.dumps(self.receipt))
        self.passes(None, inline)

    def test_package_cannot_take_inline_exemption(self):
        self.passes()
        self.receipt["archive"] = {"inline": True, "reason": "pretend small"}
        self.refuses("missing fields")

    def test_preserved_identified_foreign_dirt_permits_scoped_closure(self):
        foreign = self.repo / "foreign.txt"
        foreign.write_text("Other person's work.\n")
        self.receipt["scope"]["foreign_preserved"] = [self.file_identity(foreign, "foreign.txt")]
        self.passes()
        self.archive()
        self.passes("closed")
        self.assertEqual(foreign.read_text(), "Other person's work.\n")

    def test_unknown_ownership_and_unsupported_cleanliness_refuse(self):
        self.passes()
        (self.repo / "unclaimed.txt").write_text("unclaimed")
        self.refuses("unknown ownership")
        self.receipt["scope"]["clean"] = True
        self.refuses("unknown fields")

    def test_dirty_owned_paths_and_staged_changes_refuse(self):
        self.passes()
        (self.repo / "owned.txt").write_text("not integrated")
        self.refuses("owned scope is dirty")
        self.git("add", "owned.txt")
        self.refuses("owned scope is dirty")

    def test_ignored_owned_source_does_not_count_as_integrated(self):
        self.passes()
        (self.repo / ".git/info/exclude").write_text("ignored-owned.txt\n")
        (self.repo / "ignored-owned.txt").write_text("uncommitted useful source")
        self.receipt["scope"]["owned_paths"].append("ignored-owned.txt")
        self.refuses("owned scope has ignored untracked data")

    def test_foreign_overlap_and_changed_foreign_identity_refuse(self):
        self.passes()
        self.receipt["scope"]["foreign_preserved"] = [self.file_identity(self.repo / "owned.txt", "owned.txt")]
        self.refuses("foreign path overlaps")
        foreign = self.repo / "foreign.txt"
        foreign.write_text("original")
        self.receipt["scope"]["foreign_preserved"] = [self.file_identity(foreign, "foreign.txt")]
        foreign.write_text("newer foreign work")
        self.refuses("SHA-256 mismatch")

    def test_foreign_disposal_and_evidence_inside_disposal_refuse(self):
        self.passes()
        self.resource(self.package)
        self.refuses("would dispose foreign data, receipt or required evidence")
        self.receipt["resources"].clear()
        item = self.resource(self.root / "other-task")
        item["owner"] = "another-controller"
        self.refuses("unknown or foreign resource ownership")

    def test_missing_empty_and_changed_evidence_refuse(self):
        self.passes()
        self.proof.write_text("changed")
        self.refuses("SHA-256 mismatch")
        self.proof.unlink()
        self.refuses("not a regular evidence file")
        self.receipt["outcome"]["evidence"] = []
        self.refuses("required list is empty")

    def test_active_unknown_and_unattributable_writers_refuse(self):
        self.passes()
        for state in ("active", "unknown"):
            self.receipt["writers"]["state"] = state
            self.refuses("required assertion state: ceased")
        self.receipt["writers"]["state"] = "ceased"
        self.receipt["writers"]["authority"] = ""
        self.refuses("expected nonempty text")

    def test_consumer_and_observation_freshness_refuse(self):
        self.passes()
        self.receipt["consumers"]["state"] = "active"
        self.refuses("required assertion state: reconciled")
        self.receipt["consumers"]["state"] = "reconciled"
        for observed, message in (("2020-01-01T00:00:00+00:00", "predates package update"),
                                  ("2020-01-01T00:00:00", "requires a timezone"),
                                  ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), "in the future")):
            self.receipt["consumers"]["observed_at"] = observed
            self.refuses(message)

    def test_failed_restore_and_unresolved_flags_refuse(self):
        self.passes()
        self.receipt["recovery"]["state"] = "failed"
        self.refuses("recovery/restore is not verified")
        self.receipt["recovery"]["state"] = "verified"
        self.receipt["unresolved"] = ["restore comparison failed"]
        self.refuses("unresolved flags")

    def test_collision_checks_whole_destination_package(self):
        self.passes()
        self.destination.parent.mkdir(parents=True)
        (self.destination.parent / "foreign-evidence.txt").write_text("do not overwrite")
        self.refuses("archive destination collision")
        self.assertEqual((self.destination.parent / "foreign-evidence.txt").read_text(), "do not overwrite")

    def test_dangling_archive_directory_is_a_collision(self):
        self.passes()
        self.destination.parent.parent.mkdir(parents=True)
        self.destination.parent.symlink_to(self.root / "missing-archive")
        self.refuses("archive destination collision")

    def test_closed_requires_placement_terminal_metadata_and_identity(self):
        self.passes()
        self.refuses("not terminal", "closed")
        self.status, self.closeout = "done", "complete"
        self.refuses("not at its readable archive destination", "closed")
        self.archive()
        self.passes("closed")
        self.closeout = "pending"
        self.refuses("closeout is incomplete", "closed")
        self.closeout = "complete"
        self.receipt["task_id"] = "different-task"
        self.refuses("identity differ", "closed")

    def test_reference_text_and_destination_readback_refuse(self):
        self.passes()
        self.reference.write_text("stale reference")
        self.refuses("does not contain declared target")
        self.reference.write_text("other-task.md")
        self.receipt["archive"]["references"][0]["target"] = "other-task.md"
        self.refuses("resolves to a different task")

    def test_repository_head_common_registration_and_locks(self):
        self.passes()
        original = self.receipt["repository"]["head"]
        self.receipt["repository"]["head"] = "0" * 40
        self.refuses("HEAD does not match")
        self.receipt["repository"]["head"] = original
        self.receipt["repository"]["common_dir"] = str(self.root / "other/.git")
        self.refuses("common-repository mismatch")
        self.receipt["repository"]["common_dir"] = str(self.repo / ".git")
        (self.repo / ".git/index.lock").write_text("synthetic lock")
        self.refuses("Git lock is unresolved")

    def test_scratch_ready_then_removed_closed_with_restore_readback(self):
        scratch = self.root / "scratch"
        scratch.mkdir()
        (scratch / "required.txt").write_text("useful unique content")
        recovery = self.package / "recovery.txt"
        shutil.copy2(scratch / "required.txt", recovery)
        restored = scratch / "restored.txt"
        shutil.copy2(recovery, restored)
        self.assertEqual(restored.read_bytes(), (scratch / "required.txt").read_bytes())
        self.receipt["recovery"]["evidence"] = [self.file_identity(recovery, "recovery.txt")]
        self.resource(scratch)
        self.passes()
        self.archive()
        self.refuses("removed resource still exists", "closed")
        shutil.rmtree(scratch)  # Only this test's owned disposable fixture.
        self.passes("closed")
        self.assertEqual((self.package / "recovery.txt").read_text(), "useful unique content")

    def test_retention_requires_independent_purpose_bound_approval(self):
        retained = self.root / "retained"
        retained.mkdir()
        item = self.resource(retained, disposition="retained")
        item.update(purpose="review reproduction", approved_by="operator", review_trigger="after review")
        self.passes()
        self.archive()
        self.passes("closed")
        item["approved_by"] = "controller"
        self.refuses("cannot be self-approved", "closed")
        item["approved_by"] = "operator"
        item["review_trigger"] = ""
        self.refuses("expected nonempty text", "closed")

    def test_real_worktree_disposition_registration_and_cleanliness(self):
        workspace = self.root / "workspace"
        self.git("worktree", "add", "--detach", "-q", str(workspace))
        self.resource(workspace, "worktree")
        self.passes()
        self.git("worktree", "lock", str(workspace))
        self.refuses("locked or prunable")
        self.git("worktree", "unlock", str(workspace))
        (workspace / "foreign.txt").write_text("not disposable")
        self.refuses("workspace is dirty")
        (workspace / "foreign.txt").unlink()
        self.archive()
        self.refuses("removed resource still exists", "closed")
        self.git("worktree", "remove", str(workspace))
        self.passes("closed")

    def test_missing_but_registered_worktree_refuses_closed(self):
        workspace = self.root / "workspace"
        self.git("worktree", "add", "--detach", "-q", str(workspace))
        self.resource(workspace, "native-worktree")
        self.passes()
        self.archive()
        shutil.rmtree(workspace)  # Simulate failed disposal without native registration readback.
        self.refuses("still has Git registration", "closed")

    def test_retained_worktree_and_linked_checkout_identity(self):
        workspace = self.root / "workspace"
        self.git("worktree", "add", "--detach", "-q", str(workspace))
        item = self.resource(workspace, "native-worktree", "retained")
        item.update(purpose="native review", approved_by="operator", review_trigger="review verdict")
        self.passes()
        self.archive()
        self.passes("closed")
        # Use the linked checkout as the actual verification root, not its .git file.
        self.receipt["resources"].clear()
        self.repo = workspace
        self.passes("closed")

    def test_rename_source_cannot_escape_dirty_scope_classification(self):
        self.passes()
        self.git("mv", "owned.txt", "renamed.txt")
        self.receipt["scope"]["foreign_preserved"] = [self.file_identity(self.repo / "renamed.txt", "renamed.txt")]
        self.refuses("owned scope is dirty: owned.txt")

    def test_symlink_resource_and_escaping_owned_scope_refuse(self):
        self.passes()
        link = self.root / "removed-link"
        link.symlink_to(self.root / "absent-resource")
        self.resource(link)
        self.refuses("resource path must not be a symlink")
        self.receipt["resources"].clear()
        outside = self.repo / "outside"
        outside.symlink_to(self.package, target_is_directory=True)
        self.receipt["scope"]["owned_paths"].append("outside")
        self.refuses("owned path escapes checkout")

    def test_git_operation_and_reference_lock_refuse(self):
        self.passes()
        (self.repo / ".git/MERGE_HEAD").write_text(self.receipt["repository"]["head"])
        self.refuses("Git operation is unresolved")
        (self.repo / ".git/MERGE_HEAD").unlink()
        (self.repo / ".git/refs/heads/fixture.lock").write_text("busy")
        self.refuses("Git lock is unresolved")

    def test_worktree_cannot_masquerade_as_scratch(self):
        workspace = self.root / "workspace"
        self.git("worktree", "add", "--detach", "-q", str(workspace))
        self.resource(workspace)
        self.refuses("cannot be classified as scratch")

    def test_ignored_unique_worktree_data_blocks_ready(self):
        workspace = self.root / "workspace"
        self.git("worktree", "add", "--detach", "-q", str(workspace))
        self.resource(workspace, "worktree")
        self.passes()
        (self.repo / ".git/info/exclude").write_text("unique-ignored.txt\n")
        (workspace / "unique-ignored.txt").write_text("unique data, not approved for disposal")
        self.refuses("resource workspace has ignored untracked data")
        self.assertTrue((workspace / "unique-ignored.txt").is_file())

    def test_removed_ancestor_cannot_contain_retained_resource(self):
        disposable = self.root / "disposable"
        retained = disposable / "retained"
        retained.mkdir(parents=True)
        self.resource(disposable)
        self.passes()
        item = self.resource(retained, disposition="retained")
        item.update(purpose="necessary evidence", approved_by="operator", review_trigger="after review")
        self.refuses("would dispose protected retained resource or archive destination")

    def test_removed_ancestor_cannot_contain_intended_archive(self):
        disposable = self.root / "disposable"
        disposable.mkdir()
        self.resource(disposable)
        self.passes()
        self.destination = disposable / "archive/2026/example-task/task.md"
        self.receipt["archive"]["destination"] = str(self.destination)
        self.link = "disposable/archive/2026/example-task/task.md"
        self.reference.write_text(self.link)
        self.receipt["archive"]["references"][0]["target"] = self.link
        self.refuses("would dispose protected retained resource or archive destination")

    def test_identified_foreign_tracked_deletion_passes_and_recreation_refuses(self):
        foreign = self.repo / "foreign-deleted.txt"
        foreign.write_text("foreign source")
        self.git("add", "foreign-deleted.txt")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "foreign baseline")
        self.receipt["repository"]["head"] = self.git("rev-parse", "HEAD").strip()
        foreign.unlink()
        self.receipt["scope"]["foreign_preserved"] = [{"path": foreign.name, "state": "deleted"}]
        self.passes()
        self.archive()
        self.passes("closed")
        self.git("rm", "--cached", "foreign-deleted.txt")
        self.passes("closed")  # Staged foreign deletion must also remain representable.
        foreign.write_text("new foreign data")
        self.refuses("foreign deletion is no longer absent", "closed")

    def test_foreign_deletion_requires_tracked_identity(self):
        self.passes()
        self.receipt["scope"]["foreign_preserved"] = [{"path": "never-tracked.txt", "state": "deleted"}]
        self.refuses("foreign deletion is not tracked")

    def test_identified_foreign_symlink_passes_and_changed_target_refuses(self):
        foreign = self.repo / "foreign-link"
        foreign.symlink_to("../original-target")
        self.git("add", "foreign-link")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "foreign symlink baseline")
        self.receipt["repository"]["head"] = self.git("rev-parse", "HEAD").strip()
        foreign.unlink()
        foreign.symlink_to("../absent-target")
        self.receipt["scope"]["foreign_preserved"] = [
            {"path": foreign.name, "state": "symlink", "target": "../absent-target"}]
        self.passes()
        self.archive()
        self.passes("closed")
        foreign.unlink()
        foreign.symlink_to("../different-target")
        self.refuses("foreign symlink target changed", "closed")
        foreign.unlink()
        foreign.write_text("not a link")
        self.refuses("foreign symlink is missing or replaced", "closed")

    def test_superseded_successor_unknown_missing_and_self_refuse(self):
        self.archive()
        self.passes("closed")
        self.receipt["outcome"]["disposition"] = "superseded"
        self.status = "superseded"
        for successor, message in (("unknown", "unknown assertion"),
                                   ("missing/task.md", "successor is not a readable task file"),
                                   ("task.md", "successor must be another task")):
            self.successor = successor
            self.refuses(message, "closed")

    def test_successor_readback_requires_different_valid_task_identity(self):
        successor = self.root / "tasks/replacement/task.md"
        successor.parent.mkdir(parents=True)
        successor.write_text("not a task record")
        self.receipt["outcome"]["disposition"] = "superseded"
        self.successor = "../replacement/task.md"
        self.refuses("missing flat package frontmatter")
        self.save()
        successor.write_text(self.task.read_text())
        self.refuses("successor task identity must differ")
        successor.write_text(successor.read_text().replace("plan_id: example-task", "plan_id: replacement"))
        self.passes()

    def test_rejected_and_superseded_terminal_outcomes(self):
        self.receipt["outcome"]["disposition"] = "rejected"
        self.passes()
        self.archive()
        self.status = "rejected"
        self.passes("closed")
        self.receipt["outcome"]["disposition"] = "superseded"
        self.status = "superseded"
        self.refuses("needs a successor", "closed")
        successor = self.root / "tasks/replacement/task.md"
        successor.parent.mkdir(parents=True)
        successor.write_text(self.task.read_text().replace("plan_id: example-task", "plan_id: replacement"))
        self.successor = "../../../replacement/task.md"
        self.passes("closed")

    def test_malformed_receipts_fail_without_traceback(self):
        self.passes()
        baseline = copy.deepcopy(self.receipt)
        for mutate in (lambda r: r.pop("owner"), lambda r: r.update(version=True),
                       lambda r: r["outcome"].update(disposition="abandoned"),
                       lambda r: r["scope"].update(owned_paths=["../outside"]),
                       lambda r: r.update(resources="none")):
            self.receipt = copy.deepcopy(baseline)
            mutate(self.receipt)
            result = self.cli()
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("REFUSED:", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_duplicate_marker_or_json_key_refuses(self):
        self.save()
        original = self.task.read_text()
        for raw, message in ((original + "<!-- task-closeout:v1 -->", "exactly one receipt marker"),
                             (original.replace('"version": 1', '"version": 1, "version": 1'), "duplicate JSON key")):
            self.task.write_text(raw)
            result = subprocess.run([sys.executable, str(CHECKER), "--repo", str(self.repo),
                                     "--task", str(self.task), "--phase", "ready"],
                                    env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn(message, result.stdout)


if __name__ == "__main__":
    unittest.main()
