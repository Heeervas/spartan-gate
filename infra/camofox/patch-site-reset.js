const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const { isIP } = require('node:net');
const MARKER = '// SPARTAN_SITE_RESET_V1';
const END = '// SPARTAN_SITE_RESET_END';

// Host-only is exact; dotted Domain cookies include label-boundary ancestors.
// URL.hostname supplies canonical ASCII. IPs never use suffix matching.
function domainMatches(domain, host) {
  domain = domain.toLowerCase();
  host = host.toLowerCase();
  if (isIP(host.replace(/^\[|\]$/g, ''))) return domain === host;
  return domain === host || (domain.startsWith('.') && (domain === `.${host}` || host.endsWith(domain)));
}

const route = String.raw`${MARKER}
const spartanSiteResetFences = new Map();
const spartanSiteResetResults = new Map();
const SPARTAN_SITE_RESET_TTL_MS = 10 * 60 * 1000;
const SPARTAN_SITE_RESET_MAX_RESULTS = 2048;
function spartanSiteResetReply(code) {
  return { ok: false, site: null, cookies: 'failed', persisted: 'not_attempted', reload: 'not_attempted', code };
}
function spartanSiteResetRequest(req) {
  const body = req.body;
  const bounded = value => typeof value === 'string' && value.length > 0
    && value.length <= 256 && !/[\u0000-\u001f\u007f]/.test(value);
  return body && !Array.isArray(body)
    && Object.keys(body).sort().join(',') === 'operationId,sessionKey,userId'
    && bounded(body.userId) && bounded(body.sessionKey) && bounded(req.params.tabId)
    && typeof body.operationId === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(body.operationId);
}
function spartanSiteResetScope(userId, sessionKey, tabId) {
  const session = sessions.get(userId);
  const found = session && findTab(session, tabId);
  if (!found || found.listItemId !== sessionKey || sessionKey === SPARTAN_OPERATOR_GROUP) {
    return { status: 403, code: 'scope_denied' };
  }
  // Even an expired operator lease still shares this cookie jar.
  if (session.tabGroups.get(SPARTAN_OPERATOR_GROUP)?.size) return { status: 409, code: 'operator_denied' };
  if (session.tracePath !== null || session._spartanVaultUntraced !== true) return { status: 409, code: 'trace_denied' };
  const page = found.tabState.page;
  if (!page || page.isClosed()) return { status: 403, code: 'scope_denied' };
  const checkpoint = pluginCtx.persistence?.checkpointUser;
  if (typeof checkpoint !== 'function' || typeof checkpoint.canCheckpointUser !== 'function'
      || typeof checkpoint.settled !== 'function' || !checkpoint.canCheckpointUser(userId, session.context)) {
    return { status: 503, code: 'persistence_unavailable' };
  }
  return { session, page, checkpoint };
}
async function spartanSiteResetBounded(lease, work, timeoutMs = HANDLER_TIMEOUT_MS) {
  let timer;
  const running = spartanBrowserLifecycle.track(Promise.resolve().then(work));
  try {
    lease.assertCurrent();
    return await Promise.race([running, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('reset_timeout')), timeoutMs);
    })]);
  } finally { clearTimeout(timer); }
}
async function spartanSiteResetCookies(lease, context, host) {
  let selected;
  try {
    selected = (await spartanSiteResetBounded(lease, () => context.cookies()))
      .filter(cookie => domainMatches(cookie.domain, host));
  } catch { return 'failed'; }
  let wrote = false, failed = false;
  for (const cookie of selected) {
    try {
      await spartanSiteResetBounded(lease, () => context.addCookies([{ ...cookie, value: '', expires: 1 }]));
      wrote = true;
    } catch { failed = true; break; }
  }
  // Read-only verification: never retry or expire a newly observed cookie.
  try {
    const residual = (await spartanSiteResetBounded(lease, () => context.cookies()))
      .some(cookie => domainMatches(cookie.domain, host));
    if (failed) return wrote ? 'partial' : 'failed';
    if (residual) return selected.length ? 'deleted_with_residual' : 'partial';
    return selected.length ? 'deleted' : 'none';
  } catch { return wrote ? 'partial' : 'failed'; }
}
async function spartanSiteResetCheckpoint(lease, checkpoint, userId) {
  try {
    // checkpointUser can return inFlight before the underlying disk write ends.
    // Track that separate promise under the lifecycle lease, not just its waiter.
    const waiter = checkpoint(userId, 'site_reset', HANDLER_TIMEOUT_MS);
    const settled = checkpoint.settled(userId);
    if (!settled || typeof settled.then !== 'function') throw new Error('checkpoint_contract');
    spartanBrowserLifecycle.track(settled);
    const result = await spartanSiteResetBounded(lease, () => waiter);
    return result?.persisted === true && result?.inFlight !== true ? 'saved' : 'unknown';
  } catch { return 'unknown'; }
}
function spartanSiteResetSamePage(userId, tabId, session, page, url) {
  const found = findTab(session, tabId);
  return sessions.get(userId) === session && found?.tabState.page === page
    && !page.isClosed() && page.url() === url;
}
async function spartanSiteResetReload(lease, page, url) {
  const handler = route => route.fallback();
  try {
    // One tracked transaction: a late route registration must still unroute.
    await spartanSiteResetBounded(lease, async () => {
      try {
        await page.route('**/*', handler);
        await page.goto(url, { waitUntil: 'load', timeout: HANDLER_TIMEOUT_MS });
      } finally { await page.unroute('**/*', handler); }
    });
    return 'reloaded';
  } catch { return 'failed'; }
}
async function spartanSiteResetOperation(lease, userId, sessionKey, tabId) {
  lease.assertCurrent();
  const scope = spartanSiteResetScope(userId, sessionKey, tabId);
  if (scope.code) return { status: scope.status, payload: spartanSiteResetReply(scope.code) };
  const { session, page, checkpoint } = scope;
  const capturedUrl = page.url();
  let parsed;
  try { parsed = new URL(capturedUrl); } catch {}
  if (!parsed || !['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) {
    return { status: 400, payload: spartanSiteResetReply('invalid_site') };
  }
  const payload = { ok: false, site: parsed.hostname, cookies: 'failed', persisted: 'not_attempted', reload: 'not_attempted' };
  payload.cookies = await spartanSiteResetCookies(lease, session.context, parsed.hostname);
  if (payload.cookies === 'deleted_with_residual') {
    payload.residual_note = 'may_reappear_after_camofox_restart_repeat_reset';
  }
  payload.persisted = await spartanSiteResetCheckpoint(lease, checkpoint, userId);
  if (payload.persisted !== 'saved') return { status: 200, payload: { ...payload, code: 'checkpoint_unknown' } };
  if (['partial', 'failed'].includes(payload.cookies)) return { status: 200, payload: { ...payload, code: 'cookies_failed' } };
  if (!spartanSiteResetSamePage(userId, tabId, session, page, capturedUrl)) {
    payload.reload = 'skipped_navigated';
  } else payload.reload = await spartanSiteResetReload(lease, page, capturedUrl);
  payload.ok = ['deleted', 'none'].includes(payload.cookies) && payload.reload === 'reloaded';
  if (payload.reload === 'failed') payload.code = 'reload_failed';
  return { status: 200, payload };
}
function spartanSiteResetPrune() {
  const now = Date.now();
  for (const [key, value] of spartanSiteResetResults) {
    if (value.expires <= now) spartanSiteResetResults.delete(key);
  }
}
const spartanSiteResetParser = express.json({ limit: '4kb', strict: true });
app.post('/tabs/:tabId/spartan/reset-site', (req, res, next) => {
  const match = typeof req.headers.authorization === 'string'
    && /^Bearer ([^\s]+)$/.exec(req.headers.authorization);
  if (typeof CONFIG.accessKey !== 'string' || !CONFIG.accessKey || !match
      || !timingSafeCompare(match[1], CONFIG.accessKey)) {
    return res.status(401).json(spartanSiteResetReply('unauthorized'));
  }
  spartanSiteResetParser(req, res, error => {
    if (error) return res.status(error.type === 'entity.too.large' ? 413 : 400).json(spartanSiteResetReply('invalid_request'));
    next();
  });
}, async (req, res) => {
  if (!spartanSiteResetRequest(req)) return res.status(400).json(spartanSiteResetReply('invalid_request'));
  const userId = normalizeUserId(req.body.userId);
  const cacheKey = JSON.stringify([userId, req.body.operationId.toLowerCase()]);
  spartanSiteResetPrune();
  const previous = spartanSiteResetResults.get(cacheKey);
  if (previous?.result) return res.status(previous.result.status).json(previous.result.payload);
  if (spartanSiteResetFences.has(userId) || spartanSiteResetResults.size >= SPARTAN_SITE_RESET_MAX_RESULTS) {
    return res.status(409).json(spartanSiteResetReply('busy'));
  }
  // Reserve capacity before admission; never evict an unexpired replay receipt.
  const receipt = { expires: Date.now() + SPARTAN_SITE_RESET_TTL_MS, result: null };
  spartanSiteResetResults.set(cacheKey, receipt);
  const fence = {};
  spartanSiteResetFences.set(userId, fence);
  const publish = result => {
    receipt.result = result;
    receipt.expires = Date.now() + SPARTAN_SITE_RESET_TTL_MS;
    if (!res.headersSent) res.status(result.status).json(result.payload);
  };
  try {
    await withUserLimit(userId, async () => {
      const lock = getTabLock(req.params.tabId);
      await lock.acquire(TAB_LOCK_TIMEOUT_MS);
      try {
        await spartanBrowserLifecycle.run('site_reset', async lease => {
          try { publish(await spartanSiteResetOperation(lease, userId, req.body.sessionKey, req.params.tabId)); }
          catch { publish({ status: 500, payload: spartanSiteResetReply('reset_failed') }); }
          // Response is already bounded/published. The coordinator retains its
          // lease and this user's fence until ALL tracked underlying work settles.
        }, { waitTimeoutMs: HANDLER_TIMEOUT_MS });
      } finally { lock.release(); }
    });
  } catch {
    if (!receipt.result) publish({ status: 503, payload: spartanSiteResetReply('busy') });
  } finally {
    if (spartanSiteResetFences.get(userId) === fence) spartanSiteResetFences.delete(userId);
  }
});
${END}
`;

