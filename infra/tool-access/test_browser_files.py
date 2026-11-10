"""Real temporary files at the managed transfer boundary; no production paths."""
import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from browser_files import FileStore
from policy import AccessError


class FilesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.incoming, self.spool = self.root / 'incoming', self.root / 'outgoing'
        self.incoming.mkdir(mode=0o700)
        self.spool.mkdir(mode=0o700)
        self.store = FileStore(str(self.incoming), str(self.spool), '/browser/uploads')
        self.addCleanup(self.store.close)

    @contextmanager
    def copy_barrier(self):
        loop = asyncio.get_running_loop()
        entered, ceased = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        original = self.store._copy

        def controlled(*args):
            loop.call_soon_threadsafe(entered.set)
            release.wait(10)
            try:
                return original(*args)
            finally:
                loop.call_soon_threadsafe(ceased.set)

        with patch.object(self.store, '_copy', side_effect=controlled):
            try:
                yield entered, release, ceased
            finally:
                release.set()

    async def test_request_cancellation_keeps_copy_reservation_until_worker_ceases(self):
        (self.incoming / 'file').write_bytes(b'ok')
        with self.copy_barrier() as (entered, release, ceased):
            request = asyncio.create_task(self.store.stage('owner', 'file'))
            await asyncio.wait_for(entered.wait(), 2)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertEqual(self.store.bytes, 2)
            with self.assertRaisesRegex(AccessError, 'file_in_use'):
                self.store.cleanup('owner')
            release.set()
            await asyncio.wait_for(ceased.wait(), 2)
            await asyncio.gather(*self.store.workers)
        self.store.cleanup('owner')
        self.assertEqual(self.store.bytes, 0)
        self.assertEqual(list(self.spool.iterdir()), [])

    async def test_cancelled_worker_wrapper_is_not_physical_cessation_proof(self):
        (self.incoming / 'file').write_bytes(b'ok')
        with self.copy_barrier() as (entered, release, ceased):
            request = asyncio.create_task(self.store.stage('owner', 'file'))
            await asyncio.wait_for(entered.wait(), 2)
            worker = next(iter(self.store.workers))
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            try:
                self.assertTrue(self.store.blocked)
                self.assertEqual(self.store.bytes, 2)
                self.assertIn(worker, self.store.workers)
                self.assertEqual(next(iter(self.store.files.values()))['state'], 'uncertain')
                with self.assertRaisesRegex(RuntimeError, 'workers still own'):
                    self.store.close()
            finally:
                release.set()
                await asyncio.wait_for(ceased.wait(), 2)
                # Test-only reconciliation after independently observed thread cessation.
                self.store.workers.discard(worker)

    async def test_general_file_copy_identity_filename_and_source_preserved(self):
        source = self.incoming / 'drawing.zip'
        source.write_bytes(b'ordinary arbitrary bytes, no PDF restriction')
        data = await self.store.stage('owner', 'drawing.zip')
        self.assertEqual(data['name'], 'drawing.zip')
        self.assertEqual(data['size'], source.stat().st_size)
        paths = self.store.paths('owner', [data['file_id']])
        self.assertTrue(paths[0].endswith('/drawing.zip'))
        staged = self.spool / data['file_id'] / 'drawing.zip'
        self.assertEqual(staged.read_bytes(), source.read_bytes())
        with self.assertRaisesRegex(AccessError, 'forbidden'):
            self.store.paths('foreign', [data['file_id']])
        self.store.remove('owner', data['file_id'])
        self.assertTrue(source.exists())
        self.assertFalse(staged.exists())

    async def test_traversal_intermediate_and_leaf_symlinks_fifo_zero_reads(self):
        (self.incoming / 'normal').write_bytes(b'ok')
        (self.incoming / 'leaf').symlink_to('normal')
        (self.incoming / 'linked').symlink_to(self.root, target_is_directory=True)
        os.mkfifo(self.incoming / 'pipe')
        for name in ('../normal', '/etc/passwd', 'leaf', 'linked/normal', 'pipe'):
            with self.assertRaises(AccessError, msg=name):
                await asyncio.wait_for(self.store.stage('owner', name), 1)
        self.assertEqual(list(self.spool.iterdir()), [])

    async def test_size_duplicate_and_capacity_denials_before_upload(self):
        (self.incoming / 'file').write_bytes(b'12345')
        with patch('browser_files.MAX_FILE', 4):
            with self.assertRaisesRegex(AccessError, 'file_too_large'):
                await self.store.stage('owner', 'file')
        staged = await self.store.stage('owner', 'file')
        with self.assertRaisesRegex(AccessError, 'invalid_request'):
            self.store.paths('owner', [staged['file_id'], staged['file_id']])
        with patch('browser_files.MAX_BYTES', 5):
            with self.assertRaisesRegex(AccessError, 'capacity'):
                await self.store.stage('owner', 'file')
        self.assertEqual(len(list(self.spool.iterdir())), 1)

    async def test_restart_remnants_disable_stage_without_deleting_or_adopting(self):
        (self.incoming / 'file').write_bytes(b'ok')
        staged = await self.store.stage('owner', 'file')
        restarted = FileStore(str(self.incoming), str(self.spool), '/browser/uploads')
        self.addCleanup(restarted.close)
        with self.assertRaisesRegex(AccessError, 'reconciliation_required'):
            await restarted.stage('owner', 'file')
        self.assertTrue((self.spool / staged['file_id'] / 'file').exists())

    async def test_ambiguous_upload_and_failed_delete_retain_charged_files(self):
        (self.incoming / 'file').write_bytes(b'ok')
        staged = await self.store.stage('owner', 'file')
        self.store.uncertain('owner', [staged['file_id']])
        with self.assertRaisesRegex(AccessError, 'file_in_use'):
            self.store.remove('owner', staged['file_id'])
        self.assertEqual(self.store.bytes, 2)
        other = await self.store.stage('owner', 'file')
        with patch('browser_files.os.unlink', side_effect=OSError('synthetic')):
            with self.assertRaisesRegex(AccessError, 'reconciliation_required'):
                self.store.remove('owner', other['file_id'])
        self.assertEqual(self.store.bytes, 4)

    async def test_failed_read_removes_exact_partial_after_worker_finishes(self):
        (self.incoming / 'file').write_bytes(b'ok')
        with patch('browser_files.os.read', side_effect=OSError('synthetic-copy-error')):
            with self.assertRaisesRegex(AccessError, 'invalid_request'):
                await self.store.stage('owner', 'file')
        self.assertEqual(self.store.bytes, 0)
        self.assertEqual(list(self.spool.iterdir()), [])
