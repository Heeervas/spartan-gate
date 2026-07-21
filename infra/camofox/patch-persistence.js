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
  if (source.includes('SPARTAN_PERSISTENCE_SINGLE_FLIGHT_V1')) return source;

  const oldFunction = `async function persistStorageState({ profileDir, userId, context, logger = console }) {
  if (!profileDir || !context) {
    return { persisted: false, reason: 'disabled' };
  }

  const { userDir, storageStatePath, metaPath } = getUserPersistencePaths(profileDir, userId);
  const suffix = \`.tmp-\${process.pid}-\${Date.now()}\`;
  const tmpStoragePath = \`\${storageStatePath}\${suffix}\`;
  const tmpMetaPath = \`\${metaPath}\${suffix}\`;

  try {
    await fs.mkdir(userDir, { recursive: true });
    await context.storageState({ path: tmpStoragePath });
    await fs.rename(tmpStoragePath, storageStatePath);
    await fs.writeFile(
      tmpMetaPath,
      JSON.stringify(
        {
          userId: String(userId),
          updatedAt: new Date().toISOString(),
          storageStatePath,
        },
        null,
        2
      )
    );
    await fs.rename(tmpMetaPath, metaPath);
    return { persisted: true, userDir, storageStatePath, metaPath };
  } catch (err) {
    await fs.unlink(tmpStoragePath).catch(() => {});
    await fs.unlink(tmpMetaPath).catch(() => {});
    logger?.warn?.('failed to persist storage state', {
      userId: String(userId),
      storageStatePath,
      error: err?.message || String(err),
    });
    return { persisted: false, reason: 'error', error: err };
  }
}`;

  const newFunction = `// SPARTAN_PERSISTENCE_SINGLE_FLIGHT_V1
const persistenceFlights = new Map();
let persistenceGeneration = 0;

function validStorageState(value) {
  return !!value
    && typeof value === 'object'
    && Array.isArray(value.cookies)
    && (value.origins === undefined || Array.isArray(value.origins));
}

async function syncDirectory(directory) {
  const handle = await fs.open(directory, 'r');
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function atomicWriteJson(targetPath, value, generation) {
  const suffix = \`.tmp-\${process.pid}-\${generation}-\${crypto.randomBytes(8).toString('hex')}\`;
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
  } catch (err) {
    await handle?.close().catch(() => {});
    await fs.unlink(tmpPath).catch(() => {});
    throw err;
  }
}

async function persistGeneration({ profileDir, userId, context, logger, generation }) {
  const { userDir, storageStatePath, metaPath } = getUserPersistencePaths(profileDir, userId);
  try {
    await fs.mkdir(userDir, { recursive: true });
    const storageState = await context.storageState();
    if (!validStorageState(storageState)) {
      throw new Error('browser returned invalid storage state');
    }
    await atomicWriteJson(storageStatePath, storageState, generation);

    let metadataPersisted = true;
    try {
      await atomicWriteJson(metaPath, {
        userId: String(userId),
        updatedAt: new Date().toISOString(),
        storageStatePath,
        generation,
      }, generation);
    } catch (err) {
      metadataPersisted = false;
      logger?.warn?.('failed to persist non-authoritative storage metadata', {
        userId: String(userId),
        error: err?.message || String(err),
      });
    }
    return { persisted: true, userDir, storageStatePath, metaPath, metadataPersisted, generation };
  } catch (err) {
    logger?.warn?.('failed to persist storage state', {
      userId: String(userId),
      storageStatePath,
      error: err?.message || String(err),
    });
    return { persisted: false, reason: 'error', error: err, generation };
  }
}

async function waitForFlight(flight, timeoutMs) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return await flight.promise;
  let timer;
  try {
    return await Promise.race([
      flight.promise,
      new Promise(resolve => {
        timer = setTimeout(() => resolve({
          persisted: false,
          reason: 'timeout',
          generation: flight.generation,
          inFlight: true,
        }), timeoutMs);
        timer.unref?.();
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function persistStorageState({
  profileDir,
  userId,
  context,
  logger = console,
  timeoutMs = 8000,
}) {
  if (!profileDir || !context) {
    return { persisted: false, reason: 'disabled' };
  }

  const key = String(userId);
  let flight = persistenceFlights.get(key);
  if (!flight) {
    const generation = ++persistenceGeneration;
    flight = {
      generation,
      promise: persistGeneration({ profileDir, userId: key, context, logger, generation }),
    };
    persistenceFlights.set(key, flight);
    void flight.promise.finally(() => {
      if (persistenceFlights.get(key) === flight) persistenceFlights.delete(key);
    });
  }
  return await waitForFlight(flight, timeoutMs);
}`;

  return replaceOnce(source, oldFunction, newFunction, 'persistence implementation');
}

