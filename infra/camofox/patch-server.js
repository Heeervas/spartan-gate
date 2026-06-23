const fs = require('fs');

const path = 'server.js';
let source = fs.readFileSync(path, 'utf8');

const helperMarker = `let virtualDisplay = null;
let browserLaunchProxy = null;
let externalCamoufoxLaunch = null;
`;
const helper = `${helperMarker}
const spartanObsidianClipperBridgeToken = crypto.randomBytes(32).toString('hex');

function spartanIsObsidianClipperAddon(addonPath) {
  const manifestPath = \`\${addonPath}/manifest.json\`;
  if (!fs.existsSync(manifestPath)) return false;

  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  } catch {
    return false;
  }

  return manifest?.browser_specific_settings?.gecko?.id === 'clipper@obsidian.md'
    || manifest?.name === 'Obsidian Web Clipper';
}

function spartanStageObsidianClipperAddon(addonPath) {
  const contentPath = \`\${addonPath}/content.js\`;
  if (!fs.existsSync(contentPath)) {
    throw new Error(\`Obsidian Web Clipper addon is missing content.js: \${addonPath}\`);
  }

  const content = fs.readFileSync(contentPath, 'utf8');
  if (!content.includes('saveMarkdownToFile')) {
    throw new Error('Obsidian Web Clipper content.js does not expose the expected saveMarkdownToFile handler');
  }

  const stagedRoot = '/tmp/spartan-camofox-addons';
  const stagedPath = \`\${stagedRoot}/web-clipper-obsidian\`;
  fs.rmSync(stagedPath, { recursive: true, force: true });
  fs.mkdirSync(stagedRoot, { recursive: true });
  fs.cpSync(addonPath, stagedPath, { recursive: true });

  const stagedContentPath = \`\${stagedPath}/content.js\`;
  const marker = 'spartan-gate-obsidian-clipper-bridge';
  if (!fs.readFileSync(stagedContentPath, 'utf8').includes(marker)) {
    fs.appendFileSync(stagedContentPath, \`
;(() => {
  const marker = '\${marker}';
  const token = '\${spartanObsidianClipperBridgeToken}';
  const requestType = 'spartan-gate:obsidian-clipper:save-file';
  const resultType = 'spartan-gate:obsidian-clipper:result';
  const used = new Set();
  if (window.__spartanGateObsidianClipperBridge) return;
  window.__spartanGateObsidianClipperBridge = { marker };

  async function sendExtensionMessage(message) {
    if (globalThis.browser?.runtime?.sendMessage) {
      return await globalThis.browser.runtime.sendMessage(message);
    }
    if (globalThis.chrome?.runtime?.sendMessage) {
      return await new Promise((resolve, reject) => {
        globalThis.chrome.runtime.sendMessage(message, response => {
          const err = globalThis.chrome.runtime.lastError;
          if (err) reject(new Error(err.message));
          else resolve(response);
        });
      });
    }
    throw new Error('extension runtime messaging is unavailable');
  }

  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source !== window || !data || data.type !== requestType) return;
    if (data.token !== token || typeof data.nonce !== 'string' || data.nonce.length < 24) return;
    if (used.has(data.nonce)) return;
    used.add(data.nonce);

    Promise.resolve()
      .then(() => sendExtensionMessage({ action: 'saveMarkdownToFile' }))
      .then(response => {
        window.postMessage({ type: resultType, nonce: data.nonce, ok: !!response?.success, response }, '*');
      })
      .catch(error => {
        window.postMessage({ type: resultType, nonce: data.nonce, ok: false, error: String(error?.message || error) }, '*');
      });
  });
})();
\`);
  }

  return stagedPath;
}

function camofoxAddonPaths() {
  const paths = (process.env.CAMOFOX_ADDONS || '')
    .split(',')
    .map(path => path.trim())
    .filter(Boolean);

  return paths.map(addonPath => {
    if (!fs.existsSync(addonPath) || !fs.existsSync(\`\${addonPath}/manifest.json\`)) {
      throw new Error(\`CAMOFOX_ADDONS path is not an extracted Firefox addon: \${addonPath}\`);
    }
    return spartanIsObsidianClipperAddon(addonPath)
      ? spartanStageObsidianClipperAddon(addonPath)
      : addonPath;
  });
}
`;

