/** Private session resources and durable at-most-once dispatch journal. */
import { constants } from 'node:fs';
import { open, lstat, realpath, rename, unlink } from 'node:fs/promises';
import { dirname, resolve, isAbsolute } from 'node:path';
import { randomUUID } from 'node:crypto';

const MAX_BYTES = 1024 * 1024;
const MAX_RESULT = 2048;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const fail = code => { throw new Error(code); };
const size = value => Buffer.byteLength(JSON.stringify(value));

async function privateRead(path) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o777) !== 0o600 || stat.size > MAX_BYTES) fail('state_unavailable');
    const bytes = Buffer.alloc(MAX_BYTES + 1);
    const { bytesRead } = await file.read(bytes, 0, bytes.length, 0);
    if (bytesRead > MAX_BYTES) fail('state_unavailable');
    return JSON.parse(bytes.subarray(0, bytesRead).toString('utf8'));
  } finally { await file.close(); }
}

const dictionary = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function validateSession(id, session) {
  if (!UUID.test(id) || !dictionary(session) || !['minting', 'active', 'closed'].includes(session.status)) fail('state_unavailable');
  if (session.workflow !== null && (typeof session.workflow !== 'string' || !/^[a-f0-9]{64}$/.test(session.workflow))) fail('state_unavailable');
  if (session.status === 'active' && session.workflow === null) fail('state_unavailable');
  validateNativeState(session);
}

function validateAttachment(attached) {
  if (!dictionary(attached) || !['profile', 'group', 'tab_id'].every(key => typeof attached[key] === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(attached[key]))
      || !/^[a-f0-9]{64}$/.test(attached.realm)) fail('state_unavailable');
}

function validateStagedFile(id, file) {
    if (!/^[a-f0-9]{32}$/.test(id) || !dictionary(file) || typeof file.name !== 'string'
        || !file.name.length || Buffer.byteLength(file.name) > 128 || /[\\/\\\\\u0000-\u001f\u007f]/.test(file.name)
        || !Number.isInteger(file.size) || file.size < 0 || file.size > 20971520
        || !/^[a-f0-9]{64}$/.test(file.sha256) || !['ready', 'claimed'].includes(file.state)
        || (file.state === 'claimed' && !UUID.test(file.claim))) fail('state_unavailable');
}

function validateNativeState(session) {
  if (session.attachment !== undefined && session.attachment !== null) validateAttachment(session.attachment);
  if (session.files === undefined) return;
  if (!dictionary(session.files) || Object.keys(session.files).length > 8) fail('state_unavailable');
  for (const [id, file] of Object.entries(session.files)) validateStagedFile(id, file);
}

function validateNativeVersion(version, sessions) {
  const native = sessions.some(([, session]) => session.attachment
    || Object.values(session.files || {}).some(file => file.state === 'claimed'));
  if (native && version !== 2) fail('state_unavailable');
}

function operationReservation(id, op, sessions) {
  if (!UUID.test(id) || !dictionary(op) || typeof op.fingerprint !== 'string'
      || !['pending', 'done'].includes(op.status) || !sessions[op.session_id]) fail('state_unavailable');
  if (op.status === 'done' && (!dictionary(op.result) || size(op.result) > MAX_RESULT)) fail('state_unavailable');
  return op.status === 'pending' ? MAX_RESULT + 32 : 0;
}

function validate(data, realm) {
  if (![1, 2].includes(data?.version) || data.realm !== realm || !dictionary(data.sessions) || !dictionary(data.operations)) fail('state_unavailable');
  const sessions = Object.entries(data.sessions); const operations = Object.entries(data.operations);
  if (sessions.length > 512 || operations.length > 512) fail('state_unavailable');
  for (const [id, session] of sessions) validateSession(id, session);
  validateNativeVersion(data.version, sessions);
  if (sessions.filter(([, session]) => session.status !== 'closed').length > 8) fail('state_unavailable');
  const reserved = operations.reduce((total, [id, op]) => total + operationReservation(id, op, data.sessions), 0);
  if (size(data) + reserved > MAX_BYTES) fail('operation_capacity');
  return data;
}

async function publish(path, data) {
  const temporary = path + '.' + randomUUID() + '.tmp';
  let file;
  try {
    file = await open(temporary, 'wx', 0o600);
    await file.writeFile(JSON.stringify(data)); await file.sync();
    await file.close(); file = undefined;
    await rename(temporary, path);
    const dir = await open(dirname(path), constants.O_RDONLY | constants.O_DIRECTORY);
    try { await dir.sync(); } finally { await dir.close(); }
  } finally {
    await file?.close();
    await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; });
  }
}

