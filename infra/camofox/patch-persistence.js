const fs = require('fs');
const { spawnSync } = require('child_process');

const persistencePath = process.argv[2] || 'lib/persistence.js';
const pluginPath = process.argv[3] || 'plugins/persistence/index.js';
const serverPath = process.argv[4] || 'server.js';

function replaceOnce(source, from, to, label) {
  const count = source.split(from).length - 1;
  if (count !== 1) throw new Error(`${label}: expected one anchor, found ${count}`);
  return source.replace(from, to);
}

function patchPersistence(source) {
  if (source.includes('SPARTAN_PERSISTENCE_DURABILITY_V2')) {
    for (const marker of ['validStorageState', "fs.open(tmpPath, 'wx', 0o600)", 'await syncDirectory(path.dirname(targetPath))']) {
      if (!source.includes(marker)) throw new Error(`persistence durability patch is incomplete: ${marker}`);
    }
    return source;
  }
  if (!source.includes('  storageState,') || !source.includes('  indexedDB = false,')) {
    throw new Error('unsupported Camofox persistence source; expected pinned 1.15 storageState/indexedDB support');
  }
  const start = source.indexOf('async function persistStorageState({');
  const end = source.indexOf('\n}\n\nexport {', start);
  if (start === -1 || end === -1) throw new Error('persistence implementation anchors not found');
  const replacement = `// SPARTAN_PERSISTENCE_DURABILITY_V2
function validStorageState(value) {
  return !!value && typeof value === 'object' && Array.isArray(value.cookies)
    && (value.origins === undefined || Array.isArray(value.origins));
}

async function syncDirectory(directory) {
  const handle = await fs.open(directory, 'r');
  try { await handle.sync(); } finally { await handle.close(); }
}

async function atomicWriteJson(targetPath, value) {
  const suffix = \`.tmp-\${process.pid}-\${crypto.randomBytes(8).toString('hex')}\`;
  const tmpPath = \`\${targetPath}\${suffix}\`;
  let handle;
  try {
    handle = await fs.open(tmpPath, 'wx', 0o600);
    await handle.writeFile(JSON.stringify(value, null, 2));
    await handle.sync();
    await handle.close();
    handle = undefined;
    await fs.rename(tmpPath, targetPath);
    await syncDirectory(path.dirname(targetPath));
  } catch (error) {
    await handle?.close().catch(() => {});
    await fs.unlink(tmpPath).catch(() => {});
    throw error;
  }
}

async function persistStorageState({ profileDir, userId, context, storageState, logger = console, indexedDB = false }) {
  if (!profileDir || (!context && !storageState)) return { persisted: false, reason: 'disabled' };
  const { userDir, storageStatePath, metaPath } = getUserPersistencePaths(profileDir, userId);
  try {
    await fs.mkdir(userDir, { recursive: true });
    const captured = storageState || await context.storageState(indexedDB ? { indexedDB: true } : undefined);
    if (!validStorageState(captured)) throw new Error('browser returned invalid storage state');
    await atomicWriteJson(storageStatePath, captured);
    let metadataPersisted = true;
    try {
      await atomicWriteJson(metaPath, { userId: String(userId), updatedAt: new Date().toISOString(), storageStatePath });
    } catch (error) {
      metadataPersisted = false;
      logger?.warn?.('failed to persist non-authoritative storage metadata', { userId: String(userId), error: error?.message || String(error) });
    }
    return { persisted: true, userDir, storageStatePath, metaPath, metadataPersisted };
  } catch (error) {
    logger?.warn?.('failed to persist storage state', { userId: String(userId), storageStatePath, error: error?.message || String(error) });
    return { persisted: false, reason: 'error', error };
  }
}`;
  return source.slice(0, start) + replacement + source.slice(end + 2);
}

function patchPlugin(source) {
  if (source.includes('SPARTAN_PERSISTENCE_CHECKPOINT_SERVICE_V2')) {
    if (!source.includes('shutdown storage checkpoint failed') || source.includes('activeSessions.clear();')) {
      throw new Error('persistence checkpoint service is incomplete');
    }
    return source;
  }
  if (!source.includes('const checkpointPromises = new Map();') || !source.includes('resettingUsers')) {
    throw new Error('unsupported Camofox persistence plugin; expected pinned 1.15 checkpoint serialization');
  }
  const lifecycleMarker = '  // --- Lifecycle hooks ---';
  const service = `  // SPARTAN_PERSISTENCE_CHECKPOINT_SERVICE_V2
  async function checkpointUser(userId, reason = 'explicit_checkpoint', timeoutMs = 8000) {
    const key = String(userId);
    const context = activeSessions.get(key);
    if (!context) return { persisted: false, reason: 'inactive' };
    let timer;
    try {
      return await Promise.race([
        checkpoint(key, context, reason),
        new Promise(resolve => {
          timer = setTimeout(() => resolve({ persisted: false, reason: 'timeout', inFlight: true }), timeoutMs);
          timer.unref?.();
        }),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  ctx.persistence = Object.freeze({ checkpointUser });
  app.post('/internal/persistence/:userId/checkpoint', ctx.auth(), async (req, res) => {
    const result = await checkpointUser(req.params.userId, 'operator_checkpoint', 8000);
    const payload = {
      persisted: result?.persisted === true,
      reason: result?.reason || null,
      inFlight: result?.inFlight === true,
      metadataPersisted: result?.persisted === true && result?.metadataPersisted !== false,
    };
    res.status(payload.persisted ? 200 : 503).json(payload);
  });

`;
  if (!source.includes(lifecycleMarker)) throw new Error('persistence lifecycle marker not found');
  source = source.replace(lifecycleMarker, service + lifecycleMarker);
  const destroyingStart = source.indexOf('  // On session destroying (pre-close): checkpoint while context is still alive');
  const shutdownListener = `  // On shutdown: checkpoint all remaining sessions
  events.on('server:shutdown', async () => {
    for (const [userId, context] of activeSessions) {
      await checkpoint(userId, context, 'shutdown').catch(() => {});
    }
    activeSessions.clear();
  });`;
  const hardenedShutdownListener = `  // On shutdown: every checkpoint must succeed before contexts may close.
  // Keep tracking until session:destroyed so closeSession can verify again.
  events.on('server:shutdown', async () => {
    for (const [userId, context] of activeSessions) {
      const result = await checkpoint(userId, context, 'shutdown');
      if (!result?.persisted) throw new Error('shutdown storage checkpoint failed');
    }
  });`;
  source = replaceOnce(source, shutdownListener, hardenedShutdownListener, 'shutdown checkpoint fence');
  const destroyingEnd = source.indexOf('  // On session destroyed (post-close): cleanup tracking if not already done', destroyingStart);
  if (destroyingStart === -1 || destroyingEnd === -1) throw new Error('destroying listener anchors not found');
  const replacement = `  // Core closeSession owns the fail-closed pre-close checkpoint.
  // Tracking remains live until session:destroyed so failures can be retried.

`;
  return source.slice(0, destroyingStart) + replacement + source.slice(destroyingEnd);
}

