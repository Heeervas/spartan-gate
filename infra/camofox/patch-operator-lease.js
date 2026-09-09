const fs = require('fs');
const { spawnSync } = require('child_process');

const serverPath = process.argv[2] || 'server.js';

function replaceOnce(source, from, to, label) {
  const count = source.split(from).length - 1;
  if (count !== 1) throw new Error(`${label}: expected one anchor, found ${count}`);
  return source.replace(from, to);
}

function patchOperatorLease(source) {
  if (source.includes('SPARTAN_OPERATOR_LEASE_V2')) return source;
  if (!source.includes('SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1')) {
    throw new Error('operator lease requires the pinned Camofox 1.14 server patch');
  }
  const helpers = `const SPARTAN_OPERATOR_GROUP = 'spartan-operator-login';
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
// SPARTAN_OPERATOR_LEASE_V2
`;
  source = replaceOnce(source, 'async function closeSession(userId, session, {\n', helpers + '\nasync function closeSession(userId, session, {\n', 'operator helpers');
  source = replaceOnce(
    source,
    `  if (!session) return;
  if (reason === 'api_delete_session'`,
    `  if (!session) return;
  if (!spartanBrowserLifecycle.hasLease() && session._spartanClosePromise) {
    return await session._spartanClosePromise;
  }
  if (spartanOperatorSessionLeaseActive(session) && !spartanHardCloseReason(reason)) {
    session._closing = false;
    const error = spartanBrowserLifecycleError('operator_lease_active', 'operator login tab is leased; close that tab explicitly before closing the context');
    error.statusCode = 409;
    throw error;
  }
  if (reason === 'api_delete_session'`,
    'operator close fence',
  );

  const createStart = source.indexOf("app.post('/tabs', async (req, res) => {");
  const createEnd = source.indexOf("app.post('/tabs/:tabId/navigate', async (req, res) => {", createStart);
  if (createStart === -1 || createEnd === -1) throw new Error('operator create route anchors not found');
  let createRoute = source.slice(createStart, createEnd);
  const createMarker = '      group.set(tabId, tabState);\n      releasePageLease(session, lease);';
  if ((createRoute.split(createMarker).length - 1) !== 1) throw new Error('operator create marker not found');
  createRoute = createRoute.replace(createMarker, `      group.set(tabId, tabState);
      if (resolvedSessionKey === SPARTAN_OPERATOR_GROUP) tabState._spartanOperatorLeaseUntil = Date.now() + SPARTAN_OPERATOR_LEASE_MS;
      releasePageLease(session, lease);`);
  source = source.slice(0, createStart) + createRoute + source.slice(createEnd);

  source = replaceOnce(source, `    for (const [tid, ts] of group) {
      if (!oldestTab || ts.toolCalls < oldestTab.toolCalls) {`, `    for (const [tid, ts] of group) {
      if (spartanOperatorTabLeaseActive(ts)) continue;
      if (!oldestTab || ts.toolCalls < oldestTab.toolCalls) {`, 'tab recycling lease');
  source = replaceOnce(source, `      for (const [tabId, tabState] of group) {
        const lockState = pressureLockState(tabId);`, `      for (const [tabId, tabState] of group) {
        if (spartanOperatorTabLeaseActive(tabState, now)) continue;
        const lockState = pressureLockState(tabId);`, 'pressure cleanup lease');
  source = replaceOnce(source, '    if (now - session.lastAccess > SESSION_TIMEOUT_MS) {', `    if (
      now - session.lastAccess > SESSION_TIMEOUT_MS
      && !spartanHasPendingUserCreate(userId)
      && !spartanOperatorSessionLeaseActive(session, now)
    ) {`, 'session timeout lease');
  source = replaceOnce(source, '        if (session._closing || hasActivePageLeases(session)) continue;', '        if (session._closing || hasActivePageLeases(session) || spartanHasPendingUserCreate(key)) continue;\n        if (spartanOperatorSessionLeaseActive(session)) continue;', 'memory eviction lease');
  source = replaceOnce(source, `      for (const [tabId, tabState] of group) {
        if (!tabState._lastReaperCheck) {`, `      for (const [tabId, tabState] of group) {
        if (spartanOperatorTabLeaseActive(tabState, now)) continue;
        if (!tabState._lastReaperCheck) {`, 'tab reaper lease');
  return replaceOnce(source, `    operatorTabIsolationVersion: 1,
    nonDestructiveTabCreate: true,`, `    operatorTabIsolationVersion: 1,
    operatorLeaseMs: SPARTAN_OPERATOR_LEASE_MS,
    nonDestructiveTabCreate: true,`, 'operator lease capability');
}

if (require.main === module) {
  const original = fs.readFileSync(serverPath, 'utf8');
  const patched = patchOperatorLease(original);
  const tmpPath = `${serverPath}.spartan-operator-lease.js`;
  try {
    fs.writeFileSync(tmpPath, patched);
    const checked = spawnSync(process.execPath, ['--check', tmpPath], { encoding: 'utf8' });
    if (checked.status !== 0) throw new Error(`patched server syntax check failed: ${checked.stderr || checked.stdout}`);
    fs.renameSync(tmpPath, serverPath);
  } finally {
    fs.rmSync(tmpPath, { force: true });
  }
}

module.exports = { patchOperatorLease };
