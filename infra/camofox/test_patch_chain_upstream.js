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
    assert.equal(server.split("geoip: process.env.CAMOFOX_GEOIP !== 'false' && !!launchProxy,").length - 1, 2);
    assert.doesNotMatch(server, /geoip: !!launchProxy,/);
    assert.match(server, /virtualDisplayRegistry\.create\(\)/);
    assert.match(server, /registerVirtualDisplayProvider:.*virtualDisplayRegistry\.register/);
    assert.match(fs.readFileSync(path.join(temp, 'plugins/vnc/index.js'), 'utf8'), /ctx\.plugin\.registerVirtualDisplayProvider/);
    const { createVirtualDisplayRegistry } = await import(pathToFileURL(path.join(temp, 'lib/plugin-capabilities.js')));
    const registry = createVirtualDisplayRegistry(() => 'headless');
    registry.register('vnc', () => 'vnc');
    assert.equal(registry.create(), 'vnc');
    assert.throws(() => registry.register('other', () => 'other'), { code: 'plugin_capability_conflict' });
    assert.equal(registry.create(), 'vnc');
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
