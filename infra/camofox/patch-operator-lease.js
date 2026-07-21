const fs = require('fs');
const { spawnSync } = require('child_process');

const serverPath = process.argv[2] || 'server.js';

function replaceOnce(source, from, to, label) {
  const count = source.split(from).length - 1;
  if (count !== 1) throw new Error(`${label}: expected one anchor, found ${count}`);
  return source.replace(from, to);
}

function patchOperatorLease(source) {
  if (source.includes('SPARTAN_OPERATOR_LEASE_V1')) return source;

  const leaseHelpers = `const SPARTAN_OPERATOR_GROUP = 'spartan-operator-login';
const SPARTAN_OPERATOR_LEASE_MS = 30 * 60 * 1000;

function spartanOperatorTabLeaseActive(tabState, now = Date.now()) {
  return Number(tabState?._spartanOperatorLeaseUntil || 0) > now;
}

function spartanOperatorSessionLeaseActive(session, now = Date.now()) {
  if (!session?.tabGroups) return false;
  for (const group of session.tabGroups.values()) {
    for (const tabState of group.values()) {
      if (spartanOperatorTabLeaseActive(tabState, now)) return true;
    }
  }
  return false;
}

function spartanHardCloseReason(reason) {
  return String(reason || '').startsWith('shutdown:')
    || String(reason || '').startsWith('browser_restart:')
    || reason === 'oom_pressure_hard';
}

// SPARTAN_OPERATOR_LEASE_V1
`;
  source = replaceOnce(
    source,
    'async function closeSession(userId, session, {\n',
    `${leaseHelpers}\nasync function closeSession(userId, session, {\n`,
    'operator lease helpers',
  );

  source = replaceOnce(
    source,
    `  if (!session) return;
  if (
    reason === 'api_delete_session'
`,
    `  if (!session) return;
  if (spartanOperatorSessionLeaseActive(session) && !spartanHardCloseReason(reason)) {
    session._closing = false;
    const error = spartanBrowserLifecycleError(
      'operator_lease_active',
      'operator login tab is leased; close that tab explicitly before closing the context',
    );
    error.statusCode = 409;
    throw error;
  }
  if (
    reason === 'api_delete_session'
`,
    'operator close fence',
  );

  source = replaceOnce(
    source,
    `      group.set(tabId, tabState);
      attachPopupHandler(page, userId, resolvedSessionKey);
`,
    `      group.set(tabId, tabState);
      if (resolvedSessionKey === SPARTAN_OPERATOR_GROUP) {
        tabState._spartanOperatorLeaseUntil = Date.now() + SPARTAN_OPERATOR_LEASE_MS;
      }
      attachPopupHandler(page, userId, resolvedSessionKey);
`,
    'operator lease creation',
  );

  source = replaceOnce(
    source,
    `    for (const [tid, ts] of group) {
      if (!oldestTab || ts.toolCalls < oldestTab.toolCalls) {
`,
    `    for (const [tid, ts] of group) {
      if (spartanOperatorTabLeaseActive(ts)) continue;
      if (!oldestTab || ts.toolCalls < oldestTab.toolCalls) {
`,
    'tab recycling lease',
  );

  source = replaceOnce(
    source,
    `      for (const [tabId, tabState] of group) {
        const lockState = pressureLockState(tabId);
`,
    `      for (const [tabId, tabState] of group) {
        if (spartanOperatorTabLeaseActive(tabState, now)) continue;
        const lockState = pressureLockState(tabId);
`,
    'pressure cleanup lease',
  );

  source = replaceOnce(
    source,
    `      now - session.lastAccess > SESSION_TIMEOUT_MS
      && !spartanHasPendingUserCreate(userId)
`,
    `      now - session.lastAccess > SESSION_TIMEOUT_MS
      && !spartanHasPendingUserCreate(userId)
      && !spartanOperatorSessionLeaseActive(session, now)
`,
    'session timeout lease',
  );

  source = replaceOnce(
    source,
    `        if (session._closing || spartanHasPendingUserCreate(key)) continue;
        if (session.lastAccess < oldestAccess) {
`,
    `        if (session._closing || spartanHasPendingUserCreate(key)) continue;
        if (spartanOperatorSessionLeaseActive(session)) continue;
        if (session.lastAccess < oldestAccess) {
`,
    'memory eviction lease',
  );

  source = replaceOnce(
    source,
    `      for (const [tabId, tabState] of group) {
        if (!tabState._lastReaperCheck) {
`,
    `      for (const [tabId, tabState] of group) {
        if (spartanOperatorTabLeaseActive(tabState, now)) continue;
        if (!tabState._lastReaperCheck) {
`,
    'tab reaper lease',
  );

  source = replaceOnce(
    source,
    `    operatorTabIsolationVersion: 1,
    nonDestructiveTabCreate: true,
`,
    `    operatorTabIsolationVersion: 1,
    operatorLeaseMs: SPARTAN_OPERATOR_LEASE_MS,
    nonDestructiveTabCreate: true,
`,
    'operator lease capability',
  );
  return source;
}

if (require.main === module) {
  const original = fs.readFileSync(serverPath, 'utf8');
  const patched = patchOperatorLease(original);
  const tmpPath = `${serverPath}.spartan-operator-lease.js`;
  try {
    fs.writeFileSync(tmpPath, patched);
    const checked = spawnSync(process.execPath, ['--check', tmpPath], { encoding: 'utf8' });
    if (checked.status !== 0) {
      throw new Error(`patched server syntax check failed: ${checked.stderr || checked.stdout}`);
    }
    fs.renameSync(tmpPath, serverPath);
  } finally {
    fs.rmSync(tmpPath, { force: true });
  }
}

module.exports = { patchOperatorLease };
