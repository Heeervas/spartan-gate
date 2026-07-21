const assert = require('assert');
const fs = require('fs');
const fsp = require('fs/promises');
const os = require('os');
const path = require('path');
const { pathToFileURL } = require('url');
const test = require('node:test');

const patchPath = path.join(__dirname, 'patch-persistence.js');
const { patchPersistence, patchPlugin } = require(patchPath);

test('checkpoint failure remains retryable without degrading browser lifecycle', () => {
  const patchSource = fs.readFileSync(patchPath, 'utf8');
  const replacement = patchSource.split('`    const closing = spartanBrowserLifecycle.run(', 3)[2];
  assert.match(replacement, /persistence_checkpoint_failed/);
  assert.ok(replacement.includes('session._closing = false'));
  assert.ok(replacement.includes('session._spartanClosePromise = null'));
});
function pluginFixture() {
  return `async function persistStorageState() {
  return { persisted: true };
}

async function register(app, ctx, pluginConfig = {}) {
  const { events, config, log } = ctx;
  const profileDir = pluginConfig.profileDir || config.profileDir;
  const logger = { warn: () => {} };
  const activeSessions = new Map();

  async function checkpoint(userId, context, reason) {
    if (!context) return;
    const result = await persistStorageState({ profileDir, userId, context, logger });
    if (result.persisted) {
      log('info', 'storage state persisted', { userId, reason, path: result.storageStatePath });
    }
    return result;
  }

  // On session destroying (pre-close): checkpoint while context is still alive
  events.on('session:destroying', async ({ userId, reason }) => {
    const context = activeSessions.get(userId);
    if (context) {
      await checkpoint(userId, context, reason).catch(() => {});
      activeSessions.delete(userId);
    }
  });
}
`;
}

test('checkpoint route registers with authentication from plugin context', async () => {
  const patched = patchPlugin(pluginFixture());
  assert.strictEqual(patchPlugin(patched), patched, 'plugin patch must be idempotent');
  const register = Function(`${patched}; return register;`)();
  const middleware = () => {};
  const routes = [];
  let authCalls = 0;
  const ctx = {
    events: { on() {} },
    config: { profileDir: '/tmp/profile' },
    log() {},
    auth() {
      authCalls += 1;
      return middleware;
    },
  };
  await register({ post(...args) { routes.push(args); } }, ctx);

  assert.strictEqual(authCalls, 1);
  assert.strictEqual(routes.length, 1);
  assert.strictEqual(routes[0][0], '/internal/persistence/:userId/checkpoint');
  assert.strictEqual(routes[0][1], middleware);
  assert.match(patched, /metadataPersisted: result\.persisted === true/);
});


function upstreamPersistenceFunction() {
  const patchSource = fs.readFileSync(patchPath, 'utf8');
  const startMarker = '  const oldFunction = `';
  const endMarker = '`;\n\n  const newFunction = `';
  const start = patchSource.indexOf(startMarker);
  const end = patchSource.indexOf(endMarker, start + startMarker.length);
  assert.notStrictEqual(start, -1);
  assert.notStrictEqual(end, -1);
  const encoded = patchSource.slice(start + startMarker.length, end);
  return Function(`return \`${encoded}\`;`)();
}

function persistenceFixture() {
  return `import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

function getUserPersistencePaths(profileDir, userId) {
  const rootDir = path.resolve(profileDir);
  const safeUserDir = crypto.createHash('sha256').update(String(userId)).digest('hex').slice(0, 32);
  const userDir = path.join(rootDir, safeUserDir);
  return {
    rootDir,
    userDir,
    storageStatePath: path.join(userDir, 'storage-state.json'),
    metaPath: path.join(userDir, 'meta.json'),
  };
}

${upstreamPersistenceFunction()}

export { getUserPersistencePaths, persistStorageState };
`;
}

async function loadPatchedModule(tmp) {
  const source = patchPersistence(persistenceFixture());
  assert.strictEqual(patchPersistence(source), source, 'patch must be idempotent');
  const modulePath = path.join(tmp, 'persistence.mjs');
  await fsp.writeFile(modulePath, source);
  return await import(`${pathToFileURL(modulePath).href}?v=${Date.now()}`);
}

test('storage state is validated, fsynced, and atomically replaces authoritative state', async () => {
  const tmp = await fsp.mkdtemp(path.join(os.tmpdir(), 'spartan-persistence-'));
  try {
    const persistence = await loadPatchedModule(tmp);
    let receivedArgs = 'not-called';
    const state = {
      cookies: [{ name: 'sid', value: 'new', domain: '.example.com', path: '/' }],
      origins: [{ origin: 'https://example.com', localStorage: [] }],
    };
    const result = await persistence.persistStorageState({
      profileDir: tmp,
      userId: 'profile-a',
      context: {
        async storageState(args) {
          receivedArgs = args;
          return state;
        },
      },
      timeoutMs: 1000,
    });

    assert.strictEqual(result.persisted, true);
    assert.strictEqual(result.metadataPersisted, true);
    assert.strictEqual(receivedArgs, undefined, 'Playwright must not own the target path');
    assert.deepStrictEqual(
      JSON.parse(await fsp.readFile(result.storageStatePath, 'utf8')),
      state,
    );
    assert.strictEqual((await fsp.stat(result.storageStatePath)).mode & 0o777, 0o600);

    const invalid = await persistence.persistStorageState({
      profileDir: tmp,
      userId: 'profile-a',
      context: { async storageState() { return { cookies: 'invalid' }; } },
      timeoutMs: 1000,
    });
    assert.strictEqual(invalid.persisted, false);
    assert.deepStrictEqual(
      JSON.parse(await fsp.readFile(result.storageStatePath, 'utf8')),
      state,
      'invalid generation must not replace authoritative state',
    );
  } finally {
    await fsp.rm(tmp, { recursive: true, force: true });
  }
});

test('timed-out storageState stays single-flight and fenced until it settles', async () => {
  const tmp = await fsp.mkdtemp(path.join(os.tmpdir(), 'spartan-persistence-flight-'));
  try {
    const persistence = await loadPatchedModule(tmp);
    let release;
    let calls = 0;
    const pending = new Promise(resolve => { release = resolve; });
    const context = {
      async storageState() {
        calls += 1;
        return await pending;
      },
    };

    const first = await persistence.persistStorageState({
      profileDir: tmp,
      userId: 'profile-b',
      context,
      timeoutMs: 10,
    });
    const second = await persistence.persistStorageState({
      profileDir: tmp,
      userId: 'profile-b',
      context,
      timeoutMs: 10,
    });
    assert.strictEqual(first.reason, 'timeout');
    assert.strictEqual(second.reason, 'timeout');
    assert.strictEqual(first.generation, second.generation);
    assert.strictEqual(calls, 1, 'a timeout must not start a competing storageState call');

    release({ cookies: [], origins: [] });
    await new Promise(resolve => setTimeout(resolve, 30));
    const paths = persistence.getUserPersistencePaths(tmp, 'profile-b');
    assert.deepStrictEqual(
      JSON.parse(await fsp.readFile(paths.storageStatePath, 'utf8')),
      { cookies: [], origins: [] },
    );
  } finally {
    await fsp.rm(tmp, { recursive: true, force: true });
  }
});