if (!source.includes('function camofoxAddonPaths()')) {
  if (!source.includes(helperMarker)) {
    throw new Error('expected Camofox browser globals marker not found');
  }
  source = source.replace(helperMarker, helper);
}

const safeErrorCodeMarker = `function safeError(err) {
  if (CONFIG.nodeEnv === 'production') {`;
const safeErrorCodeReplacement = `function safeError(err) {
  if (err.code) return err.message;
  if (CONFIG.nodeEnv === 'production') {`;
if (source.includes(safeErrorCodeMarker)) {
  source = source.replace(safeErrorCodeMarker, safeErrorCodeReplacement);
} else if (!source.includes('if (err.code) return err.message;')) {
  throw new Error('expected Camofox safeError marker not found');
}

const sendErrorCodeMarker = `  const body = { error: safeError(err), ...extraFields };
  if (err instanceof StaleRefsError) {`;
const sendErrorCodeReplacement = `  const body = { error: safeError(err), ...extraFields };
  if (err.code && !body.code) body.code = err.code;
  if (err instanceof StaleRefsError) {`;
if (source.includes(sendErrorCodeMarker)) {
  source = source.replace(sendErrorCodeMarker, sendErrorCodeReplacement);
} else if (!source.includes('if (err.code && !body.code) body.code = err.code;')) {
  throw new Error('expected Camofox sendError body marker not found');
}

const displayMarker = `    const useVirtualDisplay = !!vdDisplay;
    log('info', 'launching camoufox', {`;
const displayReplacement = `    const useVirtualDisplay = !!vdDisplay;
    const addonPaths = camofoxAddonPaths();
    log('info', 'launching camoufox', {`;
if (!source.includes('const addonPaths = camofoxAddonPaths();')) {
  if (!source.includes(displayMarker)) {
    throw new Error('expected Camofox launch log marker not found');
  }
  source = source.replace(displayMarker, displayReplacement);
}

const logMarker = `      virtualDisplay: useVirtualDisplay,
    });`;
const logReplacement = `      virtualDisplay: useVirtualDisplay,
      addons: addonPaths,
    });`;
if (!source.includes('      addons: addonPaths,\n    });')) {
  if (!source.includes(logMarker)) {
    throw new Error('expected Camofox launch log fields marker not found');
  }
  source = source.replace(logMarker, logReplacement);
}

const launchMarker = `        enable_cache: true,
        proxy: launchProxy,`;
const launchReplacement = `        enable_cache: true,
        addons: addonPaths,
        proxy: launchProxy,`;
if (!source.includes('        addons: addonPaths,')) {
  if (!source.includes(launchMarker)) {
    throw new Error('expected Camofox launchOptions marker not found');
  }
  source = source.replace(launchMarker, launchReplacement);
}