function replaceOnce(source, from, to) {
  if (source.split(from).length !== 2) throw new Error('site reset patch anchor mismatch');
  return source.replace(from, to);
}
function requireMarkers(source, markers) {
  for (const marker of markers) {
    if (!source.includes(marker)) throw new Error('site reset prerequisite or revision incomplete');
  }
}
function patchSiteReset(source) {
  requireMarkers(source, [
    'SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1',
    'SPARTAN_PERSISTENCE_CLOSE_FENCE_V2',
    'SPARTAN_OPERATOR_LEASE_V2',
    'SPARTAN_OPERATOR_LEASE_API_V1',
    'SPARTAN_VAULT_EVALUATE_V1',
    'SPARTAN_VAULT_EVALUATE_END',
  ]);
  if (source.includes(MARKER)) {
    requireMarkers(source, [END, route, domainMatches.toString(), 'siteResetVersion: 1,']);
    return source;
  }
  source = replaceOnce(
    source,
    "import express from 'express';",
    "import express from 'express';\nimport { isIP } from 'node:net';",
  );
  source = replaceOnce(
    source,
    "const globalJsonParser = express.json({ limit: '100kb' });",
    domainMatches.toString() + '\n' + route + "\nconst globalJsonParser = express.json({ limit: '100kb' });",
  );
  return replaceOnce(
    source,
    '    vaultEvaluateVersion: 1,',
    '    siteResetVersion: 1,\n    vaultEvaluateVersion: 1,',
  );
}
const pluginService = `  // SPARTAN_SITE_RESET_CHECKPOINT_V1
  checkpointUser.canCheckpointUser = (userId, context) =>
    !!context && activeSessions.get(String(userId)) === context && !resettingUsers.has(String(userId));
  checkpointUser.settled = userId => checkpointPromises.get(String(userId)) || Promise.resolve();
`;
function patchSiteResetPlugin(source) {
  requireMarkers(source, [
    'SPARTAN_PERSISTENCE_CHECKPOINT_SERVICE_V2',
    'ctx.persistence.checkpointUser = checkpointUser;',
  ]);
  if (source.includes('// SPARTAN_SITE_RESET_CHECKPOINT_V1')) {
    requireMarkers(source, [pluginService, "logger: reason === 'site_reset' ? { warn() {} } : logger,"]);
    return source;
  }
  source = replaceOnce(
    source,
    '  ctx.persistence.checkpointUser = checkpointUser;',
    pluginService + '  ctx.persistence.checkpointUser = checkpointUser;',
  );
  // persistStorageState may receive an exception carrying cookie values. Do not
  // pass that exception, or any captured state, into the plugin logger.
  return replaceOnce(
    source,
    '        logger,\n        indexedDB,',
    "        logger: reason === 'site_reset' ? { warn() {} } : logger,\n        indexedDB,",
  );
}
if (require.main === module) {
  const targets = [process.argv[2] || 'server.js', process.argv[3] || 'plugins/persistence/index.js'];
  const patches = [patchSiteReset, patchSiteResetPlugin];
  // Complete transformation AND syntax preflight on both files before replacing either.
  const candidates = targets.map((target, i) => [target, patches[i](fs.readFileSync(target, 'utf8'))]);
  const staged = [];
  try {
    for (const [target, content] of candidates) {
      const temporary = target + '.spartan-site-reset.js';
      staged.push([target, temporary]);
      fs.writeFileSync(temporary, content);
      const check = spawnSync(process.execPath, ['--input-type=module', '--check'], {
        input: content,
        encoding: 'utf8',
      });
      if (check.status !== 0) throw new Error('site reset patched syntax invalid');
    }
    for (const [target, temporary] of staged) fs.renameSync(temporary, target);
  } finally {
    for (const [, temporary] of staged) fs.rmSync(temporary, { force: true });
  }
}
module.exports = { domainMatches, patchSiteReset, patchSiteResetPlugin };