function patchPlugin(source) {
  if (source.includes('SPARTAN_PERSISTENCE_CHECKPOINT_SERVICE_V1')) return source;
  source = replaceOnce(
    source,
    '  const { events, config, log } = ctx;\n',
    '  const { events, config, log, auth } = ctx;\n',
    'plugin auth middleware',
  );


  source = replaceOnce(
    source,
    `  async function checkpoint(userId, context, reason) {
    if (!context) return;
    const result = await persistStorageState({ profileDir, userId, context, logger });
    if (result.persisted) {
      log('info', 'storage state persisted', { userId, reason, path: result.storageStatePath });
    }
    return result;
  }
`,
    `  async function checkpoint(userId, context, reason, timeoutMs = 8000) {
    if (!context) return { persisted: false, reason: 'inactive' };
    const result = await persistStorageState({ profileDir, userId, context, logger, timeoutMs });
    if (result.persisted) {
      log('info', 'storage state persisted', { userId, reason, path: result.storageStatePath });
    }
    return result;
  }

  // SPARTAN_PERSISTENCE_CHECKPOINT_SERVICE_V1
  async function checkpointUser(userId, reason = 'explicit_checkpoint', timeoutMs = 8000) {
    const key = String(userId);
    return await checkpoint(key, activeSessions.get(key), reason, timeoutMs);
  }

  ctx.persistence = Object.freeze({ checkpointUser });

  app.post('/internal/persistence/:userId/checkpoint', auth(), async (req, res) => {
    const result = await checkpointUser(req.params.userId, 'operator_checkpoint', 8000);
    const payload = {
      persisted: result.persisted === true,
      reason: result.reason || null,
      generation: result.generation || null,
      inFlight: result.inFlight === true,
      metadataPersisted: result.persisted === true && result.metadataPersisted !== false,
    };
    res.status(payload.persisted ? 200 : 503).json(payload);
  });
`,
    'checkpoint service',
  );

  source = replaceOnce(
    source,
    `  // On session destroying (pre-close): checkpoint while context is still alive
  events.on('session:destroying', async ({ userId, reason }) => {
    const context = activeSessions.get(userId);
    if (context) {
      await checkpoint(userId, context, reason).catch(() => {});
      activeSessions.delete(userId);
    }
  });
`,
    `  // Core closeSession owns the pre-close checkpoint. Keep tracking until
  // session:destroyed so a failed checkpoint leaves the live context retryable.
`,
    'destroying listener',
  );
  return source;
}

function patchServer(source) {
  if (source.includes('SPARTAN_PERSISTENCE_CLOSE_FENCE_V1')) return source;

  source = replaceOnce(
    source,
    `    const closing = spartanBrowserLifecycle.run(
      'session_close',
      async () => {
        if (session._spartanCloseComplete) return;
        return await closeSession(userId, session, { reason, clearDownloads, clearLocks });
      },
      { priority: 'background', waitTimeoutMs: null },
    ).catch(error => {
      const expectedRecoveryAbort = error?.code === 'browser_lifecycle_invalidated'
        || error?.code === 'browser_lifecycle_recovering';
      if (!expectedRecoveryAbort) {
        spartanBrowserLifecycle.markDegraded('session_close_failed');
      }
      throw error;
    });
    session._spartanClosePromise = closing;
    return await closing;
`,
    `    const closing = spartanBrowserLifecycle.run(
      'session_close',
      async () => {
        if (session._spartanCloseComplete) return;
        return await closeSession(userId, session, { reason, clearDownloads, clearLocks });
      },
      { priority: 'background', waitTimeoutMs: null },
    ).catch(error => {
      const expectedRecoveryAbort = error?.code === 'browser_lifecycle_invalidated'
        || error?.code === 'browser_lifecycle_recovering'
        || error?.code === 'persistence_checkpoint_failed';
      if (!expectedRecoveryAbort) {
        spartanBrowserLifecycle.markDegraded('session_close_failed');
      }
      throw error;
    }).finally(() => {
      if (!session._spartanCloseComplete) {
        session._closing = false;
        session._spartanClosePromise = null;
      }
    });
    session._spartanClosePromise = closing;
    return await closing;
`,
    'close promise retry fence',
  );

  source = replaceOnce(
    source,
    `  const key = normalizeUserId(userId);

  // Drain locks BEFORE closing context — queued operations get clean "Tab destroyed"
`,
    `  const key = normalizeUserId(userId);

  // SPARTAN_PERSISTENCE_CLOSE_FENCE_V1: an ordinary context close is allowed
  // only after the authoritative storage state has reached durable storage.
  if (pluginCtx.persistence?.checkpointUser) {
    const checkpoint = await pluginCtx.persistence.checkpointUser(key, reason, 8000);
    if (!checkpoint?.persisted) {
      session._closing = false;
      session._spartanClosePromise = null;
      const error = spartanBrowserLifecycleError(
        'persistence_checkpoint_failed',
        'browser storage checkpoint failed; live context retained',
      );
      error.statusCode = 503;
      error.checkpointReason = checkpoint?.reason || 'unknown';
      throw error;
    }
  }

  // Drain locks BEFORE closing context — queued operations get clean "Tab destroyed"
`,
    'pre-close checkpoint fence',
  );

  source = replaceOnce(
    source,
    `  log('warn', 'destroying dead session', { userId: key });
  sessions.delete(key);
  closeSession(key, session, { reason: 'destroy_session', clearDownloads: true, clearLocks: true }).catch(() => {});
`,
    `  log('warn', 'destroying dead session', { userId: key });
  closeSession(key, session, { reason: 'destroy_session', clearDownloads: true, clearLocks: true }).catch(() => {});
`,
    'dead session retention',
  );
  return source;
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

// Complete all anchor validation before the first target-file write.
const staged = [];
try {
  for (const [path, source] of patched) {
    const tmpPath = `${path}.spartan-tmp.js`;
    staged.push([path, tmpPath]);
    fs.writeFileSync(tmpPath, source);
    const checked = spawnSync(process.execPath, ['--check', tmpPath], { encoding: 'utf8' });
    if (checked.status !== 0) {
      throw new Error(`${path}: patched syntax check failed: ${checked.stderr || checked.stdout}`);
    }
  }
  for (const [path, tmpPath] of staged) fs.renameSync(tmpPath, path);
} finally {
  for (const [, tmpPath] of staged) fs.rmSync(tmpPath, { force: true });
}

}

module.exports = { patchPersistence, patchPlugin, patchServer };
