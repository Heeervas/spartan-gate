const assert = require('assert');
const fs = require('fs');
const path = require('path');
const test = require('node:test');
const vm = require('vm');

const { patchOperatorLease } = require('./patch-operator-lease.js');

function fixture() {
  return `function spartanBrowserLifecycleError(code, message) {
  return Object.assign(new Error(message), { code });
}
function spartanHasPendingUserCreate() { return false; }
function attachPopupHandler() {}

async function closeSession(userId, session, {
  reason = 'session_closed',
} = {}) {
  if (!session) return;
  if (
    reason === 'api_delete_session'
    && spartanHasPendingUserCreate(userId)
  ) {
    throw new Error('pending');
  }
  return 'closed';
}

async function recycleOldestTab(session) {
  let oldestTab = null;
  for (const [, group] of session.tabGroups) {
    for (const [tid, ts] of group) {
      if (!oldestTab || ts.toolCalls < oldestTab.toolCalls) {
        oldestTab = ts;
      }
    }
  }
  return oldestTab;
}

function pressureCandidates(sessions, now) {
  const result = [];
  for (const [, session] of sessions) {
    for (const [, group] of session.tabGroups) {
      for (const [tabId, tabState] of group) {
        const lockState = pressureLockState(tabId);
        result.push([tabState, lockState]);
      }
    }
  }
  return result;
}

function createTab(group, tabId, tabState, page, userId, resolvedSessionKey) {
      group.set(tabId, tabState);
      attachPopupHandler(page, userId, resolvedSessionKey);
}

function timeoutCandidate(now, session, userId) {
  return (
      now - session.lastAccess > SESSION_TIMEOUT_MS
      && !spartanHasPendingUserCreate(userId)
  );
}

function memoryCandidate(entries) {
  for (const [key, session] of entries) {
        if (session._closing || spartanHasPendingUserCreate(key)) continue;
        if (session.lastAccess < oldestAccess) {
          return session;
        }
  }
}

function reap(group, now) {
      for (const [tabId, tabState] of group) {
        if (!tabState._lastReaperCheck) {
          tabState._lastReaperCheck = now;
        }
      }
}

const capabilities = {
    operatorTabIsolationVersion: 1,
    nonDestructiveTabCreate: true,
};
`;
}

test('operator lease is idempotent and guards every ordinary destructive class', async () => {
  const patched = patchOperatorLease(fixture());
  assert.strictEqual(patchOperatorLease(patched), patched);
  assert.match(patched, /SPARTAN_OPERATOR_LEASE_V1/);
  assert.match(patched, /operatorLeaseMs: SPARTAN_OPERATOR_LEASE_MS/);
  assert.match(patched, /spartanOperatorTabLeaseActive\(ts\)/);
  assert.match(patched, /spartanOperatorTabLeaseActive\(tabState, now\)/);
  assert.match(patched, /spartanOperatorSessionLeaseActive\(session, now\)/);
  assert.match(patched, /spartanOperatorSessionLeaseActive\(session\)/);

  const api = vm.runInNewContext(
    `${patched}\n({
      closeSession,
      createTab,
      tabLeaseActive: spartanOperatorTabLeaseActive,
      sessionLeaseActive: spartanOperatorSessionLeaseActive,
      capabilities,
    })`,
  );
  const group = new Map();
  const tabState = { toolCalls: 0 };
  api.createTab(group, 'operator-tab', tabState, {}, 'user', 'spartan-operator-login');
  const session = { tabGroups: new Map([['spartan-operator-login', group]]) };

  assert.strictEqual(api.tabLeaseActive(tabState), true);
  assert.strictEqual(api.sessionLeaseActive(session), true);
  assert.strictEqual(api.capabilities.operatorLeaseMs, 30 * 60 * 1000);
  await assert.rejects(
    api.closeSession('user', session, { reason: 'session_timeout' }),
    error => error.code === 'operator_lease_active' && error.statusCode === 409,
  );
  assert.strictEqual(
    await api.closeSession('user', session, { reason: 'shutdown:SIGTERM' }),
    'closed',
  );
});

test('Camofox image applies persistence before operator lease', () => {
  const dockerfile = fs.readFileSync(path.join(__dirname, 'Dockerfile'), 'utf8');
  const persistence = dockerfile.indexOf('patch-persistence.js');
  const lease = dockerfile.indexOf('patch-operator-lease.js');
  assert.notStrictEqual(persistence, -1);
  assert.notStrictEqual(lease, -1);
  assert.ok(persistence < lease);
});
