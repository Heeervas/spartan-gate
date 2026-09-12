const assert = require('assert');
const fs = require('fs');
const fsp = require('fs/promises');
const os = require('os');
const path = require('path');
const test = require('node:test');
const { pathToFileURL } = require('url');
const { patchPersistence, patchPlugin, patchServer } = require('./patch-persistence.js');

function persistenceFixture() {
  return `import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
function getUserPersistencePaths(profileDir, userId) {
  const userDir = path.join(profileDir, crypto.createHash('sha256').update(String(userId)).digest('hex').slice(0, 32));
  return { userDir, storageStatePath: path.join(userDir, 'storage-state.json'), metaPath: path.join(userDir, 'meta.json') };
}
async function persistStorageState({
  profileDir,
  userId,
  context,
  storageState,
  logger = console,
  indexedDB = false,
}) {
  if (!profileDir || (!context && !storageState)) return { persisted: false, reason: 'disabled' };
  return { persisted: true, indexedDB, logger };
}

export { getUserPersistencePaths, persistStorageState };
`;
}

function pluginFixture() {
  return `export async function register(app, ctx) {
  const activeSessions = new Map();
  const checkpointPromises = new Map();
  const resettingUsers = new Set();
  async function checkpoint(userId, context, reason, storageState) {
    const previous = checkpointPromises.get(userId) || Promise.resolve();
    const current = previous.catch(() => {}).then(async () => ({ persisted: true, userId, context, reason, storageState }));
    checkpointPromises.set(userId, current);
    try { return await current; } finally { if (checkpointPromises.get(userId) === current) checkpointPromises.delete(userId); }
  }
  // --- Lifecycle hooks ---
  // On session destroying (pre-close): checkpoint while context is still alive
  events.on('session:destroying', async ({ userId, reason }) => {
    const context = activeSessions.get(userId);
    if (context) {
      if (reason !== 'storage_reset') {
        await checkpoint(userId, context, reason).catch(() => {});
      }
      activeSessions.delete(userId);
    }
  });
  // On session destroyed (post-close): cleanup tracking if not already done
  events.on('session:destroyed', async () => {});
  // On shutdown: checkpoint all remaining sessions
  events.on('server:shutdown', async () => {
    for (const [userId, context] of activeSessions) {
      await checkpoint(userId, context, 'shutdown').catch(() => {});
    }
    activeSessions.clear();
  });
}
`;
}

function serverFixture() {
  return `const SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1;
const pluginCtx = {
};
async function closeSession(userId, session, { reason = 'session_closed', clearDownloads = true, clearLocks = true } = {}) {
  if (!session) return;
  if (!spartanBrowserLifecycle.hasLease()) {
    const closing = spartanBrowserLifecycle.run('session_close', () => closeSession(userId, session, { reason, clearDownloads, clearLocks }), { priority: 'background', waitTimeoutMs: null });
    session._spartanClosePromise = closing;
    return await closing;
  }
  const key = normalizeUserId(userId);

  // Drain locks BEFORE closing context
}
async function destroySession(userId, { reason = 'destroy_session' } = {}) {
  const key = normalizeUserId(userId);
  const session = sessions.get(key);
  if (!session) return false;
  log('warn', 'destroying session', { userId: key, reason });
  sessions.delete(key);
  deleteUserNavHealth(key);
  await closeSession(key, session, { reason, clearDownloads: true, clearLocks: true });
}
`;
}

test('durability adapter validates, fsyncs, atomically replaces, and preserves supplied storageState', async () => {
  const patched = patchPersistence(persistenceFixture());
  assert.strictEqual(patchPersistence(patched), patched);
  assert.match(patched, /SPARTAN_PERSISTENCE_DURABILITY_V2/);
  assert.match(patched, /await handle\.sync\(\)/);
  assert.match(patched, /await syncDirectory\(path\.dirname\(targetPath\)\)/);
  assert.match(patched, /indexedDB \? \{ indexedDB: true \}/);

  const temp = await fsp.mkdtemp(path.join(os.tmpdir(), 'spartan-persistence-v2-'));
  try {
    const modulePath = path.join(temp, 'persistence.mjs');
    await fsp.writeFile(modulePath, patched);
    const mod = await import(`${pathToFileURL(modulePath).href}?v=${Date.now()}`);
    let calls = 0;
    const state = { cookies: [], origins: [] };
    const result = await mod.persistStorageState({
      profileDir: temp,
      userId: 'profile-a',
      context: { storageState: async options => { calls++; assert.deepStrictEqual(options, { indexedDB: true }); return state; } },
      indexedDB: true,
    });
    assert.strictEqual(result.persisted, true);
    assert.strictEqual(calls, 1);
    assert.deepStrictEqual(JSON.parse(await fsp.readFile(result.storageStatePath, 'utf8')), state);
    assert.strictEqual((await fsp.stat(result.storageStatePath)).mode & 0o777, 0o600);

    const supplied = { cookies: [{ name: 'x' }], origins: [] };
    const direct = await mod.persistStorageState({ profileDir: temp, userId: 'profile-b', storageState: supplied });
    assert.strictEqual(direct.persisted, true);
    assert.deepStrictEqual(JSON.parse(await fsp.readFile(direct.storageStatePath, 'utf8')), supplied);

    const invalid = await mod.persistStorageState({ profileDir: temp, userId: 'bad', storageState: { cookies: 'bad' } });
    assert.strictEqual(invalid.persisted, false);
  } finally {
    await fsp.rm(temp, { recursive: true, force: true });
  }
});

test('checkpoint API reuses upstream per-user serialization and keeps tracking on failure', () => {
  const patched = patchPlugin(pluginFixture());
  assert.strictEqual(patchPlugin(patched), patched);
  assert.match(patched, /SPARTAN_PERSISTENCE_CHECKPOINT_SERVICE_V2/);
  assert.match(patched, /checkpointPromises = new Map/);
  assert.match(patched, /Promise\.race/);
  assert.match(patched, /app\.post\('\/internal\/persistence\/:userId\/checkpoint', ctx\.auth\(\)/);
  assert.match(patched, /Tracking remains live until session:destroyed/);
  assert.match(patched, /shutdown storage checkpoint failed/);
  assert.doesNotMatch(patched, /activeSessions\.clear\(\)/);
  assert.doesNotMatch(patched, /events\.on\('session:destroying'/);
});

test('checkpoint failure remains retryable and prevents session-map deletion', () => {
  const patched = patchServer(serverFixture());
  assert.strictEqual(patchServer(patched), patched);
  assert.match(patched, /SPARTAN_PERSISTENCE_CLOSE_FENCE_V2/);
  assert.match(patched, /persistence_checkpoint_failed/);
  assert.match(patched, /reason: 'plugin_unavailable'/);
  assert.match(patched, /reason: 'checkpoint_error'/);
  assert.match(patched, /typeof checkpointUser === 'function'/);
  assert.match(patched, /persistence: Object\.seal\(\{ checkpointUser: null \}\)/);
  assert.match(patched, /session\._closing = false/);
  assert.match(patched, /session\._spartanClosePromise = null/);
  assert.match(patched, /await closeSession\(key, session/);
  assert.doesNotMatch(patched, /log\('warn', 'destroying session'[\s\S]*sessions\.delete\(key\);/);
});
