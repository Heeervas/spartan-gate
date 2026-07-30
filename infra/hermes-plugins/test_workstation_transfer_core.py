from __future__ import annotations

import importlib.util
import io
import os
import pathlib
import tempfile
import unittest


CORE_PATH = (
    pathlib.Path(__file__).parent
    / "spartan-workstation-terminal"
    / "transfer_core.py"
)


def load_core():
    spec = importlib.util.spec_from_file_location(
        "spartan_workstation_transfer_core", CORE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class WorkstationTransferCoreTests(unittest.TestCase):
    def setUp(self):
        self.core = load_core()

    def test_file_and_directory_manifest_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "project" / "empty").mkdir(parents=True)
            (root / "project" / "a.txt").write_bytes(b"alpha")
            executable = root / "project" / "run.sh"
            executable.write_bytes(b"#!/bin/sh\n")
            executable.chmod(0o700)

            first = self.core.preflight_source(root, "project")
            second = self.core.preflight_source(root, "project")

            self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
            self.assertEqual(first["entry_count"], 4)
            self.assertEqual(first["total_bytes"], 15)
            self.assertEqual(first["kind"], "directory")
            self.assertEqual(first["entries"], second["entries"])

    def test_rejects_traversal_symlink_hardlink_and_special_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            outside = root.parent / f"{root.name}-outside"
            outside.write_text("outside")
            (root / "link").symlink_to(outside)
            original = root / "original"
            original.write_text("same inode")
            os.link(original, root / "hard")
            fifo = root / "pipe"
            os.mkfifo(fifo)

            for relative in ("../escape", "link", "original", "hard", "pipe"):
                with self.subTest(relative=relative):
                    with self.assertRaises(self.core.TransferError):
                        self.core.preflight_source(root, relative)
            outside.unlink()

    def test_hard_denies_private_keys_and_soft_sensitive_requires_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "id_ed25519").write_text(
                "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n"
            )
            (root / ".env").write_text("TOKEN=value\n")

            with self.assertRaisesRegex(self.core.TransferError, "hard-denied"):
                self.core.preflight_source(root, "id_ed25519", allow_sensitive=True)
            with self.assertRaisesRegex(self.core.TransferError, "allow_sensitive"):
                self.core.preflight_source(root, ".env")

            allowed = self.core.preflight_source(
                root, ".env", allow_sensitive=True
            )
            self.assertEqual(allowed["soft_sensitive_count"], 1)

    def test_round_trip_binary_directory_and_executable(self):
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as dest_tmp:
            source_root = pathlib.Path(source_tmp)
            dest_root = pathlib.Path(dest_tmp)
            project = source_root / "project"
            project.mkdir()
            (project / "binary.bin").write_bytes(bytes(range(256)) + b"\0tail")
            executable = project / "run.sh"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o700)
            summary = self.core.preflight_source(source_root, "project")

            stream = io.BytesIO()
            self.core.stream_tar(source_root, "project", summary, stream)
            stream.seek(0)
            result = self.core.receive_tar(
                stream,
                dest_root,
                "received",
                summary,
                transfer_id="wtx_" + "a" * 24,
                replace=False,
                minimum_free_bytes=0,
            )

            self.assertEqual(result["manifest_sha256"], summary["manifest_sha256"])
            self.assertEqual(
                (dest_root / "received" / "binary.bin").read_bytes(),
                bytes(range(256)) + b"\0tail",
            )
            self.assertTrue(
                (dest_root / "received" / "run.sh").stat().st_mode & 0o100
            )

    def test_existing_directory_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as dest_tmp:
            source_root = pathlib.Path(source_tmp)
            dest_root = pathlib.Path(dest_tmp)
            (source_root / "tree").mkdir()
            (source_root / "tree" / "a").write_text("a")
            (dest_root / "tree").mkdir()
            summary = self.core.preflight_source(source_root, "tree")
            stream = io.BytesIO()
            self.core.stream_tar(source_root, "tree", summary, stream)
            stream.seek(0)

            with self.assertRaisesRegex(self.core.TransferError, "directory"):
                self.core.receive_tar(
                    stream,
                    dest_root,
                    "tree",
                    summary,
                    transfer_id="wtx_" + "b" * 24,
                    replace=True,
                    minimum_free_bytes=0,
                )

    def test_windows_destination_rejects_casefold_collision_and_reserved_names(self):
        for paths in (
            ["A.txt", "a.txt"],
            ["CON"],
            ["trailing."],
            ["bad:name"],
        ):
            with self.subTest(paths=paths):
                with self.assertRaises(self.core.TransferError):
                    self.core.validate_windows_paths(paths)


    def test_destination_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "real").write_text("data", encoding="utf-8")
            (root / "link").symlink_to("real")
            with self.assertRaises(self.core.TransferError):
                self.core.destination_metadata(root, "link")

    def test_destination_capability_probe_is_complete_and_cleans_exact_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            before = set(root.iterdir())
            result = self.core.probe_destination_capabilities(root)
            after = set(root.iterdir())

        self.assertTrue(all(result.values()))
        self.assertEqual(before, after)

    def test_parent_symlink_swap_cannot_redirect_publication(self):
        with tempfile.TemporaryDirectory() as source_tmp, tempfile.TemporaryDirectory() as dest_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            source_root = pathlib.Path(source_tmp)
            dest_root = pathlib.Path(dest_tmp)
            outside = pathlib.Path(outside_tmp)
            (source_root / "artifact").write_text("anchored", encoding="utf-8")
            (dest_root / "parent").mkdir()
            summary = self.core.preflight_source(source_root, "artifact")
            archive = io.BytesIO()
            self.core.stream_tar(source_root, "artifact", summary, archive)

            class SwappingReader(io.BytesIO):
                swapped = False

                def read(reader_self, size=-1):
                    if not reader_self.swapped:
                        reader_self.swapped = True
                        (dest_root / "parent").rename(dest_root / "parent-original")
                        (dest_root / "parent").symlink_to(outside, target_is_directory=True)
                    return super().read(size)

            stream = SwappingReader(archive.getvalue())
            self.core.receive_tar(
                stream,
                dest_root,
                "parent/result",
                summary,
                transfer_id="wtx_" + "c" * 24,
                replace=False,
                minimum_free_bytes=0,
            )

            self.assertEqual(
                (dest_root / "parent-original" / "result").read_text(encoding="utf-8"),
                "anchored",
            )
            self.assertFalse((outside / "result").exists())

if __name__ == "__main__":
    unittest.main()