export class BrowserState {
  constructor(path, realm, lock) {
    this.path = path; this.realm = realm; this.lock = lock;
    this.tail = Promise.resolve(); this.poisoned = false;
  }

  static async open(path, realm) {
    if (!isAbsolute(path)) fail('state_unavailable');
    const parent = await lstat(dirname(path));
    if (!parent.isDirectory() || parent.uid !== process.getuid() || (parent.mode & 0o7777) !== 0o700 || await realpath(dirname(path)) !== resolve(dirname(path))) fail('state_unavailable');
    let lock;
    try { lock = await open(path + '.lock', constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600); }
    catch { fail('state_locked'); }
    const state = new BrowserState(path, realm, lock);
    try {
      try { state.data = validate(await privateRead(path), realm); }
      catch (error) {
        if (error.code !== 'ENOENT') throw error;
        state.data = { version: 1, realm, sessions: {}, operations: {} };
        await publish(path, state.data);
      }
      return state;
    } catch { await state.close(); fail('state_unavailable'); }
  }

  async transaction(change) {
    const run = this.tail.then(async () => {
      if (this.poisoned || !this.lock) fail('state_unavailable');
      const next = structuredClone(this.data);
      const result = change(next);
      validate(next, this.realm);
      try { await publish(this.path, next); }
      catch { this.poisoned = true; fail('state_unavailable'); }
      this.data = next;
      return result;
    });
    this.tail = run.catch(() => {});
    return run;
  }

  session(id) {
    if (this.poisoned || !this.lock) fail('state_unavailable');
    const session = this.data.sessions[id];
    if (!UUID.test(id) || !session) fail('stale_session');
    return structuredClone(session);
  }

  begin(id, fingerprint, sessionId, create = false) {
    return this.transaction(data => {
      if (!UUID.test(id)) fail('invalid_request');
      const old = data.operations[id];
      // Deliberately before checking closed sessions: cleanup replay is historical.
      if (old) {
        if (old.fingerprint !== fingerprint) fail('operation_conflict');
        return old.status === 'done' ? { result: old.result } : { pending: true };
      }
      if (Object.keys(data.operations).length >= 512) fail('operation_capacity');
      if (create) {
        if (Object.values(data.sessions).filter(session => session.status !== 'closed').length >= 8) fail('capacity');
        sessionId = randomUUID();
        data.sessions[sessionId] = { status: 'minting', workflow: null };
      } else if (!UUID.test(sessionId) || data.sessions[sessionId]?.status !== 'active') fail('stale_session');
      data.operations[id] = { fingerprint, session_id: sessionId, status: 'pending' };
      return { fresh: true, session_id: sessionId };
    });
  }

  claimFiles(id, fileIds) {
    return this.transaction(data => {
      const op = data.operations[id];
      if (!op || op.status !== 'pending') fail('state_unavailable');
      const files = data.sessions[op.session_id].files || {};
      if (fileIds.some(key => !files[key])) fail('forbidden');
      if (fileIds.some(key => files[key].state !== 'ready')) fail('file_in_use');
      if (fileIds.reduce((sum, key) => sum + files[key].size, 0) > 41943040) fail('file_too_large');
      data.version = 2; // Legacy adapters must not ignore native uncertainty on rollback.
      for (const key of fileIds) { files[key].state = 'claimed'; files[key].claim = id; }
    });
  }

  complete(id, result, sessionUpdate) {
    return this.transaction(data => {
      const op = data.operations[id];
      if (!op || op.status !== 'pending' || size(result) > MAX_RESULT) fail('state_unavailable');
      if (sessionUpdate?.attachment) {
        data.version = 2;
        const next = sessionUpdate.attachment;
        if (Object.entries(data.sessions).some(([id, session]) => id !== op.session_id && session.status === 'active'
            && session.attachment?.realm === next.realm && session.attachment.tab_id === next.tab_id)) {
          const denied = { isError: true, content: [{ type: 'text', text: '{"error":{"code":"workflow_busy"}}' }] };
          op.status = 'done'; op.result = denied; return denied;
        }
      }
      if (sessionUpdate) Object.assign(data.sessions[op.session_id], sessionUpdate);
      for (const file of Object.values(data.sessions[op.session_id].files || {})) {
        if (file.claim === id) { file.state = 'ready'; delete file.claim; }
      }
      op.status = 'done'; op.result = result;
      return result;
    });
  }

  async close() {
    await this.tail;
    const lock = this.lock;
    if (!lock) return;
    this.lock = undefined;
    try {
      const [owned, current] = await Promise.all([lock.stat(), lstat(this.path + '.lock')]);
      if (owned.ino === current.ino && owned.dev === current.dev) await unlink(this.path + '.lock');
    } finally { await lock.close(); }
  }
}
