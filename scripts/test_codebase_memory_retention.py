"""Cache pruning regressions with real filesystem state and child locks."""
from contextlib import contextmanager
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import codebase_memory_local as launcher


def age_session(state):
    old = time.time() - 25 * 3600
    for path in (*state.rglob('*'), state):
        os.utime(path, (old, old), follow_symlinks=False)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.parent = Path(directory.name)

    def session(self, name, marker='initialized'):
        state = self.parent / name
        (state / 'runtime/cbm-daemon-test').mkdir(parents=True)
        if marker == 'initialized':
            with launcher.session_lease(state) as fd:
                self.assertTrue(os.get_inheritable(fd))
        elif marker == 'empty':
            (state / '.lease').touch()
        age_session(state)
        return state

    @contextmanager
    def held_locks(self, *locks):
        hold = ('import fcntl, sys\nfiles = [open(p, "r+b") for p in sys.argv[2::2]]\n'
                'for kind, f in zip(sys.argv[1::2], files): getattr(fcntl, kind)(f, fcntl.LOCK_EX)\n'
                'print("ready", flush=True); sys.stdin.read()')
        args = [str(arg) for lock in locks for arg in lock]
        with subprocess.Popen([sys.executable, '-c', hold, *args],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as child:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(5), 'fixture did not acquire locks')
                self.assertEqual(child.stdout.readline(), b'ready\n')
                yield
            finally:
                child.stdin.close()
        self.assertEqual(child.returncode, 0)

    def test_pruning_respects_flocks_record_locks_and_whole_tree_age(self):
        expired = self.session('session-expired')
        cases = {'lease': '.lease', 'flock': 'native.turn', 'posix': 'native.lifetime.lock',
                 'turn': 'fresh.turn', 'rw': 'fresh.rw', 'payload': 'index.db'}
        paths = {}
        for kind, name in cases.items():
            state = self.session('session-' + kind)
            lock = state / name if kind in ('payload', 'lease') else state / 'runtime/cbm-daemon-test' / name
            lock.touch()
            age_session(state)
            paths[kind] = lock
        for kind in ('turn', 'rw', 'payload'):
            paths[kind].touch()
        with self.held_locks(('flock', paths['lease']), ('flock', paths['flock']), ('lockf', paths['posix'])):
            launcher.prune_sessions(self.parent)
        self.assertFalse(expired.exists())
        for kind, path in paths.items():
            with self.subTest(kind=kind):
                self.assertTrue(path.is_file())

    def test_uninitialized_foreign_and_symlink_paths_are_untouched(self):
        legacy = self.session('session-legacy', marker='missing')
        empty = self.session('session-empty', marker='empty')
        foreign = self.session('foreign')
        unsafe = self.session('session-bad-lock')
        lock = unsafe / 'runtime/cbm-daemon-test/native.lock'
        lock.symlink_to(foreign / '.lease')
        age_session(unsafe)
        for name in ('session-link', 'trash-link'):
            (self.parent / name).symlink_to(foreign, target_is_directory=True)
        launcher.prune_sessions(self.parent)
        self.assertEqual(set(self.parent.iterdir()), {legacy, empty, foreign, unsafe,
                         self.parent / 'session-link', self.parent / 'trash-link'})
        self.assertTrue(lock.is_symlink())
        self.assertTrue((self.parent / 'session-link').is_symlink())
        self.assertTrue((self.parent / 'trash-link').is_symlink())

    @unittest.skipIf(os.geteuid() == 0, 'requires directory read permissions to be enforced')
    def test_partial_removal_is_retried_without_a_lease(self):
        state = self.session('session-partial')
        blocked = state / 'payload/unreadable'
        blocked.mkdir(parents=True)
        (blocked / 'data').touch()
        age_session(state)
        rmtree = shutil.rmtree
        failures = []

        def fail_midway(path, *args, **kwargs):
            if path == 'payload':
                trash, = self.parent.glob('trash-*')
                (trash / '.lease').unlink(missing_ok=True)
                (trash / 'payload/unreadable').chmod(0)
            try:
                return rmtree(path, *args, **kwargs)
            except PermissionError:
                failures.append(path)
                raise

        try:
            with patch.object(launcher.shutil, 'rmtree', side_effect=fail_midway):
                launcher.prune_sessions(self.parent)
            trash, = self.parent.glob('trash-*')
            self.assertEqual(failures, ['payload'])
            self.assertFalse(state.exists())
            self.assertFalse((trash / '.lease').exists())
        finally:
            for path in self.parent.glob('*/payload/unreadable'):
                path.chmod(0o700)
        launcher.prune_sessions(self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_trash_retry_respects_native_flock_and_record_lock(self):
        for kind in ('flock', 'lockf'):
            with self.subTest(kind=kind):
                state = self.session('session-' + kind)
                relative_lock = Path('runtime/cbm-daemon-test/native.rw')
                (state / relative_lock).touch()
                age_session(state)
                with patch.object(launcher, 'remove_detached', side_effect=PermissionError):
                    launcher.prune_sessions(self.parent)
                trash, = self.parent.glob('trash-*')
                (trash / '.lease').unlink()
                lock = trash / relative_lock
                with self.held_locks((kind, lock)):
                    launcher.prune_sessions(self.parent)
                    self.assertTrue(lock.is_file())
                launcher.prune_sessions(self.parent)
                self.assertFalse(trash.exists())

    def test_trash_name_without_random_suffix_is_untouched(self):
        state = self.session('not-trash')
        info = state.stat()
        near_miss = state.rename(self.parent / f'trash-{info.st_dev:x}-{info.st_ino:x}-')
        launcher.prune_sessions(self.parent)
        self.assertTrue((near_miss / '.lease').is_file())

    def test_directory_swapped_at_rename_is_not_deleted(self):
        state = self.session('session-checked')
        replacement = self.parent / 'replacement'
        replacement.mkdir()
        (replacement / 'keep').touch()
        rename = os.rename

        def swap(source, target, **kwargs):
            if source == state.name and target.startswith('trash-'):
                rename(source, 'displaced', **kwargs)
                rename(replacement.name, source, **kwargs)
            rename(source, target, **kwargs)

        with patch.object(launcher.os, 'rename', side_effect=swap) as renamed:
            launcher.prune_sessions(self.parent)
        self.assertTrue(renamed.called)
        self.assertTrue((state / 'keep').is_file())
        self.assertTrue((self.parent / 'displaced/.lease').is_file())
        self.assertEqual(list(self.parent.glob('trash-*')), [])
