const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const test = require('node:test');

const patch = path.join(__dirname, 'patch-server.js');

test('1.15 server adapter retains upstream leases and fixes inherited profile/timeout bugs', () => {
  const source = fs.readFileSync(patch, 'utf8');
  assert.match(source, /SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1/);
  assert.match(source, /createPageWithRecoveryForUser/);
  assert.match(source, /closeLeasedPage\(late\.session, late\.page, late\.lease\)/);
  assert.match(source, /cleanupStaleFirefoxProfiles\(\{ protectedPaths: profiles \}\)/);
  assert.match(source, /isNavigationTimeout && proxyPool\?\.canRotateSessions/);
  assert.match(source, /destroyTab\(timedOutSession, timedOutTabId, 'navigation_timeout'/);
  assert.match(source, /spartanBrowserLifecycle\.run\('tab_create'/);
  assert.match(source, /spartanNavigationReadiness/);
  assert.match(source, /ownerDeadlineExceeded.*browser_lifecycle_stuck/);
  assert.match(source, /if \(paths\.length > 0 && !CONFIG\.disableDefaultAddons\)/);
  assert.match(source, /CAMOFOX_ADDONS requires CAMOFOX_DISABLE_DEFAULT_ADDONS=true/);
  assert.match(source, /app\.get\('\/spartan\/health\/deep'/);
  assert.match(source, /app\.post\('\/spartan\/recover'/);
  assert.match(source, /app\.get\('\/live'/);
  assert.match(source, /app\.post\(\['\/tabs\/:tabId\/spartan\/viewport', '\/tabs\/:tabId\/viewport'\]/);
  assert.doesNotMatch(source, /const spartanCreateLimitMarker/);
});

test('unsupported pre-1.15 source fails closed without rewriting it', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'spartan-camofox-unsupported-'));
  try {
    const serverPath = path.join(temp, 'server.js');
    const original = "import fs from 'fs';\nlet browser = null;\n";
    fs.writeFileSync(serverPath, original);
    const result = spawnSync(process.execPath, [patch], { cwd: temp, encoding: 'utf8' });
    assert.notStrictEqual(result.status, 0);
    assert.match(result.stderr, /expected Camofox browser globals marker|unsupported Camofox upstream/);
    assert.strictEqual(fs.readFileSync(serverPath, 'utf8'), original);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test('Dockerfile pins 1.15 and asserts the modern patch markers', () => {
  const dockerfile = fs.readFileSync(path.join(__dirname, 'Dockerfile'), 'utf8');
  assert.match(dockerfile, /camofox-browser:1\.15\.0@sha256:e32beb2a6c958126bc2eb0882dedd97880bd822c0c15ea5b24cff558400664ae/);
  assert.match(dockerfile, /SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1/);
  assert.doesNotMatch(dockerfile, /delete config\.plugins\.vnc\.enabled/);
});
