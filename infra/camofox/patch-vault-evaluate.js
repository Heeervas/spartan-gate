const fs = require('node:fs');
const { spawnSync } = require('node:child_process');
const MARKER = '// SPARTAN_VAULT_EVALUATE_V1';

// Registered before the global parser, request reporters and plugin surfaces.
const route = `${MARKER}
function spartanVaultError(res, status, code) {
  return res.status(status).json({ ok: false, error: code, error_type: code });
}
const spartanVaultParser = express.json({ limit: '100kb', strict: true });
app.post('/tabs/:tabId/spartan/vault-evaluate', (req, res, next) => {
  const match = typeof req.headers.authorization === 'string'
    && /^Bearer ([^\\s]+)$/.exec(req.headers.authorization);
  if (typeof CONFIG.accessKey !== 'string' || !CONFIG.accessKey || !match
      || !timingSafeCompare(match[1], CONFIG.accessKey)) {
    return spartanVaultError(res, 401, 'unauthorized');
  }
  spartanVaultParser(req, res, error => {
    if (error) return spartanVaultError(res, error.type === 'entity.too.large' ? 413 : 400, 'invalid_request');
    next();
  });
}, async (req, res) => {
  const body = req.body;
  const bounded = (value, max) => typeof value === 'string' && value.length > 0
    && value.length <= max && !/[\\u0000-\\u001f\\u007f]/.test(value);
  if (!body || Array.isArray(body) || Object.keys(body).sort().join(',') !== 'expression,sessionKey,userId'
      || !bounded(body.userId, 256) || !bounded(body.sessionKey, 256)
      || !bounded(req.params.tabId, 256) || typeof body.expression !== 'string'
      || !body.expression.length || Buffer.byteLength(body.expression, 'utf8') > 96 * 1024) {
    return spartanVaultError(res, 400, 'invalid_request');
  }
  const { userId, sessionKey, expression } = body;
  const tabId = req.params.tabId;
  const fail = (status, code) => Object.assign(new Error(code), { vaultStatus: status, vaultCode: code });
  try {
    // Keep the existing tab-lock -> lifecycle order used by navigation routes.
    // Neither lock admission nor lifecycle admission starts the evaluation timer.
    const result = await withUserLimit(userId, async () => {
      const lock = getTabLock(tabId);
      await lock.acquire(TAB_LOCK_TIMEOUT_MS);
      try {
        return await spartanBrowserLifecycle.run('vault_evaluate', async lease => {
          lease.assertCurrent();
          const session = sessions.get(normalizeUserId(userId));
          const found = session && findTab(session, tabId);
          if (!found || found.listItemId !== sessionKey || sessionKey === SPARTAN_OPERATOR_GROUP) {
            throw fail(403, 'vault_scope_denied');
          }
          // Trace startup failures also set tracePath null; require explicit untraced creation.
          if (session.tracePath !== null || session._spartanVaultUntraced !== true) throw fail(409, 'vault_trace_denied');
          const page = found.tabState.page;
          if (!page || page.isClosed()) throw fail(403, 'vault_scope_denied');
          let timer;
          const running = spartanBrowserLifecycle.track(Promise.resolve().then(() => page.evaluate(expression)));
          try {
            let value = await Promise.race([
              running,
              new Promise((_, reject) => {
                timer = setTimeout(() => reject(fail(410, 'vault_evaluate_timeout')), HANDLER_TIMEOUT_MS);
              }),
            ]);
            // Native vault build_fill_js returns JSON text. Parse only a bounded
            // result and project the safe fields rather than forwarding page output.
            if (typeof value === 'string' && Buffer.byteLength(value, 'utf8') <= 4096) {
              try { value = JSON.parse(value); } catch { throw fail(500, 'vault_evaluate_failed'); }
            }
            if (value && typeof value === 'object' && !Array.isArray(value)) {
              const keys = Object.keys(value);
              if (keys.length === 1 && keys[0] === 'filled' && Number.isSafeInteger(value.filled) && value.filled >= 0) {
                return { filled: value.filled };
              }
              if (value.refused === 'origin_changed' && keys.every(key => key === 'refused' || key === 'found')
                  && (value.found === undefined || (typeof value.found === 'string' && value.found.length <= 2048))) {
                return { refused: 'origin_changed' };
              }
            }
            throw fail(500, 'vault_evaluate_failed');
          } catch (error) {
            if (error?.vaultCode === 'vault_evaluate_timeout') {
              // Close within this lease to settle the tracked evaluate before releasing
              // ownership. Do not use generic cleanup/logging or plugin callbacks.
              let closeTimer;
              try {
                await Promise.race([
                  spartanBrowserLifecycle.track(page.close({ runBeforeUnload: false }))
                    .then(() => running.then(() => undefined, () => undefined)),
                  new Promise((_, reject) => {
                    closeTimer = setTimeout(() => reject(fail(410, 'vault_evaluate_timeout')), PAGE_CLOSE_TIMEOUT_MS);
                  }),
                ]);
              } catch {
                // Keep the lease's tracked work fenced until it settles or the
                // supervisor replaces this degraded process. Bound the HTTP reply.
                spartanBrowserLifecycle.markDegraded('vault_cleanup_failed');
                spartanVaultError(res, 410, 'vault_evaluate_timeout');
              } finally {
                clearTimeout(closeTimer);
                found.group.delete(tabId);
                if (!found.group.size) session.tabGroups.delete(found.listItemId);
                lock.drain();
                tabLocks.delete(tabId);
                refreshTabLockQueueDepth();
                refreshActiveTabsGauge();
              }
            }
            throw error;
          } finally {
            clearTimeout(timer);
          }
        }, { waitTimeoutMs: HANDLER_TIMEOUT_MS });
      } finally {
        lock.release();
      }
    });
    return res.json({ ok: true, result });
  } catch (error) {
    if (res.headersSent) return;
    if (error?.vaultCode) return spartanVaultError(res, error.vaultStatus, error.vaultCode);
    if (typeof error?.code === 'string' && error.code.startsWith('browser_lifecycle_')) {
      return spartanVaultError(res, 503, 'vault_unavailable');
    }
    if (error?.statusCode === 429 || error?.message === 'Tab lock queue timeout') {
      return spartanVaultError(res, 503, 'vault_unavailable');
    }
    return spartanVaultError(res, 500, 'vault_evaluate_failed');
  }
});
// SPARTAN_VAULT_EVALUATE_END
`;

