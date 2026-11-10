"""Explicit transfer-folder files. No source discovery, adoption or automatic GC."""
import asyncio
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from policy import AccessError

MAX_FILE = 20 * 1024 * 1024
MAX_BYTES = 256 * 1024 * 1024
MAX_ATTACH = 40 * 1024 * 1024
FILE_ID = re.compile(r'[0-9a-f]{32}\Z')


def root_descriptor(path, *, private=False):
    if not isinstance(path, str) or not os.path.isabs(path) or str(Path(path).resolve()) != os.path.abspath(path):
        raise ValueError('unsafe transfer root')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        os.close(fd)
        raise ValueError('unsafe spool ownership')
    return fd


def source_parts(relative):
    if (not isinstance(relative, str) or not relative or len(relative) > 1024
            or relative.startswith('/') or '\\' in relative or '\x00' in relative):
        raise AccessError('invalid_request')
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise AccessError('invalid_request')
    return parts


def source_info(fd, name):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or len(name.encode()) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise AccessError('invalid_request')
    if info.st_size > MAX_FILE:
        raise AccessError('file_too_large')
    return info


def open_source(root, relative):
    parts = source_parts(relative)
    directory = os.dup(root)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError:
        raise AccessError('invalid_request') from None
    finally:
        os.close(directory)
    try:
        return fd, source_info(fd, parts[-1]), parts[-1]
    except BaseException:
        os.close(fd)
        raise


class FileStore:
    def __init__(self, incoming, spool, remote_root):
        self.incoming = root_descriptor(incoming, private=True)
        try:
            self.spool = root_descriptor(spool, private=True)
            if not os.path.isabs(remote_root) or '..' in remote_root.split('/'):
                raise ValueError('invalid remote upload root')
        except Exception:
            os.close(self.incoming)
            if hasattr(self, 'spool'):
                os.close(self.spool)
            raise
        self.remote_root = remote_root.rstrip('/')
        with os.scandir(self.spool) as entries:
            self.blocked = next(entries, None) is not None
        self.files = {}
        self.bytes = 0
        self.workers = set()

    def available(self):
        if self.blocked:
            raise AccessError('reconciliation_required')

    async def stage(self, owner, relative):
        self.available()
        fd, info, name = open_source(self.incoming, relative)
        owned = sum(item['owner'] == owner for item in self.files.values())
        if owned >= 8 or len(self.files) >= 64 or self.bytes + info.st_size > MAX_BYTES:
            os.close(fd)
            raise AccessError('capacity')
        file_id = uuid.uuid4().hex
        record = {'owner': owner, 'name': name, 'size': info.st_size, 'state': 'copying'}
        self.files[file_id] = record
        self.bytes += info.st_size
        worker = asyncio.create_task(asyncio.to_thread(self._copy, fd, info, file_id, name))
        self.workers.add(worker)
        worker.add_done_callback(lambda done: self._finished(done, file_id, record))
        try:
            digest = await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Worker/resource remain accounted until their actual completion.
            raise
        except Exception:
            raise AccessError('invalid_request') from None
        return {'file_id': file_id, 'name': name, 'size': info.st_size, 'sha256': digest}

    def _copy(self, fd, before, file_id, name):
        destination = None
        try:
            os.mkdir(file_id, 0o700, dir_fd=self.spool)
            destination = os.open(file_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.spool)
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination)
            digest, count = hashlib.sha256(), 0
            with os.fdopen(out, 'wb') as target:
                while chunk := os.read(fd, 256 * 1024):
                    count += len(chunk)
                    if count > before.st_size or count > MAX_FILE:
                        raise ValueError('source grew')
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            after = os.fstat(fd)
            if count != before.st_size or (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('source changed')
            return digest.hexdigest()
        finally:
            os.close(fd)
            if destination is not None:
                os.close(destination)

    def _finished(self, worker, file_id, record):
        if worker.cancelled():
            # Cancelling to_thread's wrapper does NOT stop its physical thread.
            # Keep descriptors, bytes and the exact path reserved until an operator
            # can attest cessation; never unlink a possibly still-active copy.
            record['state'] = 'uncertain'
            self.blocked = True
            return
        self.workers.discard(worker)
        try:
            record['sha256'] = worker.result()
            record['state'] = 'ready'
        except BaseException:
            record['state'] = 'failed'
            try:
                self._delete(file_id, record['name'])
                self.bytes -= record['size']
                self.files.pop(file_id, None)
            except OSError:
                self.blocked = True  # Keep charged; never guess that cleanup succeeded.

    def owned(self, owner, file_id):
        record = self.files.get(file_id) if isinstance(file_id, str) and FILE_ID.fullmatch(file_id) else None
        if not record or record['owner'] != owner:
            raise AccessError('forbidden')
        return record

    def paths(self, owner, file_ids):
        self.available()
        if (not isinstance(file_ids, list) or not 1 <= len(file_ids) <= 8
                or any(not isinstance(value, str) for value in file_ids)
                or len(set(file_ids)) != len(file_ids)):
            raise AccessError('invalid_request')
        records = [self.owned(owner, value) for value in file_ids]
        if any(item['state'] != 'ready' for item in records):
            raise AccessError('file_in_use')
        if sum(item['size'] for item in records) > MAX_ATTACH:
            raise AccessError('file_too_large')
        return [self.remote_root + '/' + value + '/' + item['name'] for value, item in zip(file_ids, records)]

    def uncertain(self, owner, file_ids):
        for file_id in file_ids:
            self.owned(owner, file_id)['state'] = 'uncertain'

    def _delete(self, file_id, name):
        directory = os.open(file_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.spool)
        try:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        finally:
            os.close(directory)
        os.rmdir(file_id, dir_fd=self.spool)

    def removable(self, owner):
        if any(item['owner'] == owner and item['state'] != 'ready' for item in self.files.values()):
            raise AccessError('file_in_use')

    def remove(self, owner, file_id):
        record = self.owned(owner, file_id)
        if record['state'] != 'ready':
            raise AccessError('file_in_use')
        try:
            self._delete(file_id, record['name'])
        except OSError:
            self.blocked = True
            raise AccessError('reconciliation_required') from None
        self.bytes -= record['size']
        del self.files[file_id]

    def cleanup(self, owner):
        self.removable(owner)
        for file_id in [key for key, item in self.files.items() if item['owner'] == owner]:
            self.remove(owner, file_id)

    async def shutdown(self):
        if self.workers:
            await asyncio.shield(asyncio.gather(*self.workers, return_exceptions=True))
        self.close()

    def close(self):
        if self.workers:
            raise RuntimeError('copy workers still own spool descriptors')
        for name in ('incoming', 'spool'):
            fd = getattr(self, name, None)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)