const clipperRouteMarker = `// Get captured downloads`;
const clipperRoute = `function spartanFindTabOwner(tabId) {
  for (const [userId, session] of sessions) {
    const found = findTab(session, tabId);
    if (found) return { userId, session, ...found };
  }
  return null;
}

function spartanDownloadRecordKey(record) {
  return String(record?.id || '');
}

async function spartanWaitForSingleNewDownload(tabState, beforeKeys, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    const candidates = (Array.isArray(tabState.downloads) ? tabState.downloads : [])
      .filter(download => !beforeKeys.has(spartanDownloadRecordKey(download)));

    if (candidates.length === 1) return candidates[0];
    if (candidates.length > 1) {
      const err = new Error('Multiple downloads were captured after clip request; refusing ambiguous result');
      err.statusCode = 409;
      err.code = 'ambiguous_download';
      throw err;
    }

    await new Promise(resolve => setTimeout(resolve, 100));
  }

  const err = new Error('Timed out waiting for Obsidian Web Clipper download');
  err.statusCode = 504;
  err.code = 'download_timeout';
  throw err;
}

async function spartanInvokeObsidianClipperBridge(page, timeoutMs) {
  const nonce = crypto.randomBytes(24).toString('hex');
  try {
    return await page.evaluate(({ nonce, token, timeoutMs }) => {
    const requestType = 'spartan-gate:obsidian-clipper:save-file';
    const resultType = 'spartan-gate:obsidian-clipper:result';
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        window.removeEventListener('message', onMessage);
        reject(new Error('Obsidian Web Clipper bridge did not respond'));
      }, timeoutMs);

      function onMessage(event) {
        const data = event.data;
        if (event.source !== window || !data || data.type !== resultType || data.nonce !== nonce) return;
        clearTimeout(timeout);
        window.removeEventListener('message', onMessage);
        if (data.ok) resolve(data.response || { success: true });
        else reject(new Error(data.error || data.response?.error || 'Obsidian Web Clipper bridge rejected request'));
      }

      window.addEventListener('message', onMessage);
      window.postMessage({ type: requestType, nonce, token }, '*');
    });
    }, { nonce, token: spartanObsidianClipperBridgeToken, timeoutMs });
  } catch (err) {
    if (String(err?.message || err).includes('Obsidian Web Clipper bridge did not respond')) {
      err.statusCode = 504;
      err.code = 'clipper_bridge_timeout';
    }
    throw err;
  }
}

app.post('/tabs/:tabId/extensions/obsidian-web-clipper/clip', authMiddleware(), async (req, res) => {
  try {
    const userId = req.body?.userId || req.query?.userId;
    if (!userId) return res.status(400).json({ error: 'userId is required', code: 'missing_user_id' });

    const normalizedUserId = normalizeUserId(userId);
    const session = sessions.get(normalizedUserId);
    const found = session && findTab(session, req.params.tabId);
    if (!found) {
      const owner = spartanFindTabOwner(req.params.tabId);
      if (owner) {
        return res.status(403).json({ error: 'Tab does not belong to requested userId', code: 'tab_user_mismatch' });
      }
      return tabNotFoundResponse(res, req.params.tabId);
    }

    const { tabState } = found;
    tabState.toolCalls++;
    session.lastAccess = Date.now();

    const mode = req.body?.mode || 'saveFile';
    if (mode !== 'saveFile') {
      return res.status(400).json({ error: 'Only mode=saveFile is supported', code: 'unsupported_mode' });
    }

    const waitForDownloadMsRaw = Number(req.body?.waitForDownloadMs);
    const waitForDownloadMs = Number.isFinite(waitForDownloadMsRaw)
      ? Math.min(Math.max(waitForDownloadMsRaw, 1000), 10000)
      : 10000;
    const includeData = req.body?.includeData === true;
    const maxBytesRaw = Number(req.body?.maxBytes);
    const maxBytes = Number.isFinite(maxBytesRaw) && maxBytesRaw > 0
      ? Math.min(maxBytesRaw, MAX_DOWNLOAD_INLINE_BYTES)
      : MAX_DOWNLOAD_INLINE_BYTES;

    const { bridge, downloadView } = await withTabLock(req.params.tabId, async () => {
      const beforeKeys = new Set((Array.isArray(tabState.downloads) ? tabState.downloads : []).map(spartanDownloadRecordKey));
      const bridge = await spartanInvokeObsidianClipperBridge(tabState.page, Math.min(waitForDownloadMs, 10000));
      const download = await spartanWaitForSingleNewDownload(tabState, beforeKeys, waitForDownloadMs);
      const [downloadView] = (await getDownloadsList(tabState, { includeData, maxBytes }))
        .filter(item => item.id === download.id);
      return { bridge, downloadView };
    });

    if (!downloadView) {
      const err = new Error('Captured download could not be read back');
      err.statusCode = 500;
      err.code = 'download_missing';
      throw err;
    }

    if (downloadView.dataBase64) {
      downloadView.sha256 = crypto.createHash('sha256').update(Buffer.from(downloadView.dataBase64, 'base64')).digest('hex');
      downloadView.encoding = 'base64';
    }

    res.json({
      ok: true,
      tabId: req.params.tabId,
      userId: normalizedUserId,
      mode,
      bridge,
      download: downloadView,
    });
  } catch (err) {
    failuresTotal.labels(classifyError(err), 'obsidian_clip').inc();
    log('error', 'obsidian clip failed', { reqId: req.reqId, tabId: req.params.tabId, error: err.message, code: err.code });
    res.status(err.statusCode || 500).json({ error: safeError(err), code: err.code || 'obsidian_clip_failed' });
  }
});

${clipperRouteMarker}`;
if (!source.includes('/tabs/:tabId/extensions/obsidian-web-clipper/clip')) {
  if (!source.includes(clipperRouteMarker)) {
    throw new Error('expected Camofox downloads route marker not found');
  }
  source = source.replace(clipperRouteMarker, clipperRoute);
}

