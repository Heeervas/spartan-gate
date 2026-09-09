const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const test = require('node:test');

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

test('ordered patch chain transforms pristine pinned Camofox source idempotently', {
  skip: upstreamSource ? false : 'set CAMOFOX_UPSTREAM_SOURCE to an extracted pristine v1.14.0 tree',
}, () => {
  const source = path.resolve(upstreamSource);
  assert.ok(fs.existsSync(path.join(source, 'server.js')), 'upstream source must contain server.js');
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
    assert.match(server, /never scan Firefox temp profiles while a browser is live/);
    assert.doesNotMatch(server, /Periodic temp profile cleanup every 10 minutes/);
    assert.match(server, /NAVIGATION_TIMEOUT_ACTIONS\.has\(action\) && proxyPool\?\.canRotateSessions/);
    assert.match(server, /destroyTab\(timedOutSession, timedOutTabId, 'navigation_timeout'/);
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
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
