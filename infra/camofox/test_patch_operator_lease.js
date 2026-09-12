const assert = require('assert');
const fs = require('fs');
const path = require('path');
const test = require('node:test');
const vm = require('node:vm');
const { patchOperatorLease } = require('./patch-operator-lease.js');

const fixture = `const SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1;
async function closeSession(userId, session, {
  reason = 'session_closed',
  clearDownloads = true,
  clearLocks = true,
} = {}) {
  if (!session) return;
  if (reason === 'api_delete_session' && spartanHasPendingUserCreate(userId)) {}
}
async function recycleOldestTab(session, reqId, userId) {
  for (const [gKey, group] of session.tabGroups) {
    for (const [tid, ts] of group) {
      if (!oldestTab || ts.toolCalls < oldestTab.toolCalls) {}
    }
  }
}
function pressureCleanup(now) {
  for (const [userId, session] of sessions) {
    for (const [listItemId, group] of session.tabGroups) {
      for (const [tabId, tabState] of group) {
        const lockState = pressureLockState(tabId);
      }
    }
  }
}
app.get('/spartan/capabilities', authMiddleware(), (_req, res) => {
  res.json({
    operatorTabIsolationVersion: 1,
    nonDestructiveTabCreate: true,
  });
});
app.post('/tabs', async (req, res) => {
  const resolvedSessionKey = req.body.sessionKey;
  const group = getTabGroup(session, resolvedSessionKey);
  const tabId = fly.makeTabId();
  let tabState = createTabState(page);
      group.set(tabId, tabState);
      releasePageLease(session, lease);
});
app.post('/tabs/:tabId/navigate', async (req, res) => {});
function cleanupSessions(now) {
  for (const [userId, session] of Array.from(sessions.entries())) {
    if (SESSION_TIMEOUT_MS > 0 && now - session.lastAccess > SESSION_TIMEOUT_MS) {}
  }
}
function memoryEviction() {
      for (const [key, session] of sessions) {
        if (session._closing || hasActivePageLeases(session)) continue;
  }
}
function tabReaper(now) {
  for (const [userId, session] of sessions) {
    for (const [listItemId, group] of session.tabGroups) {
      for (const [tabId, tabState] of group) {
        if (!tabState._lastReaperCheck) {}
      }
    }
  }
}
`;

test('operator lease is idempotent and guards every ordinary destructive class', () => {
  const patched = patchOperatorLease(fixture);
  assert.strictEqual(patchOperatorLease(patched), patched);
  assert.match(patched, /SPARTAN_OPERATOR_LEASE_V2/);
  assert.match(patched, /operator_lease_active/);
  assert.ok(
    patched.indexOf('!spartanBrowserLifecycle.hasLease() && session._spartanClosePromise')
      < patched.indexOf('if (spartanOperatorSessionLeaseActive(session)'),
    'an in-flight hard close must win before the operator soft-close fence',
  );
  assert.match(patched, /resolvedSessionKey === SPARTAN_OPERATOR_GROUP/);
  assert.match(patched, /spartanOperatorTabLeaseActive\(ts\)/);
  assert.match(patched, /spartanOperatorTabLeaseActive\(tabState, now\)/);
  assert.match(patched, /!spartanOperatorSessionLeaseActive\(session, now\)/);
  assert.match(patched, /hasActivePageLeases\(session\) \|\| spartanHasPendingUserCreate\(key\)/);
  assert.match(patched, /spartanOperatorSessionLeaseActive\(session\)/);
  assert.match(patched, /operatorLeaseMs: SPARTAN_OPERATOR_LEASE_MS/);
});

test('Camofox image applies persistence before operator lease', () => {
  const dockerfile = fs.readFileSync(path.join(__dirname, 'Dockerfile'), 'utf8');
  assert.ok(dockerfile.indexOf('patch-persistence.js') < dockerfile.indexOf('patch-operator-lease.js'));
});

test('session expiry preserves disabled timeouts, pending creates and operator leases', () => {
  const patched = patchOperatorLease(fixture);
  const condition = patched.match(/if \(\n(\s+SESSION_TIMEOUT_MS > 0[\s\S]*?)\n    \) \{/);
  assert.ok(condition, 'transformed expiry condition exists');
  for (const [timeout, pending, leased, expired] of [
    [0, false, false, false], [-1, false, false, false],
    [100, true, false, false], [100, false, true, false],
    [100, false, false, true], [10000, false, false, false],
  ]) {
    assert.equal(vm.runInNewContext(`(${condition[1]})`, {
      SESSION_TIMEOUT_MS: timeout, now: 1000, session: { lastAccess: 0 }, userId: 'user',
      spartanHasPendingUserCreate: () => pending,
      spartanOperatorSessionLeaseActive: () => leased,
    }), expired);
  }
});