const navigateProxyErrorPattern = /(\n\s*} else \{\n)(\s*)throw navErr;\n(\s*}\n\s*})/;
let navigateProxyErrorPatchCount = 0;
source = source.replace(new RegExp(navigateProxyErrorPattern.source, 'g'), (_match, prefix, indent, suffix) => {
  navigateProxyErrorPatchCount++;
  return (
    `${prefix}${indent}if (isProxyError(navErr)) {\n`
    + `${indent}  navErr.statusCode = 403;\n`
    + `${indent}  navErr.code = 'proxy_forbidden';\n`
    + `${indent}}\n`
    + `${indent}throw navErr;\n${suffix}`
  );
});
if (navigateProxyErrorPatchCount === 0 && !source.includes("navErr.code = 'proxy_forbidden'")) {
  throw new Error('expected Camofox navigate proxy error marker not found');
}


const closeTabRouteStart = "app.delete('/tabs/:tabId', async (req, res) => {";
const closeTabRouteEnd = "\n});\n\n// Close tab group";
const closeTabRouteStartIndex = source.indexOf(closeTabRouteStart);
if (closeTabRouteStartIndex !== -1) {
  const closeTabRouteEndIndex = source.indexOf(closeTabRouteEnd, closeTabRouteStartIndex);
  if (closeTabRouteEndIndex === -1) throw new Error('expected Camofox close-tab route end marker not found');

  const closeTabRoute = source.slice(closeTabRouteStartIndex, closeTabRouteEndIndex);
  if (!closeTabRoute.includes('await withTabLock(req.params.tabId, async () =>')) {
    const foundMarker = "    if (found) {\n      if (found.tabState.navigateAbort) found.tabState.navigateAbort.abort();";
    const cleanupMarker = "      { const _l = tabLocks.get(req.params.tabId); if (_l) _l.drain(); tabLocks.delete(req.params.tabId); refreshTabLockQueueDepth(); }\n";
    const logMarker = "      log('info', 'tab closed', { reqId: req.reqId, tabId: req.params.tabId, userId });";
    if (!closeTabRoute.includes(foundMarker) || !closeTabRoute.includes(cleanupMarker) || !closeTabRoute.includes(logMarker)) {
      throw new Error('expected Camofox close-tab locking anchors not found');
    }
    const lockedCloseTabRoute = closeTabRoute
      .replace(foundMarker, "    if (found) {\n      await withTabLock(req.params.tabId, async () => {\n        if (found.tabState.navigateAbort) found.tabState.navigateAbort.abort();")
      .replace(cleanupMarker, '')
      .replace(logMarker, logMarker + "\n      });\n      { const _l = tabLocks.get(req.params.tabId); if (_l) _l.drain(); tabLocks.delete(req.params.tabId); refreshTabLockQueueDepth(); }");
    source = source.slice(0, closeTabRouteStartIndex) + lockedCloseTabRoute + source.slice(closeTabRouteEndIndex);
  }
}

const nativeMemResetMarker = `  // Reset native memory baseline so next browser measures from fresh
  reporter.resetNativeMemBaseline();
  _nativeMemBaseline = null;`;
const nativeMemResetReplacement = `  // Reset native memory baseline so next browser measures from fresh
  if (typeof reporter.resetNativeMemBaseline === 'function') {
    reporter.resetNativeMemBaseline();
  }
  _nativeMemBaseline = null;`;
if (source.includes(nativeMemResetMarker)) {
  source = source.replace(nativeMemResetMarker, nativeMemResetReplacement);
} else if (!source.includes("typeof reporter.resetNativeMemBaseline === 'function'")) {
  throw new Error('expected Camofox native memory reset marker not found');
}

fs.writeFileSync(path, source);
