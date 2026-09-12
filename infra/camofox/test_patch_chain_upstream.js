const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const test = require('node:test');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const upstreamSource = process.env.CAMOFOX_UPSTREAM_SOURCE;
const patchers = [
  ['patch-server.js'],
  ['patch-vnc-readiness.js'],
  ['patch-log-redaction.js'],
  ['patch-persistence.js', 'lib/persistence.js', 'plugins/persistence/index.js', 'server.js'],
  ['patch-operator-lease.js'],
  ['patch-operator-lease-api.js'],
  ['patch-memory-pressure.js'],
];

function digest(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function runPatcher(cwd, entry) {
  const script = path.join(__dirname, entry[0]);
  const result = spawnSync(process.execPath, [script, ...entry.slice(1)], { cwd, encoding: 'utf8' });
  assert.strictEqual(result.status, 0, `${entry[0]} failed:\n${result.stderr || result.stdout}`);
}

function verifyTimeoutIsolation(server) {
  const start = server.indexOf("  const NAVIGATION_TIMEOUT_ACTIONS = new Set(");
  const end = server.indexOf('  // Track consecutive timeouts per tab', start);
  assert.ok(start >= 0 && end > start);
  // Execute the actual transformed branch, not a second implementation of it.
  const branch = server.slice(start, end);
  for (const action of ['click', 'navigate', 'open_url']) {
    for (const canRotateSessions of [false, true]) {
      const user = { tabs: new Map([['stuck', {}], ['neighbor', {}]]) };
      const other = { tabs: new Map([['other', {}]]) };
      const sessions = new Map([['user', user], ['other', other]]);
      let failures = 0;
      vm.runInNewContext(branch, {
        err: new Error('timeout'), action, userId: 'user',
        req: { params: { tabId: 'stuck' } }, sessions,
        proxyPool: { canRotateSessions }, isTimeoutError: () => true,
        normalizeUserId: String, log() {},
        browserRestartsTotal: { labels: () => ({ inc() {} }) },
        recordNavFailure: () => ++failures,
        destroySession: async id => sessions.delete(id),
        destroyTab: (session, tabId) => session.tabs.delete(tabId),
      });
      assert.equal(failures, 1, 'a timeout must be counted exactly once');
      assert.equal(sessions.get('other'), other, 'unrelated user survives');
      assert.equal(sessions.has('user'), !canRotateSessions);
      if (!canRotateSessions) {
        assert.equal(user.tabs.has('stuck'), false, 'retire timed-out tab');
        assert.equal(user.tabs.has('neighbor'), true, 'preserve neighboring tab');
      }
    }
  }
  for (const reason of ['navigate_failure', 'tab_create_nav_failure']) {
    assert.ok(server.includes("if (!isTimeoutError(navErr) && recordNavFailure(userId)) {\n              await recoverUserSession(userId, '" + reason + "');"),
      'route-level accounting must not count the timeout a second time');
  }
}

async function verifyLaunchLateCleanup(server) {
  const start = server.indexOf('let spartanLaunchAttemptSequence = 0;');
  const end = server.indexOf('function spartanCaptureLaunchIdentity(', start);
  assert.ok(start >= 0 && end > start, 'generated launch attempt helpers exist');
  const helpers = server.slice(start, end);
  const context = {
    Promise, Set, Date,
    withTimeout: async (promise, ms, label) => await Promise.race([
      promise,
      new Promise((_, reject) => setTimeout(() => reject(new Error(label + ' timed out')), ms)),
    ]),
    setTimeout,
  };
  vm.runInNewContext(`${helpers}
    this.runResource = (attempt, start, dispose) => {
      spartanCurrentLaunchAttempt = attempt;
      return spartanLaunchResource(start, attempt, 'synthetic late resource', dispose);
    };
    this.runStep = (attempt, start) => {
      spartanCurrentLaunchAttempt = attempt;
      return spartanLaunchStep(start, attempt, 'synthetic expired step');
    };
    this.drainAttempt = spartanDrainLaunchAttempt;`, context);
  let started = 0;
  const expired = { active: true, deadlineAt: Date.now() - 1, pending: new Set(), cleanups: new Set(), cleanupErrors: [] };
  await assert.rejects(context.runStep(expired, () => { started++; }), /expired/);
  assert.equal(started, 0, 'expired thunk must not start');

  let disposed = 0;
  const attempt = { active: true, deadlineAt: Date.now() + 10, pending: new Set(), cleanups: new Set(), cleanupErrors: [] };
  await assert.rejects(context.runResource(attempt,
    () => new Promise(resolve => setTimeout(() => resolve({ close: () => { disposed++; } }), 35)),
    resource => resource.close()), /timed out/);
  attempt.active = false;
  await context.drainAttempt(attempt);
  assert.equal(disposed, 1, 'late resource must be disposed exactly once before attempt drain settles');
  assert.equal(attempt.pending.size, 0);
  assert.equal(attempt.cleanups.size, 0);
  const failedCleanup = { active: true, deadlineAt: Date.now() + 10, pending: new Set(), cleanups: new Set(), cleanupErrors: [] };
  await assert.rejects(context.runResource(failedCleanup,
    () => new Promise(resolve => setTimeout(() => resolve({}), 30)),
    () => { throw new Error('synthetic disposer failure'); }), /timed out/);
  failedCleanup.active = false;
  await assert.rejects(context.drainAttempt(failedCleanup), /browser launch resource cleanup failed/);
  assert.equal(failedCleanup.cleanupErrors.length, 1, 'cleanup rejection remains fail-closed after settlement');
  assert.match(server, /if \(browserLaunchPromise === spartanPublishedLaunchPromise\) browserLaunchPromise = null/,
    'single-flight remains published until the underlying attempt and disposer drain settle');
  assert.match(server, /spartanBrowserLifecycle\.markDegraded\('browser_launch_cleanup_failed'\)/,
    'unproven cleanup degrades lifecycle before single-flight can clear');
  assert.doesNotMatch(server, /proxy probe context cleanup'\)\.catch/,
    'proxy helper context cleanup failure prevents browser acceptance');
}

function generatedDeepHealthExclusive(server, context) {
  const start = server.indexOf('  runExclusive:');
  const end = server.indexOf('\n  },\n});', start);
  assert.ok(start >= 0 && end > start, 'generated deep-health runExclusive exists');
  const property = server.slice(start, end + '\n  }'.length);
  return vm.runInNewContext(`({${property}}).runExclusive`, context);
}

async function verifyDeepHealthReservation(server, lifecycleModule) {
  const makeExclusive = coordinator => generatedDeepHealthExclusive(server, {
    spartanBrowserLifecycle: coordinator,
    spartanBrowserLifecycleError: lifecycleModule.spartanBrowserLifecycleError,
    SPARTAN_DEEP_PROBE_TIMEOUT_MS: 1050,
  });

  const coordinator = lifecycleModule.createSpartanBrowserLifecycleCoordinator({ maxQueued: 2 });
  let releaseOwner;
  const ownerGate = new Promise(resolve => { releaseOwner = resolve; });
  const owner = coordinator.run('tab_create', async () => { await ownerGate; });
  await new Promise(resolve => setImmediate(resolve));
  const order = [];
  const foreground = coordinator.run('queued_create', async () => { order.push('foreground'); }, {
    priority: 'foreground', waitTimeoutMs: null,
  });
  const health = makeExclusive(coordinator)(async () => {
    assert.equal(coordinator.snapshot().ownerKind, 'deep_probe');
    await coordinator.run('browser_ensure', async () => {
      assert.equal(coordinator.snapshot().ownerKind, 'deep_probe', 'nested ensure reuses the health lease');
      order.push('health');
    });
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(coordinator.snapshot().queued.health, 1, 'health must use its reserved queue slot');
  releaseOwner();
  await Promise.all([owner, health, foreground]);
  assert.deepEqual(order, ['health', 'foreground'], 'health runs before queued foreground work');

  const blocked = lifecycleModule.createSpartanBrowserLifecycleCoordinator();
  let releaseBlocked;
  const blockedGate = new Promise(resolve => { releaseBlocked = resolve; });
  const blockingOwner = blocked.run('tab_create', async () => { await blockedGate; });
  await new Promise(resolve => setImmediate(resolve));
  let probeRan = false;
  await assert.rejects(
    makeExclusive(blocked)(async () => { probeRan = true; }),
    error => error?.code === 'browser_lifecycle_busy',
    'an expired health admission maps to cached-busy semantics',
  );
  assert.equal(probeRan, false);
  assert.equal(blocked.snapshot().queued.health, 0, 'expired health waiter is removed');
  releaseBlocked();
  await blockingOwner;
}

test('ordered patch chain transforms pristine pinned Camofox source idempotently', {
  skip: upstreamSource ? false : 'set CAMOFOX_UPSTREAM_SOURCE to an extracted pristine v1.15.0 tree',
}, async () => {
  const source = path.resolve(upstreamSource);
  assert.ok(fs.existsSync(path.join(source, 'server.js')), 'upstream source must contain server.js');
  assert.equal(JSON.parse(fs.readFileSync(path.join(source, 'package.json'))).version, '1.15.0');
  for (const [file, expected] of Object.entries({
    'server.js': '02c7e55032b07c0ffcb17fce7d2cd13c1abd29470f0aea38dec7f2c75224099b',
    'lib/config.js': 'e6380bbbc26a0a1352eb57454a8e3e838deb4acc4d997cf7c67b64f36b9c2b1c',
    'lib/persistence.js': 'd9f9431644aea772bb0b870a4cf8907d9e9909e399667635a4f33abdb797e328',
    'plugins/persistence/index.js': '47724352f30a46ab7bda98533ff8228fe64d173995b97425fca62bf32c56e3fa',
  })) assert.equal(digest(path.join(source, file)), expected, `${file} must match the pinned release`);
  const config = fs.readFileSync(path.join(source, 'lib/config.js'), 'utf8');
  const pristineServer = fs.readFileSync(path.join(source, 'server.js'), 'utf8');
  assert.doesNotMatch(pristineServer, /SPARTAN_/, 'upstream source must be pristine before patching');
  assert.match(config, /CAMOFOX_DISABLE_DEFAULT_ADDONS/);
  assert.match(config, /=== '1' \|\| .* === 'true'/, 'only exact true/1 may disable default add-ons');

  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'spartan-camofox-upstream-'));
  try {
    fs.cpSync(source, temp, { recursive: true });
    for (const entry of patchers) runPatcher(temp, entry);

    const files = ['server.js', 'lib/persistence.js', 'plugins/persistence/index.js'];
    const once = Object.fromEntries(files.map(file => [file, digest(path.join(temp, file))]));
    for (const entry of patchers) runPatcher(temp, entry);
    const twice = Object.fromEntries(files.map(file => [file, digest(path.join(temp, file))]));
    assert.deepStrictEqual(twice, once, 'the complete ordered patch chain must be byte-idempotent');

    const server = fs.readFileSync(path.join(temp, 'server.js'), 'utf8');
    assert.match(server, /SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1/);
    assert.match(server, /SPARTAN_OPERATOR_LEASE_V2/);
    assert.match(server, /if \(timer\) clearTimeout\(timer\)/);
    assert.match(server, /if \(state\.queue\.length >= MAX_SESSIONS\)/);
    assert.match(server, /const index = state\.queue\.indexOf\(waiter\)/);
    assert.match(server, /currentSession !== sourceSession/);
    assert.match(server, /code: 'legacy_tab_open_disabled'/);
    assert.match(server, /Spartan queued-create fence: pressure cleanup/);
    assert.match(server, /Spartan queued-create fence: tab reaper/);
    assert.match(server, /await session\.context\.close\(\);/);
    assert.doesNotMatch(server, /await session\.context\.close\(\)\.catch/);
    assert.match(server, /reason: 'plugin_unavailable'/);
    assert.match(server, /hasActivePageLeases\(session\)/);
    assert.match(server, /Spartan: periodic profile cleanup disabled during browser ownership transitions/);
    assert.doesNotMatch(server, /cleanupStaleFirefoxProfiles\(\{ protectedPaths: profiles \}\)/);
    assert.match(server, /isNavigationTimeout && proxyPool\?\.canRotateSessions/);
    assert.match(server, /destroyTab\(timedOutSession, timedOutTabId, 'navigation_timeout'/);
    verifyTimeoutIsolation(server);
    await verifyLaunchLateCleanup(server);
    assert.equal(server.split("geoip: process.env.CAMOFOX_GEOIP !== 'false' && !!launchProxy,").length - 1, 2);
    assert.doesNotMatch(server, /geoip: !!launchProxy,/);
    assert.match(server, /virtualDisplayRegistry\.create\(\)/);
    assert.match(server, /registerVirtualDisplayProvider:.*virtualDisplayRegistry\.register/);
    const vncPlugin = fs.readFileSync(path.join(temp, 'plugins/vnc/index.js'), 'utf8');
    const vncWatcher = fs.readFileSync(path.join(temp, 'plugins/vnc/vnc-watcher.sh'), 'utf8');
    assert.match(vncPlugin, /ctx\.plugin\.registerVirtualDisplayProvider/);
    assert.match(vncPlugin, /SPARTAN_VNC_READINESS_V2/);
    assert.match(vncPlugin, /events\.on\('browser:accepted', startAcceptedWatcher\)/);
    assert.match(vncPlugin, /events\.on\('browser:closing', stopAcceptedWatcher\)/);
    assert.match(vncWatcher, /NOVNC_PID=\$!/);
    assert.match(vncWatcher, /X11VNC_PID=\$!/);
    assert.match(vncWatcher, /VNC_BROWSER_STARTTIME/);
    assert.match(vncWatcher, /VNC_XVFB_STARTTIME/);
    assert.match(vncWatcher, /\[ "\$1" != Z \]/);
    assert.doesNotMatch(vncWatcher, /pgrep -f| -bg |find_owned_display/);
    const watcherSyntax = spawnSync('sh', ['-n', path.join(temp, 'plugins/vnc/vnc-watcher.sh')], { encoding: 'utf8' });
    assert.strictEqual(watcherSyntax.status, 0, `VNC watcher syntax failed:\n${watcherSyntax.stderr}`);
    fs.writeFileSync(path.join(temp, 'plugins/vnc/vnc-launcher.js'), `
      import { EventEmitter } from 'node:events';
      export function resolveVncConfig() { return { enabled: true, resolution: '1x1x24', vncPassword: '', viewOnly: false, vncPort: '5900', novncPort: '6080' }; }
      export function startWatcher(options) {
        const child = new EventEmitter();
        child.exitCode = null;
        child.killed = false;
        child.getVncStatus = () => ({ running: child.exitCode === null, display: options.identity.display });
        child.kill = signal => {
          assert.equal(signal, 'SIGTERM');
          child.killed = true;
          setTimeout(() => { child.exitCode = 0; child.emit('exit', 0, null); }, 20);
        };
        globalThis.__spartanVncStarts.push({ options, child });
        return child;
      }
    `.replace('          assert.equal(signal, \'SIGTERM\');', "          if (signal !== 'SIGTERM') throw new Error('unexpected signal');"));
    globalThis.__spartanVncStarts = [];
    const { createPluginEvents: createVncEvents } = await import(pathToFileURL(path.join(temp, 'lib/plugins.js')));
    const { register: registerVnc } = await import(pathToFileURL(path.join(temp, 'plugins/vnc/index.js')) + '?lifecycle-test=1');
    const vncEvents = createVncEvents();
    class FakeVirtualDisplay { get xvfb_args() { return ['-screen', '0', '1x1x24']; } kill() {} }
    await registerVnc({ get() {} }, {
      events: vncEvents, config: { accessKey: 'test' }, log() {}, sessions: new Map(),
      VirtualDisplay: FakeVirtualDisplay, safeError: String, persistenceStorageStateOptions: {},
      plugin: { settings: {}, registerVirtualDisplayProvider() {} },
    });
    assert.equal(globalThis.__spartanVncStarts.length, 0, 'VNC must not start before browser acceptance');
    await assert.rejects(vncEvents.emitAsync('browser:accepted', { launchId: 1 }), /missing accepted VNC identity field/);
    const identity = { launchId: 1, generation: 1, serverPid: 10, browserPid: 11, browserStartTime: '12', xvfbPid: 13, xvfbStartTime: '14', display: ':0' };
    await vncEvents.emitAsync('browser:accepted', identity);
    assert.equal(globalThis.__spartanVncStarts.length, 1);
    assert.deepEqual(globalThis.__spartanVncStarts[0].options.identity, identity);
    const closeStarted = Date.now();
    await vncEvents.emitAsync('browser:closing', { reason: 'test' });
    assert.ok(Date.now() - closeStarted >= 15, 'close barrier must await watcher exit');
    assert.equal(globalThis.__spartanVncStarts[0].child.exitCode, 0);
    delete globalThis.__spartanVncStarts;
    const { createVirtualDisplayRegistry } = await import(pathToFileURL(path.join(temp, 'lib/plugin-capabilities.js')));
    const registry = createVirtualDisplayRegistry(() => 'headless');
    registry.register('vnc', () => 'vnc');
    assert.equal(registry.create(), 'vnc');
    assert.throws(() => registry.register('other', () => 'other'), { code: 'plugin_capability_conflict' });
    assert.equal(registry.create(), 'vnc');
    const lifecycleModule = await import(pathToFileURL(path.join(__dirname, 'spartan-lifecycle.mjs')));
    await verifyDeepHealthReservation(server, lifecycleModule);
    // Load the real patched plugin through upstream's Object.create(ctx) scope.
    // A service assigned only to the child context is invisible to core closes.
    const { loadPlugins, createPluginEvents } = await import(pathToFileURL(path.join(temp, 'lib/plugins.js')));
    const configPath = path.join(temp, 'persistence-test-config.json');
    fs.writeFileSync(configPath, JSON.stringify({ plugins: ['persistence'] }));
    const events = createPluginEvents();
    const shared = { config: { profileDir: path.join(temp, 'scratch-profiles') },
      log() {}, events, auth: () => () => {}, persistence: Object.seal({ checkpointUser: null }) };
    assert.match(server, /persistence: Object\.seal\(\{ checkpointUser: null \}\)/);
    const app = { get() {}, post() {}, delete() {} };
    assert.deepEqual(await loadPlugins(app, shared, { configPath, pluginsDir: path.join(temp, 'plugins') }), ['persistence']);
    assert.equal(typeof shared.persistence.checkpointUser, 'function', 'core must see the plugin checkpoint service');
    const context = { storageState: async () => ({ cookies: [], origins: [] }) };
    await events.emitAsync('session:created', { userId: 'scratch', context });
    assert.equal((await shared.persistence.checkpointUser('scratch')).persisted, true);
    context.storageState = async () => { throw new Error('synthetic checkpoint failure'); };
    assert.equal((await shared.persistence.checkpointUser('scratch')).persisted, false);
    context.storageState = async () => ({ cookies: [], origins: [] });
    assert.equal((await shared.persistence.checkpointUser('scratch')).persisted, true, 'failed checkpoints remain retryable');
    assert.match(server, /app\.get\('\/spartan\/health\/deep'/);
    assert.match(server, /app\.post\('\/spartan\/recover'/);
    assert.match(server, /app\.post\(\['\/tabs\/:tabId\/spartan\/viewport', '\/tabs\/:tabId\/viewport'\]/);

    const persistence = fs.readFileSync(path.join(temp, 'lib/persistence.js'), 'utf8');
    assert.match(persistence, /SPARTAN_PERSISTENCE_DURABILITY_V2/);
    assert.match(persistence, /await handle\.sync\(\)/);
    assert.match(persistence, /await syncDirectory\(path\.dirname\(targetPath\)\)/);
    assert.match(persistence, /storageState \|\| await context\.storageState/);

    for (const file of files) {
      const result = spawnSync(process.execPath, ['--check', path.join(temp, file)], { encoding: 'utf8' });
      assert.strictEqual(result.status, 0, `${file} syntax failed:\n${result.stderr || result.stdout}`);
    }

    // Missing or duplicated anchors must fail without partially replacing source.
    for (const changed of [pristineServer.replaceAll('geoip: !!launchProxy,', 'geoip: true,'), pristineServer + '\ngeoip: !!launchProxy,\n']) {
      fs.writeFileSync(path.join(temp, 'server.js'), changed);
      const result = spawnSync(process.execPath, [path.join(__dirname, 'patch-server.js')], { cwd: temp, encoding: 'utf8' });
      assert.notEqual(result.status, 0);
      assert.match(result.stderr, /expected two Camofox proxy geoip anchors/);
      assert.equal(fs.readFileSync(path.join(temp, 'server.js'), 'utf8'), changed);
    }
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