function replaceOnce(source, from, to) {
  if (source.split(from).length !== 2) throw new Error('vault evaluate patch anchor mismatch');
  return source.replace(from, to);
}
function patchVaultEvaluate(source) {
  if (source.includes(MARKER)) return source;
  source = replaceOnce(source, "const globalJsonParser = express.json({ limit: '100kb' });", route + "\nconst globalJsonParser = express.json({ limit: '100kb' });");
  source = replaceOnce(source, '    operatorTabIsolationVersion: 1,', '    vaultEvaluateVersion: 1,\n    operatorTabIsolationVersion: 1,');
  return replaceOnce(source, 'proxySessionId: sessionProxy?.sessionId || null, tracePath };',
    'proxySessionId: sessionProxy?.sessionId || null, tracePath, _spartanVaultUntraced: trace === false };');
}
if (require.main === module) {
  const target = process.argv[2] || 'server.js';
  const patched = patchVaultEvaluate(fs.readFileSync(target, 'utf8'));
  const temporary = target + '.spartan-vault.js';
  try {
    fs.writeFileSync(temporary, patched);
    const check = spawnSync(process.execPath, ['--check', temporary], { encoding: 'utf8' });
    if (check.status !== 0) throw new Error('vault evaluate patched syntax invalid: ' + check.stderr);
    fs.renameSync(temporary, target);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}
module.exports = { patchVaultEvaluate };