function patchServer(source) {
  if (source.includes('SPARTAN_PERSISTENCE_CLOSE_FENCE_V2')) {
    for (const marker of ["reason: 'plugin_unavailable'", "reason: 'checkpoint_error'", 'persistence_checkpoint_failed']) {
      if (!source.includes(marker)) throw new Error(`persistence close fence is incomplete: ${marker}`);
    }
    return source;
  }
  if (!source.includes('SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1')) {
    throw new Error('persistence close fence requires the pinned Camofox 1.15 server patch');
  }
  source = replaceOnce(
    source,
    `    const closing = spartanBrowserLifecycle.run('session_close', () => closeSession(userId, session, { reason, clearDownloads, clearLocks }), { priority: 'background', waitTimeoutMs: null });
    session._spartanClosePromise = closing;
    return await closing;`,
    `    const closing = spartanBrowserLifecycle.run('session_close', () => closeSession(userId, session, { reason, clearDownloads, clearLocks }), { priority: 'background', waitTimeoutMs: null })
      .catch(error => {
        const expected = error?.code === 'browser_lifecycle_invalidated'
          || error?.code === 'browser_lifecycle_recovering'
          || error?.code === 'persistence_checkpoint_failed';
        if (!expected) spartanBrowserLifecycle.markDegraded('session_close_failed');
        throw error;
      })
      .finally(() => {
        if (!session._spartanCloseComplete) {
          session._closing = false;
          session._spartanClosePromise = null;
        }
      });
    session._spartanClosePromise = closing;
    return await closing;`,
    'close promise retry fence',
  );
  source = replaceOnce(
    source,
    `  const key = normalizeUserId(userId);

  // Drain locks BEFORE closing context`,
    `  const key = normalizeUserId(userId);

  // SPARTAN_PERSISTENCE_CLOSE_FENCE_V2
  if (reason !== 'storage_reset') {
    const checkpointUser = pluginCtx.persistence?.checkpointUser;
    let checkpoint;
    try {
      checkpoint = typeof checkpointUser === 'function'
        ? await checkpointUser(key, reason, 8000)
        : { persisted: false, reason: 'plugin_unavailable' };
    } catch (checkpointError) {
      checkpoint = { persisted: false, reason: 'checkpoint_error', error: checkpointError };
    }
    if (!checkpoint?.persisted) {
      session._closing = false;
      session._spartanClosePromise = null;
      const error = spartanBrowserLifecycleError('persistence_checkpoint_failed', 'browser storage checkpoint failed; live context retained');
      error.statusCode = 503;
      error.checkpointReason = checkpoint?.reason || 'unknown';
      throw error;
    }
  }

  // Drain locks BEFORE closing context`,
    'pre-close checkpoint fence',
  );
  return replaceOnce(
    source,
    `  log('warn', 'destroying session', { userId: key, reason });
  sessions.delete(key);
  deleteUserNavHealth(key);
  await closeSession(key, session, { reason, clearDownloads: true, clearLocks: true });`,
    `  log('warn', 'destroying session', { userId: key, reason });
  await closeSession(key, session, { reason, clearDownloads: true, clearLocks: true });
  deleteUserNavHealth(key);`,
    'dead session retention',
  );
}

if (require.main === module) {
  const originals = [
    [persistencePath, fs.readFileSync(persistencePath, 'utf8')],
    [pluginPath, fs.readFileSync(pluginPath, 'utf8')],
    [serverPath, fs.readFileSync(serverPath, 'utf8')],
  ];
  const patched = [
    [persistencePath, patchPersistence(originals[0][1])],
    [pluginPath, patchPlugin(originals[1][1])],
    [serverPath, patchServer(originals[2][1])],
  ];
  const staged = [];
  try {
    for (const [target, contents] of patched) {
      const tmpPath = `${target}.spartan-tmp.js`;
      staged.push([target, tmpPath]);
      fs.writeFileSync(tmpPath, contents);
      const checked = spawnSync(process.execPath, ['--check', tmpPath], { encoding: 'utf8' });
      if (checked.status !== 0) throw new Error(`${target}: patched syntax check failed: ${checked.stderr || checked.stdout}`);
    }
    for (const [target, tmpPath] of staged) fs.renameSync(tmpPath, target);
  } finally {
    for (const [, tmpPath] of staged) fs.rmSync(tmpPath, { force: true });
  }
}

module.exports = { patchPersistence, patchPlugin, patchServer };
