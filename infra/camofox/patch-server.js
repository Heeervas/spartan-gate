const fs = require('fs');

const path = 'server.js';
let source = fs.readFileSync(path, 'utf8');

function installSingleAttemptClick(input) {
  const start = input.indexOf("app.post('/tabs/:tabId/click', async (req, res) => {");
  const end = input.indexOf('// --- /tabs/:tabId/upload timeouts (ms) ---', start);
  if (start === -1 || end === -1) throw new Error('expected Camofox single-attempt click route boundaries');
  let route = input.slice(start, end);
  const guard = '        const click = async (options) => clickWithDownloadGuard(tabState, () => locator.click(options));';
  const singleAttempt = `
        // Spartan opt-in: never replay an ambiguous click, including Google SERPs.
        if (req.body.singleAttempt === true) {
          await click({ timeout: Math.max(1, Math.min(3000, remainingBudget())) });
          return;
        }`;
  if (!route.includes(singleAttempt)) {
    if (route.split(guard).length !== 2) throw new Error('expected one Camofox guarded locator click');
    route = route.replace(guard, guard + singleAttempt);
  }
  const installedGuard = guard + singleAttempt;
  if (route.split(installedGuard).length !== 2 || route.indexOf(installedGuard) > route.indexOf('        if (onGoogleSerp)')) {
    throw new Error('Camofox single-attempt click implementation is incomplete');
  }
  input = input.slice(0, start) + route + input.slice(end);
  const capability = '    singleAttemptBrowserClick: true,\n';
  const capabilityAnchor = '    cleanupFailedOperatorNavigation: true,\n';
  if (!input.includes(capability)) {
    if (input.split(capabilityAnchor).length !== 2) throw new Error('expected one Camofox capability anchor');
    input = input.replace(capabilityAnchor, capabilityAnchor + capability);
  }
  if (input.split(capability).length !== 2) throw new Error('duplicate Camofox single-attempt click capability');
  return input;
}

if (source.includes('SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1')) {
  for (const [marker, label] of [
    ['SPARTAN_LIFECYCLE_COORDINATOR_VERSION = 1', 'lifecycle coordinator'],
    ['spartanTrackLifecyclePromise(promise)', 'timeout retention'],
    ['if (timer) clearTimeout(timer);', 'timeout timer cleanup'],
    ['if (state.queue.length >= MAX_SESSIONS)', 'bounded per-user waiters'],
    ['currentSession !== sourceSession', 'popup generation fence'],
    ["code: 'legacy_tab_open_disabled'", 'legacy tabs-open disablement'],
    ['await session.context.close();', 'context-close error propagation'],
    ['Spartan queued-create fence: pressure cleanup.', 'pressure cleanup create fence'],
    ['Spartan queued-create fence: tab reaper.', 'tab reaper create fence'],
    ['spartanBindPendingCreateSession(userId, spartanCreateFence, session)', 'create generation bind'],
    ['closeLeasedPage(late.session, late.page, late.lease)', 'late leased-page cleanup'],
    ["app.get('/spartan/health/deep'", 'deep health'],
    ["app.get('/live'", 'liveness'],
    ['Spartan: periodic profile cleanup disabled during browser ownership transitions.', 'live profile protection'],
    ['if (isNavigationTimeout && proxyPool?.canRotateSessions)', 'upstream timeout proxy guard'],
    ["geoip: process.env.CAMOFOX_GEOIP !== 'false' && !!launchProxy,", 'proxy geoip policy'],
    ["destroyTab(timedOutSession, timedOutTabId, 'navigation_timeout'", 'targeted timeout retirement'],
  ]) {
    if (!source.includes(marker)) throw new Error(`Camofox 1.15 patch marker is incomplete: ${label}`);
  }
  const updated = installSingleAttemptClick(source);
  if (updated !== source) fs.writeFileSync(path, updated);
  process.exit(0);
}

const spartanHealthImportV1 = "import { createSpartanDeepHealthController, spartanInternalRequestAuthorized, spartanCgroupAdmissionAllowed } from './spartan-health.mjs';\n";
const spartanHealthImport = "import { createSpartanDeepHealthController, createSpartanNavigationReadiness, spartanInternalRequestAuthorized, spartanCgroupAdmissionAllowed } from './spartan-health.mjs';\n";
if (source.includes(spartanHealthImportV1)) {
  source = source.replace(spartanHealthImportV1, spartanHealthImport);
} else if (!source.includes(spartanHealthImport)) {
  const importMarker = "import fs from 'fs';\n";
  if (!source.includes(importMarker)) throw new Error('expected Camofox fs import marker not found');
  source = source.replace(importMarker, importMarker + spartanHealthImport);
}

const spartanLifecycleImport = "import { createSpartanBrowserLifecycleCoordinator, spartanBrowserLifecycleError } from './spartan-lifecycle.mjs';\n";
if (!source.includes(spartanLifecycleImport)) {
  const importMarker = "import fs from 'fs';\n";
  if (!source.includes(importMarker)) throw new Error('expected Camofox fs import marker not found');
  source = source.replace(importMarker, importMarker + spartanLifecycleImport);
}

const spartanChildProcessRequire = "const spartanChildProcess = require('child_process');\n";
const spartanChildProcessImport = "import * as spartanChildProcess from 'child_process';\n";

source = source.replaceAll(spartanChildProcessRequire, '');
source = source.replaceAll(spartanChildProcessImport, '');

const helperMarker = `let virtualDisplay = null;
let browserLaunchProxy = null;
let externalCamoufoxLaunch = null;
`;
const helper = `${helperMarker}
const spartanObsidianClipperBridgeToken = crypto.randomBytes(32).toString('hex');
const spartanViewportRelaunchLock = { tail: Promise.resolve() };
let spartanLaunchViewportWindow = null;

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


  if (paths.length > 0 && !CONFIG.disableDefaultAddons) {
    throw new Error(
      'CAMOFOX_ADDONS requires CAMOFOX_DISABLE_DEFAULT_ADDONS=true: bundled Camoufox cannot preserve default UBO blocking when custom add-ons are loaded',
    );
  }
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

const spartanViewportGlobalsMarker = "const spartanObsidianClipperBridgeToken = crypto.randomBytes(32).toString('hex');\n";
source = source.replaceAll("const spartanViewportWindowLock = { tail: Promise.resolve() };\n", '');
source = source.replaceAll("const spartanViewportRelaunchLock = { tail: Promise.resolve() };\nlet spartanLaunchViewportWindow = null;\n", '');
if (!source.includes('const spartanViewportRelaunchLock') && source.includes('function camofoxAddonPaths()')) {
  if (!source.includes(spartanViewportGlobalsMarker)) {
    throw new Error('expected Camofox Spartan helper globals marker not found');
  }
  source = source.replace(
    spartanViewportGlobalsMarker,
    spartanViewportGlobalsMarker + "const spartanViewportRelaunchLock = { tail: Promise.resolve() };\nlet spartanLaunchViewportWindow = null;\n",
  );
}

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
const upstreamStructuredErrors = source.includes('const code = browserErrorCode(err);')
  && source.includes('retryable: isRetryableBrowserError(err),');
if (source.includes(safeErrorCodeMarker) && !upstreamStructuredErrors) {
  source = source.replace(safeErrorCodeMarker, safeErrorCodeReplacement);
} else if (!upstreamStructuredErrors && !source.includes('if (err.code) return err.message;')) {
  throw new Error('expected Camofox safeError marker not found');
}

const sendErrorCodeMarker = `  const body = { error: safeError(err), ...extraFields };
  if (err instanceof StaleRefsError) {`;
const sendErrorCodeV1 = `  const body = { error: safeError(err), ...extraFields };
  if (err.code && !body.code) body.code = err.code;
  if (err instanceof StaleRefsError) {`;
const sendErrorCodeReplacement = `  const body = { error: err.spartanPublicMessage || safeError(err), ...extraFields };
  if (err.code && !body.code) body.code = err.code;
  if (err.failureClass && !body.failureClass) body.failureClass = err.failureClass;
  if (err instanceof StaleRefsError) {`;
const upstreamSendErrorMarker = `  const body = {
    error: safeError(err),
    retryable: isRetryableBrowserError(err),
    ...extraFields,
  };
  if (code) body.code = code;`;
const upstreamSendErrorReplacement = `  const body = {
    error: err.spartanPublicMessage || safeError(err),
    retryable: isRetryableBrowserError(err),
    ...extraFields,
  };
  if (code) body.code = code;
  if (err.failureClass && !body.failureClass) body.failureClass = err.failureClass;`;
if (source.includes(upstreamSendErrorMarker)) {
  source = source.replace(upstreamSendErrorMarker, upstreamSendErrorReplacement);
} else if (source.includes(sendErrorCodeMarker)) {
  source = source.replace(sendErrorCodeMarker, sendErrorCodeReplacement);
} else if (source.includes(sendErrorCodeV1)) {
  source = source.replace(sendErrorCodeV1, sendErrorCodeReplacement);
} else if (!source.includes('if (err.code && !body.code) body.code = err.code;')) {
  throw new Error('expected Camofox sendError body marker not found');
}

const displayMarker = `    const useVirtualDisplay = !!vdDisplay;
    log('info', 'launching camoufox', {`;
const displayReplacement = `    const useVirtualDisplay = !!vdDisplay;
    const addonPaths = camofoxAddonPaths();
    log('info', 'launching camoufox', {`;
if (!source.includes('const addonPaths = camofoxAddonPaths();')) {
  if (source.includes(displayMarker)) {
    source = source.replace(displayMarker, displayReplacement);
  } else if (source.includes('    const useVirtualDisplay = !!vdDisplay;\n')) {
    source = source.replace(
      '    const useVirtualDisplay = !!vdDisplay;\n',
      '    const useVirtualDisplay = !!vdDisplay;\n    const addonPaths = camofoxAddonPaths();\n',
    );
  } else {
    throw new Error('expected Camofox launch log marker not found');
  }
}

const logMarker = `      virtualDisplay: useVirtualDisplay,
    });`;
const logReplacement = `      virtualDisplay: useVirtualDisplay,
      addons: addonPaths,
    });`;
if (!source.includes('      addons: addonPaths,\n')) {
  if (source.includes(logMarker)) {
    source = source.replace(logMarker, logReplacement);
  } else if (source.includes('      interactiveMode: CONFIG.interactiveMode,\n')) {
    source = source.replace(
      '      interactiveMode: CONFIG.interactiveMode,\n',
      '      interactiveMode: CONFIG.interactiveMode,\n      addons: addonPaths,\n',
    );
  } else {
    throw new Error('expected Camofox launch log fields marker not found');
  }
}

const launchMarker = `        enable_cache: true,
        proxy: launchProxy,`;
const launchReplacement = `        enable_cache: true,
        addons: addonPaths,
        window: spartanLaunchViewportWindow?.window,
        proxy: launchProxy,`;
if (!source.includes('        addons: addonPaths,')) {
  if (!source.includes(launchMarker)) {
    throw new Error('expected Camofox launchOptions marker not found');
  }
  source = source.replace(launchMarker, launchReplacement);
}
if (!source.includes('        window: spartanLaunchViewportWindow?.window,')) {
  const windowLaunchMarker = `        addons: addonPaths,
        proxy: launchProxy,`;
  const windowLaunchReplacement = `        addons: addonPaths,
        window: spartanLaunchViewportWindow?.window,
        proxy: launchProxy,`;
  if (!source.includes(windowLaunchMarker)) {
    throw new Error('expected Camofox launchOptions window marker not found');
  }
  source = source.replace(windowLaunchMarker, windowLaunchReplacement);
}

function spartanFindCallExpressionEnd(text, start) {
  const open = text.indexOf('(', start);
  if (open === -1) return -1;

  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;

  for (let index = open; index < text.length; index++) {
    const char = text[index];
    const next = text[index + 1];

    if (lineComment) {
      if (char === '\n') lineComment = false;
      continue;
    }
    if (blockComment) {
      if (char === '*' && next === '/') {
        blockComment = false;
        index++;
      }
      continue;
    }
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (char === '\\') {
        escaped = true;
      } else if (char === quote) {
        quote = null;
      }
      continue;
    }

    if (char === '/' && next === '/') {
      lineComment = true;
      index++;
      continue;
    }
    if (char === '/' && next === '*') {
      blockComment = true;
      index++;
      continue;
    }
    if (char === '\'' || char === '"' || char === '`') {
      quote = char;
      continue;
    }
    if (char === '(') {
      depth++;
      continue;
    }
    if (char === ')') {
      depth--;
      if (depth === 0) {
        let end = index + 1;
        while (end < text.length && /\s/.test(text[end])) end++;
        if (text[end] === ';') end++;
        return end;
      }
    }
  }
  return -1;
}

function spartanReplaceFirstRouteCall(text, prefixes, replacement) {
  for (const prefix of prefixes) {
    const start = text.indexOf(prefix);
    if (start === -1) continue;
    const end = spartanFindCallExpressionEnd(text, start);
    if (end === -1) throw new Error('expected Camofox viewport route call end not found');
    return { source: text.slice(0, start) + replacement + text.slice(end), replaced: true };
  }
  return { source: text, replaced: false };
}

const upstreamViewportDisabledRoute = `app.post('/tabs/:tabId/upstream-viewport-disabled', authMiddleware(), async (_req, res) => {
  res.status(410).json({
    ok: false,
    error: 'The upstream viewport route is disabled because it does not verify effective runtime dimensions; use /tabs/{tabId}/spartan/viewport',
    code: 'upstream_viewport_disabled',
  });
});
`;
if (!source.includes("code: 'upstream_viewport_disabled'")) {
  const replaced = spartanReplaceFirstRouteCall(source, [
    "app.post('/tabs/:tabId/viewport',",
    'app.post("/tabs/:tabId/viewport",',
    "app.post('/tabs/:tabId/upstream-viewport-disabled',",
    'app.post("/tabs/:tabId/upstream-viewport-disabled",',
  ], upstreamViewportDisabledRoute);
  source = replaced.source;
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

const viewportRouteMarker = `function spartanDownloadRecordKey(record) {`;
const viewportRouteOnly = `function spartanViewportBody(req) {
  const payload = req.body?.viewport && typeof req.body.viewport === 'object'
    ? req.body.viewport
    : (req.body || {});
  return payload;
}

function spartanParseViewportInteger(value, name, min, max) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    const err = new Error('Invalid viewport ' + name + '; expected integer ' + min + '-' + max);
    err.statusCode = 400;
    err.code = 'invalid_viewport';
    throw err;
  }
  return parsed;
}

function spartanUnsupportedViewportOptions(payload, rootPayload = null) {
  const names = ['deviceScaleFactor', 'isMobile', 'hasTouch', 'touch', 'userAgent'];
  const found = new Set();
  for (const candidate of [payload, rootPayload]) {
    if (!candidate || typeof candidate !== 'object') continue;
    for (const name of names) {
      if (Object.prototype.hasOwnProperty.call(candidate, name)) found.add(name);
    }
  }
  return Array.from(found);
}

function spartanViewportError(message, statusCode, code) {
  const err = new Error(message);
  err.statusCode = statusCode;
  err.code = code;
  return err;
}

function spartanSleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function spartanViewportMatches(requested, effective) {
  return Math.abs(Number(effective?.innerWidth) - requested.width) <= 2
    && Math.abs(Number(effective?.innerHeight) - requested.height) <= 2;
}

function spartanNativeOuterTarget(requested, effective) {
  const chromeWidth = Math.max(0, Number(effective?.outerWidth || 0) - Number(effective?.innerWidth || 0));
  const chromeHeight = Math.max(0, Number(effective?.outerHeight || 0) - Number(effective?.innerHeight || 0));
  return {
    width: requested.width + chromeWidth,
    height: requested.height + chromeHeight,
    chromeWidth,
    chromeHeight,
  };
}

function spartanWithViewportRelaunchLock(callback) {
  const run = spartanViewportRelaunchLock.tail.catch(() => undefined).then(callback);
  spartanViewportRelaunchLock.tail = run.catch(() => undefined);
  return run;
}

async function spartanEvaluateViewport(page) {
  return await page.evaluate(() => ({
    href: String(location.href).slice(0, 160),
    title: document.title,
    readyState: document.readyState,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    screenWidth: window.screen?.width ?? null,
    screenHeight: window.screen?.height ?? null,
    visualViewportWidth: window.visualViewport?.width ?? null,
    visualViewportHeight: window.visualViewport?.height ?? null,
    devicePixelRatio: window.devicePixelRatio,
    maxWidth767: window.matchMedia('(max-width: 767px)').matches,
    maxWidth430: window.matchMedia('(max-width: 430px)').matches,
    userAgent: navigator.userAgent,
    hasTouch: (navigator.maxTouchPoints || 0) > 0,
  }));
}

async function spartanRelaunchViewportTab(options) {
  const { userId, sessionKey, tabId, tabState, requested, effective, timeoutMs } = options;
  if (!spartanBrowserLifecycle.hasLease()) {
    const lifecycleTimeoutMs = Number.isFinite(Number(timeoutMs)) && Number(timeoutMs) > 0
      ? Number(timeoutMs)
      : requestTimeoutMs();
    return await spartanBrowserLifecycle.run(
      'viewport_relaunch',
      () => spartanRelaunchViewportTab(options),
      {
        priority: 'foreground',
        waitTimeoutMs: Math.max(1, lifecycleTimeoutMs - 1000),
        deadlineAt: Date.now() + lifecycleTimeoutMs,
      },
    );
  }
  const resumeUrl = String(tabState.page?.url?.() || '');
  const canResumeUrl = /^https?:\/\//i.test(resumeUrl);
  const target = spartanNativeOuterTarget(requested, effective);
  const launchWindow = [target.width, target.height];
  spartanLaunchViewportWindow = {
    requested,
    window: launchWindow,
    chromeWidth: target.chromeWidth,
    chromeHeight: target.chromeHeight,
    updatedAt: new Date().toISOString(),
  };

  await closeAllSessions('viewport_relaunch', { clearDownloads: true, clearLocks: true });
  await closeBrowserFully('viewport_relaunch');
  browserLaunchPromise = null;
  await ensureBrowser();

  const session = await getSession(userId);
  const group = getTabGroup(session, sessionKey);
  const page = await session.context.newPage();
  if (typeof page.setViewportSize === 'function') {
    await page.setViewportSize(requested);
  }
  const nextTabState = createTabState(page);
  nextTabState.lastRequestedUrl = canResumeUrl ? resumeUrl : null;
  attachDownloadListener(nextTabState, tabId, log, pluginEvents, userId);
  group.set(tabId, nextTabState);
  attachPopupHandler(page, userId, sessionKey);
  refreshActiveTabsGauge();

  if (canResumeUrl) {
    await page.goto(resumeUrl, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
    nextTabState.visitedUrls.add(resumeUrl);
  }

  const relaunchedEffective = await spartanEvaluateViewport(page);
  return {
    strategy: 'camoufoxLaunchWindow',
    effective: relaunchedEffective,
    relaunch: {
      resumeUrl: canResumeUrl ? resumeUrl : null,
      requested,
      launchWindow,
      chromeWidth: target.chromeWidth,
      chromeHeight: target.chromeHeight,
      closesOtherSessions: true,
    },
  };
}

app.post(['/tabs/:tabId/spartan/viewport', '/tabs/:tabId/viewport'], authMiddleware(), async (req, res) => {
  let requested = null;
  let before = null;
  let effective = null;
  let strategy = 'pageViewport';
  const attempts = [];
  try {
    const userId = req.body?.userId || req.query?.userId;
    if (!userId) return res.status(400).json({ ok: false, error: 'userId is required', code: 'missing_user_id' });

    const normalizedUserId = normalizeUserId(userId);
    const session = sessions.get(normalizedUserId);
    const found = session && findTab(session, req.params.tabId);
    if (!found) {
      const owner = spartanFindTabOwner(req.params.tabId);
      if (owner) {
        return res.status(403).json({ ok: false, error: 'Tab does not belong to requested userId', code: 'tab_user_mismatch' });
      }
      return tabNotFoundResponse(res, req.params.tabId);
    }

    const payload = spartanViewportBody(req);
    requested = {
      width: spartanParseViewportInteger(payload.width, 'width', 100, 4096),
      height: spartanParseViewportInteger(payload.height, 'height', 100, 4096),
    };

    const unsupported = spartanUnsupportedViewportOptions(payload, req.body || {});
    if (unsupported.length > 0) {
      const err = new Error('Unsupported live viewport option(s): ' + unsupported.join(', '));
      err.statusCode = 400;
      err.code = 'unsupported_viewport_options';
      throw err;
    }

    const timeoutMsRaw = Number(payload.timeoutMs);
    const timeoutMs = Number.isFinite(timeoutMsRaw)
      ? Math.min(Math.max(timeoutMsRaw, 1000), 60000)
      : 10000;

    const { tabState } = found;
    tabState.toolCalls++;
    session.lastAccess = Date.now();

    const result = await withTabLock(req.params.tabId, async () => {
      if (typeof tabState.page?.setViewportSize !== 'function') {
        const err = new Error('Camofox page does not support live viewport resizing');
        err.statusCode = 501;
        err.code = 'viewport_resize_unsupported';
        throw err;
      }

      const page = tabState.page;
      const shouldReload = payload.reload === true && typeof page.reload === 'function';
      before = await spartanEvaluateViewport(page);
      await page.setViewportSize(requested);
      effective = await spartanEvaluateViewport(page);
      attempts.push({ strategy: 'pageViewport', effective });
      if (spartanViewportMatches(requested, effective)) {
        if (shouldReload) {
          await page.reload({ waitUntil: 'domcontentloaded', timeout: timeoutMs });
          effective = await spartanEvaluateViewport(page);
          attempts.push({ strategy: 'pageViewportReload', effective });
        }
        if (spartanViewportMatches(requested, effective)) return { strategy: 'pageViewport', effective };
      }

      if (payload.relaunch === true) {
        strategy = 'camoufoxLaunchWindow';
        return await spartanWithViewportRelaunchLock(async () => {
          const relaunched = await spartanRelaunchViewportTab({
            userId: normalizedUserId,
            sessionKey: found.listItemId,
            tabId: req.params.tabId,
            tabState,
            requested,
            effective,
            timeoutMs,
          });
          attempts.push({ strategy: 'camoufoxLaunchWindow', relaunch: relaunched.relaunch, effective: relaunched.effective });
          return relaunched;
        });
      }

      return { strategy: 'pageViewport', effective };
    });

    strategy = result.strategy;
    effective = result.effective;
    if (!spartanViewportMatches(requested, effective)) {
      const err = new Error('Viewport did not take effect in page runtime');
      err.statusCode = 409;
      err.code = 'viewport_mismatch';
      err.requested = requested;
      err.before = before;
      err.effective = effective;
      err.strategy = strategy;
      err.attempts = attempts;
      err.relaunchSupported = true;
      err.relaunchRequired = payload.relaunch !== true;
      err.hint = payload.relaunch === true
        ? 'Camoufox launch window relaunch completed but runtime dimensions still did not match'
        : 'Retry with relaunch=true to restart Camoufox with a launch-time window size';
      throw err;
    }

    res.json({
      ok: true,
      tabId: req.params.tabId,
      userId: normalizedUserId,
      requested,
      before,
      effective,
      strategy,
      attempts,
      relaunch: result.relaunch || undefined,
    });
  } catch (err) {
    failuresTotal.labels(classifyError(err), 'viewport').inc();
    log('error', 'viewport resize failed', { reqId: req.reqId, tabId: req.params.tabId, error: err.message, code: err.code, strategy });
    res.status(err.statusCode || 500).json({
      ok: false,
      error: safeError(err),
      code: err.code || 'viewport_failed',
      requested: err.requested || requested || undefined,
      before: err.before || before || undefined,
      effective: err.effective || effective || undefined,
      strategy: err.strategy || strategy || undefined,
      attempts: err.attempts || attempts || undefined,
      relaunchSupported: err.relaunchSupported || undefined,
      relaunchRequired: err.relaunchRequired || undefined,
      hint: err.hint || undefined,
    });
  }
});

${viewportRouteMarker}`;
if (!source.includes('/tabs/:tabId/viewport')) {
  if (!source.includes('function spartanFindTabOwner(tabId)')) {
    throw new Error('expected Camofox tab owner helper marker not found');
  }
  if (!source.includes(viewportRouteMarker)) {
    throw new Error('expected Camofox viewport route marker not found');
  }
  source = source.replace(viewportRouteMarker, viewportRouteOnly);
}

const createTabRouteStart = "app.post('/tabs', async (req, res) => {";
const createTabRouteNext = "app.post('/tabs/:tabId/navigate', async (req, res) => {";
const operatorCapabilityRouteMarker = "app.get('/spartan/capabilities'";
if (!source.includes(operatorCapabilityRouteMarker)) {
  if (!source.includes(createTabRouteStart)) {
    throw new Error('expected Camofox create-tab route marker not found');
  }
  const operatorCapabilityRoute = `app.get('/spartan/capabilities', authMiddleware(), (_req, res) => {
  res.json({
    operatorTabIsolationVersion: 1,
    nonDestructiveTabCreate: true,
    cleanupFailedOperatorNavigation: true,
  });
});

`;
  source = source.replace(createTabRouteStart, operatorCapabilityRoute + createTabRouteStart);
}

const createTabRouteStartIndex = source.indexOf(createTabRouteStart);
const createTabRouteNextIndex = source.indexOf(createTabRouteNext, createTabRouteStartIndex);
if (createTabRouteStartIndex === -1 || createTabRouteNextIndex === -1) {
  throw new Error('expected Camofox create-tab route markers not found');
}
let createTabRoute = source.slice(createTabRouteStartIndex, createTabRouteNextIndex);
if (!createTabRoute.includes('const spartanNonDestructiveCreate')) {
  const requestMarker = "    const { userId, sessionKey, listItemId, url, trace } = req.body;\n";
  const capacityMarker = `      if (totalTabs >= MAX_TABS_PER_SESSION || getTotalTabCount() >= MAX_TABS_GLOBAL) {
        const recycled = await recycleOldestTab(session, req.reqId, userId);
        if (!recycled) {
          throw Object.assign(new Error('Maximum tabs per session reached'), { statusCode: 429 });
        }
      }`;
  const compactCapacityMarker = `      if (totalTabs >= MAX_TABS_PER_SESSION || getTotalTabCount() >= MAX_TABS_GLOBAL) {
        const recycled = await recycleOldestTab(session, req.reqId, userId);
        if (!recycled) throw Object.assign(new Error('Maximum tabs per session reached'), { statusCode: 429 });
      }`;
  const capacityReplacement = `      if (totalTabs >= MAX_TABS_PER_SESSION || getTotalTabCount() >= MAX_TABS_GLOBAL) {
        if (spartanNonDestructiveCreate) {
          const err = new Error('Maximum tabs reached; refusing to recycle an active tab');
          err.statusCode = 429;
          err.code = 'tab_capacity_reached';
          throw err;
        }
        const recycled = await recycleOldestTab(session, req.reqId, userId);
        if (!recycled) {
          throw Object.assign(new Error('Maximum tabs per session reached'), { statusCode: 429 });
        }
      }`;
  const retryMarker = "          if ((isProxyError(navErr) || isTimeoutError(navErr)) && proxyPool?.canRotateSessions) {";

  if (!createTabRoute.includes(requestMarker)) {
    throw new Error('expected Camofox create-tab request marker not found');
  }
  const capacityAnchor = createTabRoute.includes(capacityMarker)
    ? capacityMarker
    : (createTabRoute.includes(compactCapacityMarker) ? compactCapacityMarker : null);
  if (!capacityAnchor) {
    throw new Error('expected Camofox create-tab capacity marker not found');
  }
  if (!createTabRoute.includes(retryMarker)) {
    throw new Error('expected Camofox create-tab retry marker not found');
  }

  createTabRoute = createTabRoute
    .replace(
      requestMarker,
      requestMarker + "    const spartanNonDestructiveCreate = req.body?.nonDestructive === true;\n",
    )
    .replace(capacityAnchor, capacityReplacement)
    .replace(
      retryMarker,
      "          if (!spartanNonDestructiveCreate && (isProxyError(navErr) || isTimeoutError(navErr)) && proxyPool?.canRotateSessions) {",
    );
}

if (!createTabRoute.includes('const spartanCleanupCreatedTab')) {
  const legacyCleanupMarker = `      group.set(tabId, tabState);
      attachPopupHandler(page, userId, resolvedSessionKey);`;
  const leasedCleanupMarker = `      attachPopupHandler(page, userId, resolvedSessionKey);
      refreshActiveTabsGauge();`;
  const cleanupMarker = createTabRoute.includes(legacyCleanupMarker)
    ? legacyCleanupMarker
    : leasedCleanupMarker;
  const cleanupReplacement = `${cleanupMarker}
      const spartanCleanupCreatedTab = async () => {
        if (tabState.navigateAbort) tabState.navigateAbort.abort();
        await clearTabDownloads(tabState).catch(() => {});
        await safePageClose(tabState.page);
        group.delete(tabId);
        if (group.size === 0) session.tabGroups.delete(resolvedSessionKey);
        refreshActiveTabsGauge();
      };`;
  const throwMarker = "            throw navErr;\n";
  const throwCount = createTabRoute.split(throwMarker).length - 1;

  if (!createTabRoute.includes(cleanupMarker)) {
    throw new Error('expected Camofox created-tab cleanup marker not found');
  }
  if (throwCount !== 1) {
    throw new Error('expected one Camofox create-tab navigation throw marker');
  }

  let cleanupBeforeThrow = "            if (spartanNonDestructiveCreate) await spartanCleanupCreatedTab();\n";
  if (!createTabRoute.includes("navErr.code = 'proxy_forbidden'")) {
    cleanupBeforeThrow = `            if (isProxyError(navErr)) {
              navErr.statusCode = 403;
              navErr.code = 'proxy_forbidden';
            }
` + cleanupBeforeThrow;
  }
  createTabRoute = createTabRoute
    .replace(cleanupMarker, cleanupReplacement)
    .replace(throwMarker, cleanupBeforeThrow + throwMarker);
}

source = source.slice(0, createTabRouteStartIndex) + createTabRoute + source.slice(createTabRouteNextIndex);

const operatorTabIsolationRevisionMarker = '// spartan-camofox-operator-tab-isolation-v1';
const operatorTabIsolationRequiredMarkers = [
  [operatorCapabilityRouteMarker, 'capability route'],
  ['operatorTabIsolationVersion: 1', 'capability version'],
  ['nonDestructiveTabCreate: true', 'non-destructive capability'],
  ['cleanupFailedOperatorNavigation: true', 'cleanup capability'],
  ['const spartanNonDestructiveCreate = req.body?.nonDestructive === true;', 'request opt-in'],
  ["err.code = 'tab_capacity_reached';", 'capacity rejection'],
  ['if (!spartanNonDestructiveCreate && (isProxyError(navErr) || isTimeoutError(navErr))', 'rotation guard'],
  ['const spartanCleanupCreatedTab = async () => {', 'cleanup helper'],
  ['if (spartanNonDestructiveCreate) await spartanCleanupCreatedTab();', 'cleanup invocation'],
];
for (const [marker, label] of operatorTabIsolationRequiredMarkers) {
  const count = source.split(marker).length - 1;
  if (count !== 1) {
    throw new Error('Camofox operator tab isolation v1 marker is incomplete: ' + label);
  }
}
const operatorTabIsolationRevisionCount = source.split(operatorTabIsolationRevisionMarker).length - 1;
if (operatorTabIsolationRevisionCount > 1) {
  throw new Error('duplicate Camofox operator tab isolation v1 revision marker');
}
if (operatorTabIsolationRevisionCount === 0) {
  source = source.replace(
    operatorCapabilityRouteMarker,
    operatorTabIsolationRevisionMarker + '\n' + operatorCapabilityRouteMarker,
  );
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

// Camofox 1.15 ships targeted per-user recovery and page leases. Compose the
// Spartan lifecycle boundary around those primitives instead of rewriting them
// back to the pre-1.12 server shape.
const spartanModernUpstream = source.includes('const sessionCreations = new Map();')
  && source.includes('createPageWithRecoveryForUser');
if (spartanModernUpstream) {
  function modernReplaceOnce(marker, replacement, label) {
    const count = source.split(marker).length - 1;
    if (count !== 1) throw new Error(`expected one Camofox 1.15 ${label} marker, found ${count}`);
    source = source.replace(marker, replacement);
  }

  const modernRuntimeAnchor = 'const userConcurrency = new Map();';
  const modernRuntime = `const userConcurrency = new Map();

const SPARTAN_LIFECYCLE_COORDINATOR_VERSION = 1;
const SPARTAN_LIFECYCLE_COORDINATOR_REVISION = 3;
const SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1;
const SPARTAN_MAX_CONCURRENT_CREATES_RAW = Number(process.env.SPARTAN_CAMOFOX_MAX_CONCURRENT_CREATES || '1');
if (!Number.isInteger(SPARTAN_MAX_CONCURRENT_CREATES_RAW) || SPARTAN_MAX_CONCURRENT_CREATES_RAW !== 1) {
  throw new Error('SPARTAN_CAMOFOX_MAX_CONCURRENT_CREATES must be exactly 1');
}
const SPARTAN_RECOVERY_DRAIN_TIMEOUT_MS = Number(process.env.SPARTAN_CAMOFOX_RECOVERY_DRAIN_TIMEOUT_MS || 2000);
if (!Number.isFinite(SPARTAN_RECOVERY_DRAIN_TIMEOUT_MS) || SPARTAN_RECOVERY_DRAIN_TIMEOUT_MS < 1) {
  throw new Error('SPARTAN_CAMOFOX_RECOVERY_DRAIN_TIMEOUT_MS must be positive');
}
const SPARTAN_PENDING_USER_CREATE_LIMIT = MAX_SESSIONS + 1;
const spartanPendingUserCreates = new Map();
let spartanPendingUserCreateCount = 0;
let spartanBrowserGeneration = 0;

const spartanBrowserLifecycle = createSpartanBrowserLifecycleCoordinator({ maxQueued: MAX_SESSIONS, backgroundAgingMs: 1000 });
const spartanGuardedBrowsers = new WeakSet();
const spartanGuardedContexts = new WeakSet();
const spartanGuardedPages = new WeakSet();
const spartanClosePromises = new WeakMap();
const SPARTAN_PAGE_LIFECYCLE_METHODS = Object.freeze(['close', 'goBack', 'goForward', 'goto', 'reload']);

function spartanTrackLifecyclePromise(promise) {
  return spartanBrowserLifecycle.hasLease() ? spartanBrowserLifecycle.track(promise) : promise;
}
function spartanGuardLifecycleMethod(target, methodName, { prefix, priority = 'foreground', transform = value => value } = {}) {
  const original = target?.[methodName];
  if (typeof original !== 'function') return;
  target[methodName] = function spartanGuardedLifecycleMethod(...args) {
    if (methodName === 'close') {
      const existing = spartanClosePromises.get(target);
      if (existing) return existing;
    }
    const invoke = () => Promise.resolve().then(() => original.apply(this, args)).then(transform);
    const kind = prefix + '_' + String(methodName).replace(/[A-Z]/g, value => '_' + value.toLowerCase());
    const running = spartanBrowserLifecycle.hasLease()
      ? spartanBrowserLifecycle.track(invoke())
      : spartanBrowserLifecycle.run(kind, invoke, {
          priority,
          waitTimeoutMs: priority === 'background' ? null : Math.max(1, HANDLER_TIMEOUT_MS - 1000),
          deadlineAt: priority === 'background' ? null : Date.now() + HANDLER_TIMEOUT_MS,
        });
    if (methodName === 'close') {
      spartanClosePromises.set(target, running);
      running.finally(() => {
        if (spartanClosePromises.get(target) === running) spartanClosePromises.delete(target);
      }).catch(() => {});
    }
    return running;
  };
}
function spartanGuardPage(page) {
  if (!page || spartanGuardedPages.has(page)) return page;
  spartanGuardedPages.add(page);
  for (const methodName of SPARTAN_PAGE_LIFECYCLE_METHODS) {
    spartanGuardLifecycleMethod(page, methodName, { prefix: 'page', priority: methodName === 'close' ? 'background' : 'foreground' });
  }
  return page;
}
function spartanGuardContext(context) {
  if (!context || spartanGuardedContexts.has(context)) return context;
  spartanGuardedContexts.add(context);
  spartanGuardLifecycleMethod(context, 'newPage', { prefix: 'context', transform: spartanGuardPage });
  spartanGuardLifecycleMethod(context, 'addCookies', { prefix: 'context' });
  spartanGuardLifecycleMethod(context, 'close', { prefix: 'context', priority: 'background' });
  try { for (const page of context.pages?.() || []) spartanGuardPage(page); } catch {}
  return context;
}
function spartanGuardBrowser(candidate) {
  if (!candidate || spartanGuardedBrowsers.has(candidate)) return candidate;
  spartanGuardedBrowsers.add(candidate);
  spartanGuardLifecycleMethod(candidate, 'newContext', { prefix: 'browser', transform: spartanGuardContext });
  spartanGuardLifecycleMethod(candidate, 'close', { prefix: 'browser', priority: 'background' });
  return candidate;
}
let spartanLaunchAttemptSequence = 0;
let spartanCurrentLaunchAttempt = null;
function spartanAssertLaunchAttempt(attempt, label) {
  if (!attempt?.active || spartanCurrentLaunchAttempt !== attempt || Date.now() >= attempt.deadlineAt) {
    if (attempt) attempt.active = false;
    throw new Error('Browser launch expired during ' + label);
  }
}
async function spartanLaunchStep(start, attempt, label) {
  spartanAssertLaunchAttempt(attempt, label);
  const underlying = Promise.resolve().then(start);
  attempt.pending.add(underlying);
  underlying.finally(() => attempt.pending.delete(underlying)).catch(() => {});
  const result = await withTimeout(underlying, Math.max(1, attempt.deadlineAt - Date.now()), label);
  spartanAssertLaunchAttempt(attempt, label);
  return result;
}
async function spartanLaunchResource(start, attempt, label, dispose) {
  spartanAssertLaunchAttempt(attempt, label);
  const underlying = Promise.resolve().then(start);
  attempt.pending.add(underlying);
  underlying.finally(() => attempt.pending.delete(underlying)).catch(() => {});
  underlying.then(resource => {
    if (attempt.active && spartanCurrentLaunchAttempt === attempt && Date.now() < attempt.deadlineAt) return;
    const cleanup = Promise.resolve().then(() => dispose(resource));
    attempt.cleanups.add(cleanup);
    cleanup.catch(error => attempt.cleanupErrors.push(error));
    cleanup.finally(() => attempt.cleanups.delete(cleanup)).catch(() => {});
  }).catch(() => {});
  const result = await withTimeout(underlying, Math.max(1, attempt.deadlineAt - Date.now()), label);
  spartanAssertLaunchAttempt(attempt, label);
  return result;
}
async function spartanDrainLaunchAttempt(attempt) {
  while (attempt.pending.size || attempt.cleanups.size) {
    await Promise.allSettled([...attempt.pending, ...attempt.cleanups]);
  }
  if (attempt.cleanupErrors.length) {
    throw new AggregateError(attempt.cleanupErrors, 'browser launch resource cleanup failed');
  }
}
function spartanInvalidateCurrentLaunch() {
  if (spartanCurrentLaunchAttempt) spartanCurrentLaunchAttempt.active = false;
}
function spartanCaptureLaunchIdentity(attempt, localVirtualDisplay, display, before) {
  spartanAssertLaunchAttempt(attempt, 'process identity capture');
  const beforeIds = new Set(before.map(proc => proc.pid + ':' + proc.startTime));
  const created = snapshotOwnedBrowserProcesses(process.pid)
    .filter(proc => !beforeIds.has(proc.pid + ':' + proc.startTime));
  const browsers = created.filter(proc => /camoufox-bin/.test(proc.cmdline) && proc.ppid === process.pid);
  const xvfbPid = Number(localVirtualDisplay?.proc?.pid);
  const xvfb = created.find(proc => proc.pid === xvfbPid && proc.cmdline.startsWith('/usr/bin/Xvfb' + String.fromCharCode(0)));
  if (browsers.length !== 1 || !xvfb || !/^:\\d+$/.test(String(display))) {
    throw new Error('ambiguous or missing accepted browser/display identity');
  }
  return {
    launchId: attempt.id,
    generation: spartanBrowserGeneration + 1,
    serverPid: process.pid,
    browserPid: browsers[0].pid,
    browserStartTime: browsers[0].startTime,
    xvfbPid: xvfb.pid,
    xvfbStartTime: xvfb.startTime,
    display: String(display),
  };
}
async function spartanWaitForDefaultAddonPage(page, attempt) {
    await spartanLaunchStep(() => page.goto('https://example.com/', { waitUntil: 'domcontentloaded', timeout: Math.max(1, attempt.deadlineAt - Date.now()) }), attempt, 'addon neutral navigation');
    const trackerUrls = [
      'https://www.google-analytics.com/__spartan_refresh_probe__',
      'https://www.googletagmanager.com/__spartan_refresh_probe__',
      'https://www.facebook.com/tr/',
    ];
    const failures = new Map();
    page.on('requestfailed', request => {
      if (trackerUrls.includes(request.url())) failures.set(request.url(), request.failure()?.errorText || '');
    });
    let ready = false;
    while (Date.now() < attempt.deadlineAt) {
      failures.clear();
      const attribute = 'data-spartan-addon-ready-' + crypto.randomUUID();
      const result = await spartanLaunchStep(() => page.evaluate(async ({ urls, attribute }) => {
        const source = '(async () => { const values = await Promise.all(' + JSON.stringify(urls)
          + '.map(async url => { try { await fetch(url, { mode: "no-cors", cache: "no-store", credentials: "omit" }); return true; } catch { return false; } }));'
          + 'document.documentElement.setAttribute(' + JSON.stringify(attribute) + ', JSON.stringify(values)); })()';
        const script = document.createElement('script');
        script.textContent = source;
        document.documentElement.appendChild(script);
        try {
          for (let attempt = 0; attempt < 120; attempt++) {
            const value = document.documentElement.getAttribute(attribute);
            if (value) return JSON.parse(value);
            await new Promise(resolve => setTimeout(resolve, 100));
          }
          throw new Error('page-world addon readiness probe timed out');
        } finally {
          script.remove();
          document.documentElement.removeAttribute(attribute);
        }
      }, { urls: trackerUrls, attribute }), attempt, 'default addon readiness');
      ready = Array.isArray(result) && result.length === trackerUrls.length && result.every(value => value === false)
        && trackerUrls.every(url => failures.get(url) === 'NS_ERROR_ABORT');
      if (ready) break;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    if (!ready) throw new Error('default addon did not block controlled page-world requests before launch deadline');
}
async function spartanWaitForDefaultAddon(candidate, attempt) {
  const context = await spartanLaunchResource(() => candidate.newContext(), attempt, 'addon context creation', value => value.close());
  let primaryError = null;
  try {
    const page = await spartanLaunchResource(() => context.newPage(), attempt, 'addon page creation', value => value.close());
    await spartanWaitForDefaultAddonPage(page, attempt);
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    try { await withTimeout(context.close(), 10000, 'addon context cleanup'); }
    catch (cleanupError) {
      attempt.cleanupErrors.push(cleanupError);
      if (!primaryError) throw cleanupError;
    }
  }
}
function spartanPublishBrowser(candidate) {
  browser = spartanGuardBrowser(candidate);
  spartanBrowserGeneration++;
  return browser;
}
function spartanPendingUserCreateSnapshot(now = Date.now()) {
  let oldestAgeMs = 0;
  let deadlineExceeded = false;
  for (const pending of spartanPendingUserCreates.values()) {
    for (const token of pending) {
      oldestAgeMs = Math.max(oldestAgeMs, Math.max(0, now - token.startedAt));
      if (now >= token.deadlineAt) deadlineExceeded = true;
    }
  }
  return { count: spartanPendingUserCreateCount, oldestAgeMs, deadlineExceeded };
}
function spartanHasPendingUserCreate(userId) {
  const pending = spartanPendingUserCreates.get(normalizeUserId(userId));
  return pending instanceof Set && pending.size > 0;
}
function spartanBindPendingCreateSession(userId, fence, session) {
  const key = normalizeUserId(userId);
  fence.userKey = key;
  fence.session = session;
  if (!session || session._closing || sessions.get(key) !== session) {
    fence.active = false;
    throw spartanBrowserLifecycleError('session_invalidated_during_create', 'session was invalidated during tab creation');
  }
  return session;
}
async function spartanWithPendingUserCreate(userId, fence, operation, deadlineAt) {
  if (spartanPendingUserCreateCount >= SPARTAN_PENDING_USER_CREATE_LIMIT) {
    throw spartanBrowserLifecycleError('browser_lifecycle_queue_full', 'Camofox queued create registry is full');
  }
  const key = normalizeUserId(userId);
  let pending = spartanPendingUserCreates.get(key);
  if (!pending) {
    pending = new Set();
    spartanPendingUserCreates.set(key, pending);
  }
  const token = { fence, startedAt: Date.now(), deadlineAt };
  pending.add(token);
  spartanPendingUserCreateCount++;
  try {
    return await operation();
  } finally {
    if (pending.delete(token)) spartanPendingUserCreateCount--;
    if (pending.size === 0 && spartanPendingUserCreates.get(key) === pending) spartanPendingUserCreates.delete(key);
  }
}
const spartanNavigationReadiness = createSpartanNavigationReadiness({
  getGeneration: () => spartanBrowserGeneration,
  markDegraded: code => spartanBrowserLifecycle.markDegraded(code),
});
function spartanSettleNavigationFailure(ticket, error) {
  ticket?.failure({ qualifying: isTimeoutError(error) });
}`;
  modernReplaceOnce(modernRuntimeAnchor, modernRuntime, 'runtime insertion');

  const timeoutRangeStart = source.indexOf('function withTimeout(promise, ms, label) {');
  const timeoutRangeEnd = source.indexOf('\n}\n\nfunction requestTimeoutMs(', timeoutRangeStart);
  if (timeoutRangeStart === -1 || timeoutRangeEnd === -1) throw new Error('expected Camofox 1.15 timeout function');
  const retainedTimeout = `async function withTimeout(promise, ms, label) {
  let timer;
  try {
    return await Promise.race([
      spartanTrackLifecyclePromise(promise),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(\`\${label} timed out after \${ms}ms\`)), ms);
        timer.unref?.();
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}`;
  source = source.slice(0, timeoutRangeStart) + retainedTimeout + source.slice(timeoutRangeEnd + 2);

  const userLimitStart = source.indexOf('async function withUserLimit(userId, operation) {');
  const userLimitEnd = source.indexOf('\n}\n\nasync function safePageClose(', userLimitStart);
  if (userLimitStart === -1 || userLimitEnd === -1) throw new Error('expected Camofox 1.15 per-user concurrency function');
  const upstreamUserLimit = source.slice(userLimitStart, userLimitEnd + 2);
  if (!upstreamUserLimit.includes('state.queue.push(() => { clearTimeout(timer); resolve(); });')) {
    throw new Error('unsupported Camofox 1.15 per-user concurrency function');
  }
  const retainedUserLimit = `async function withUserLimit(userId, operation) {
  const key = normalizeUserId(userId);
  let state = userConcurrency.get(key);
  if (!state) {
    state = { active: 0, queue: [] };
    userConcurrency.set(key, state);
  }
  if (state.active < MAX_CONCURRENT_PER_USER) {
    state.active++;
  } else {
    if (state.queue.length >= MAX_SESSIONS) {
      const error = new Error('User concurrency limit reached, try again');
      error.statusCode = 429;
      throw error;
    }
    await new Promise((resolve, reject) => {
      const waiter = {
        queued: true,
        timer: null,
        activate() {
          if (!waiter.queued) return;
          waiter.queued = false;
          clearTimeout(waiter.timer);
          state.active++;
          resolve();
        },
      };
      waiter.timer = setTimeout(() => {
        if (!waiter.queued) return;
        waiter.queued = false;
        const index = state.queue.indexOf(waiter);
        if (index >= 0) state.queue.splice(index, 1);
        if (state.active === 0 && state.queue.length === 0) {
          if (userConcurrency.get(key) === state) userConcurrency.delete(key);
        }
        const error = new Error('User concurrency limit reached, try again');
        error.statusCode = 429;
        reject(error);
      }, 30000);
      waiter.timer.unref?.();
      state.queue.push(waiter);
    });
  }
  healthState.activeOps++;
  try {
    const result = await operation();
    healthState.lastSuccessfulNav = Date.now();
    return result;
  } finally {
    healthState.activeOps--;
    state.active--;
    const next = state.queue.shift();
    if (next) next.activate();
    if (state.active === 0 && state.queue.length === 0) {
      if (userConcurrency.get(key) === state) userConcurrency.delete(key);
    }
  }
}`;
  source = source.slice(0, userLimitStart) + retainedUserLimit + source.slice(userLimitEnd + 2);

  modernReplaceOnce('async function buildLaunchOptionsWithGeoipFallback(baseOptions, attemptMeta) {', 'async function buildLaunchOptionsWithGeoipFallback(baseOptions, attemptMeta, spartanLaunchAttempt) {', 'launch options deadline argument');
  modernReplaceOnce("    return await withTimeout(launchOptions(baseOptions), GEOIP_SETUP_TIMEOUT_MS, 'GeoIP setup');", "    return await spartanLaunchStep(() => launchOptions(baseOptions), spartanLaunchAttempt, 'GeoIP setup');", 'launch options primary deadline');
  modernReplaceOnce('    return await launchOptions({ ...baseOptions, geoip: false });', "    return await spartanLaunchStep(() => launchOptions({ ...baseOptions, geoip: false }), spartanLaunchAttempt, 'GeoIP fallback setup');", 'launch options fallback deadline');
  modernReplaceOnce('async function probeGoogleSearch(candidateBrowser) {', 'async function probeGoogleSearch(candidateBrowser, spartanLaunchAttempt) {', 'proxy probe deadline argument');
  modernReplaceOnce('    context = await candidateBrowser.newContext({', '    context = await spartanLaunchResource(() => candidateBrowser.newContext({', 'proxy probe context deadline');
  modernReplaceOnce("      permissions: ['geolocation'],\n    });", "      permissions: ['geolocation'],\n    }), spartanLaunchAttempt, 'proxy probe context creation', value => value.close());", 'proxy probe context deadline close');
  modernReplaceOnce('    const page = await context.newPage();', "    const page = await spartanLaunchResource(() => context.newPage(), spartanLaunchAttempt, 'proxy probe page creation', value => value.close());", 'proxy probe page deadline');
  modernReplaceOnce("    await page.goto('https://www.google.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });", "    await spartanLaunchStep(() => page.goto('https://www.google.com/', { waitUntil: 'domcontentloaded', timeout: Math.max(1, spartanLaunchAttempt.deadlineAt - Date.now()) }), spartanLaunchAttempt, 'proxy probe landing');", 'proxy probe landing deadline');
  modernReplaceOnce("    await page.waitForTimeout(1200);", "    await spartanLaunchStep(() => page.waitForTimeout(1200), spartanLaunchAttempt, 'proxy probe landing settle');", 'proxy probe landing settle deadline');
  modernReplaceOnce("    await page.goto('https://www.google.com/search?q=weather%20today', { waitUntil: 'domcontentloaded', timeout: 30000 });", "    await spartanLaunchStep(() => page.goto('https://www.google.com/search?q=weather%20today', { waitUntil: 'domcontentloaded', timeout: Math.max(1, spartanLaunchAttempt.deadlineAt - Date.now()) }), spartanLaunchAttempt, 'proxy probe search');", 'proxy probe search deadline');
  modernReplaceOnce("    await page.waitForTimeout(4000);", "    await spartanLaunchStep(() => page.waitForTimeout(4000), spartanLaunchAttempt, 'proxy probe search settle');", 'proxy probe search settle deadline');
  modernReplaceOnce('    await context?.close().catch(() => {});', "    await withTimeout(context?.close(), 10000, 'proxy probe context cleanup');", 'proxy probe cleanup bound');
  modernReplaceOnce('async function launchBrowserInstance() {', 'async function launchBrowserInstance(spartanLaunchAttempt) {', 'browser launch attempt argument');
  modernReplaceOnce('    let localVirtualDisplay = null;', `    const spartanOwnedBefore = snapshotOwnedBrowserProcesses(process.pid);
    let localVirtualDisplay = null;`, 'browser launch process snapshot');
  modernReplaceOnce('        vdDisplay = await localVirtualDisplay.get();', "        vdDisplay = await spartanLaunchResource(() => localVirtualDisplay.get(), spartanLaunchAttempt, 'virtual display creation', () => localVirtualDisplay.kill());", 'virtual display deadline');
  modernReplaceOnce('      });\n      options.proxy = normalizePlaywrightProxy(options.proxy);', '      }, spartanLaunchAttempt);\n      options.proxy = normalizePlaywrightProxy(options.proxy);', 'launch options deadline call');
  modernReplaceOnce("      await pluginEvents.emitAsync('browser:launching', { options });", "      await spartanLaunchStep(() => pluginEvents.emitAsync('browser:launching', { options }), spartanLaunchAttempt, 'browser launching event');", 'launching event deadline');
  modernReplaceOnce('    let candidateBrowser = null;\n    try {', `    let candidateBrowser = null;
    try {`, 'browser launch addon identity');
  modernReplaceOnce('      candidateBrowser = await firefox.launch(options);', `      options.timeout = Math.max(1, spartanLaunchAttempt.deadlineAt - Date.now());
      candidateBrowser = await spartanLaunchResource(() => firefox.launch(options), spartanLaunchAttempt, 'browser process launch', value => value.close());
      if (!CONFIG.disableDefaultAddons) await spartanWaitForDefaultAddon(candidateBrowser, spartanLaunchAttempt);`, 'browser addon readiness gate');
  modernReplaceOnce('        const probe = await probeGoogleSearch(candidateBrowser);', '        const probe = await probeGoogleSearch(candidateBrowser, spartanLaunchAttempt);', 'proxy probe deadline call');
  modernReplaceOnce(`      virtualDisplay = localVirtualDisplay;
      browserLaunchProxy = launchProxy;
      _lastBrowserPid = candidateBrowser.process?.()?.pid ?? null;
      browser = candidateBrowser; // publish AFTER PID is captured`, `      const spartanAcceptedIdentity = spartanCaptureLaunchIdentity(spartanLaunchAttempt, localVirtualDisplay, vdDisplay, spartanOwnedBefore);
      await spartanLaunchStep(() => pluginEvents.emitAsync('browser:accepted', spartanAcceptedIdentity), spartanLaunchAttempt, 'browser accepted event');
      spartanAssertLaunchAttempt(spartanLaunchAttempt, 'browser publication');
      virtualDisplay = localVirtualDisplay;
      browserLaunchProxy = launchProxy;
      _lastBrowserPid = spartanAcceptedIdentity.browserPid;
      spartanPublishBrowser(candidateBrowser); // publish AFTER PID is captured`, 'identity-bound browser publication');
  modernReplaceOnce('async function _closeBrowserFullyImpl(reason) {\n  const b = browser;', `async function _closeBrowserFullyImpl(reason) {
  spartanInvalidateCurrentLaunch();
  await pluginEvents.emitAsync('browser:closing', { reason });
  const b = browser;`, 'VNC close barrier');
  modernReplaceOnce('      await candidateBrowser?.close().catch(() => {});', `      let spartanBarrierError = null;
      try { await withTimeout(pluginEvents.emitAsync('browser:closing', { reason: 'launch_failed', launchId: spartanLaunchAttempt.id }), 5000, 'VNC close barrier'); }
      catch (cleanupError) { spartanBarrierError = cleanupError; }
      let spartanCandidateCleanupError = null;
      try { if (candidateBrowser) await withTimeout(candidateBrowser.close(), 10000, 'failed candidate cleanup'); }
      catch (cleanupError) { spartanCandidateCleanupError = cleanupError; }
      if (spartanBarrierError || spartanCandidateCleanupError) {
        const cleanupErrors = [spartanBarrierError, spartanCandidateCleanupError].filter(Boolean);
        spartanLaunchAttempt.cleanupErrors.push(...cleanupErrors);
        spartanBrowserLifecycle.markDegraded('browser_launch_cleanup_failed');
        throw new AggregateError([err, ...cleanupErrors], 'launch failed and teardown was not proven');
      }`, 'failed launch VNC cleanup');
  modernReplaceOnce(
    'async function ensureBrowser() {\n  clearBrowserIdleTimer();',
    `async function ensureBrowser() {
  if (!spartanBrowserLifecycle.hasLease()) {
    return await spartanBrowserLifecycle.run('browser_ensure', () => ensureBrowser(), {
      priority: 'foreground', waitTimeoutMs: Math.max(1, HANDLER_TIMEOUT_MS - 1000), deadlineAt: Date.now() + HANDLER_TIMEOUT_MS,
    });
  }
  clearBrowserIdleTimer();`,
    'ensure-browser guard',
  );
  modernReplaceOnce('  if (browser) return browser;\n  if (browserLaunchPromise) return browserLaunchPromise;', '  if (browser) return spartanGuardBrowser(browser);\n  if (browserLaunchPromise) return browserLaunchPromise.then(spartanGuardBrowser);', 'ensure-browser returns');
  modernReplaceOnce(`  browserLaunchPromise = Promise.race([
    launchBrowserInstance(),
    new Promise((_, reject) => setTimeout(() => reject(new Error(\`Browser launch timeout (\${Math.round(launchTimeoutMs / 1000)}s)\`)), launchTimeoutMs)),
  ]).finally(() => { browserLaunchPromise = null; });`, `  await withTimeout(pluginEvents.emitAsync('browser:closing', { reason: 'before_launch' }), 5000, 'VNC pre-launch barrier');
  const spartanLaunchAttempt = { id: ++spartanLaunchAttemptSequence, deadlineAt: Date.now() + launchTimeoutMs, active: true, pending: new Set(), cleanups: new Set(), cleanupErrors: [] };
  spartanCurrentLaunchAttempt = spartanLaunchAttempt;
  const spartanUnderlyingLaunch = spartanTrackLifecyclePromise((async () => {
    try { return await launchBrowserInstance(spartanLaunchAttempt); }
    finally {
      spartanLaunchAttempt.active = false;
      try { await spartanDrainLaunchAttempt(spartanLaunchAttempt); }
      catch (cleanupError) {
        spartanBrowserLifecycle.markDegraded('browser_launch_cleanup_failed');
        throw cleanupError;
      }
    }
  })());
  let spartanLaunchTimer;
  const spartanPublishedLaunchPromise = Promise.race([
    spartanUnderlyingLaunch,
    new Promise((_, reject) => {
      spartanLaunchTimer = setTimeout(() => {
        spartanLaunchAttempt.active = false;
        reject(new Error(\`Browser launch timeout (\${Math.round(launchTimeoutMs / 1000)}s)\`));
      }, launchTimeoutMs);
      spartanLaunchTimer.unref?.();
    }),
  ]).finally(() => { clearTimeout(spartanLaunchTimer); });
  browserLaunchPromise = spartanPublishedLaunchPromise;
  spartanUnderlyingLaunch.finally(() => {
    spartanLaunchAttempt.active = false;
    if (spartanCurrentLaunchAttempt === spartanLaunchAttempt) spartanCurrentLaunchAttempt = null;
    if (browserLaunchPromise === spartanPublishedLaunchPromise) browserLaunchPromise = null;
  }).catch(() => {});`, 'bounded tracked browser launch');
  modernReplaceOnce('  return browserLaunchPromise;\n}\n\n// Helper to normalize userId', '  return browserLaunchPromise.then(spartanGuardBrowser);\n}\n\n// Helper to normalize userId', 'guarded browser return');
  modernReplaceOnce(
    'async function closeBrowserFully(reason) {\n  if (_browserClosePromise)',
    `async function closeBrowserFully(reason) {
  spartanInvalidateCurrentLaunch();
  if (!spartanBrowserLifecycle.hasLease()) {
    return await spartanBrowserLifecycle.run('browser_close', () => closeBrowserFully(reason), { priority: 'background', waitTimeoutMs: null });
  }
  if (_browserClosePromise)`,
    'close-browser guard',
  );
  modernReplaceOnce(
    'async function closeAllSessions(reason, { clearDownloads = true, clearLocks = true } = {}) {\n  const openSessions',
    `async function closeAllSessions(reason, { clearDownloads = true, clearLocks = true } = {}) {
  spartanInvalidateCurrentLaunch();
  if (!spartanBrowserLifecycle.hasLease()) {
    return await spartanBrowserLifecycle.run('sessions_close_all', () => closeAllSessions(reason, { clearDownloads, clearLocks }), { priority: 'background', waitTimeoutMs: null });
  }
  await withTimeout(pluginEvents.emitAsync('browser:closing', { reason }), 5000, 'VNC close barrier');
  const openSessions`,
    'close-all guard',
  );
  modernReplaceOnce(
    `async function closeSession(userId, session, {
  reason = 'session_closed',
  clearDownloads = true,
  clearLocks = true,
} = {}) {
  if (!session) return;`,
    `async function closeSession(userId, session, {
  reason = 'session_closed',
  clearDownloads = true,
  clearLocks = true,
} = {}) {
  if (!session) return;
  if (reason === 'api_delete_session' && spartanHasPendingUserCreate(userId)) {
    throw spartanBrowserLifecycleError('session_create_pending', 'session has pending tab creation');
  }
  if (!spartanBrowserLifecycle.hasLease()) {
    if (session._spartanClosePromise) return await session._spartanClosePromise;
    session._closing = true;
    const closing = spartanBrowserLifecycle.run('session_close', () => closeSession(userId, session, { reason, clearDownloads, clearLocks }), { priority: 'background', waitTimeoutMs: null });
    session._spartanClosePromise = closing;
    return await closing;
  }`,
    'close-session guard',
  );
  modernReplaceOnce('  await session.context.close().catch(() => {});', '  await session.context.close();', 'session context close propagation');
  const legacyOpenStart = source.indexOf("app.post('/tabs/open', async (req, res) => {");
  if (legacyOpenStart === -1) throw new Error('expected Camofox 1.15 legacy tabs-open route');
  const legacyOpenEnd = spartanFindCallExpressionEnd(source, legacyOpenStart);
  if (legacyOpenEnd === -1) throw new Error('expected Camofox 1.15 legacy tabs-open route end');
  const disabledLegacyOpen = `app.post('/tabs/open', (_req, res) => {
  return res.status(410).json({
    error: 'Deprecated endpoint disabled; use POST /tabs',
    code: 'legacy_tab_open_disabled',
  });
});`;
  source = source.slice(0, legacyOpenStart) + disabledLegacyOpen + source.slice(legacyOpenEnd);

  modernReplaceOnce(
    'if (closeEmptySessions && session.tabGroups.size === 0 && !hasActivePageLeases(session)) {',
    `// Spartan queued-create fence: pressure cleanup.
      if (closeEmptySessions && session.tabGroups.size === 0 && !hasActivePageLeases(session) && !spartanHasPendingUserCreate(userId)) {`,
    'pressure-cleanup pending-create guard',
  );
  modernReplaceOnce(
    'if (session.tabGroups.size === 0 && !hasActivePageLeases(session)) {',
    `// Spartan queued-create fence: tab reaper.
    if (session.tabGroups.size === 0 && !hasActivePageLeases(session) && !spartanHasPendingUserCreate(userId)) {`,
    'tab-reaper pending-create guard',
  );

  modernReplaceOnce("  sessions.delete(key);\n  await pluginEvents.emitAsync('session:destroyed'", "  if (sessions.get(key) === session) sessions.delete(key);\n  session._spartanCloseComplete = true;\n  await pluginEvents.emitAsync('session:destroyed'", 'session compare-and-delete');
  modernReplaceOnce(
    'async function getSession(userId, { trace = false } = {}) {\n  const key = normalizeUserId(userId);',
    `async function getSession(userId, { trace = false } = {}) {
  if (!spartanBrowserLifecycle.hasLease()) {
    return await spartanBrowserLifecycle.run('session_get', () => getSession(userId, { trace }), {
      priority: 'foreground', waitTimeoutMs: Math.max(1, HANDLER_TIMEOUT_MS - 1000), deadlineAt: Date.now() + HANDLER_TIMEOUT_MS,
    });
  }
  const key = normalizeUserId(userId);`,
    'get-session guard',
  );
  modernReplaceOnce(
    'async function restartBrowser(reason) {\n  if (healthState.isRecovering) return;',
    `async function restartBrowser(reason) {
  if (!spartanBrowserLifecycle.isRecoveryLease()) {
    if (spartanBrowserLifecycle.hasLease()) {
      spartanBrowserLifecycle.markDegraded('browser_recovery_reentrant');
      throw spartanBrowserLifecycleError('browser_recovery_reentrant', 'browser recovery requested by an active lifecycle operation');
    }
    return await spartanBrowserLifecycle.recover(() => restartBrowser(reason), { drainTimeoutMs: SPARTAN_RECOVERY_DRAIN_TIMEOUT_MS });
  }
  if (healthState.isRecovering) return;`,
    'restart guard',
  );

  const popupStart = source.indexOf('function attachPopupHandler(page, userId, sessionKey) {');
  const popupEnd = source.indexOf('\n}\n\nfunction pressureHash(', popupStart);
  if (popupStart === -1 || popupEnd === -1) throw new Error('expected Camofox 1.15 popup handler');
  let modernPopup = source.slice(popupStart, popupEnd + 2);
  const popupOpening = `function attachPopupHandler(page, userId, sessionKey) {
  page.on('popup', (popupPage) => {
    const key = normalizeUserId(userId);
    const currentSession = sessions.get(key);
    if (!currentSession || currentSession._closing) return;`;
  const popupFence = `function attachPopupHandler(page, userId, sessionKey) {
  const key = normalizeUserId(userId);
  const sourceSession = sessions.get(key);
  page = spartanGuardPage(page);
  page.on('popup', (popupPage) => {
    const guardedPopupPage = spartanGuardPage(popupPage);
    const currentSession = sessions.get(key);
    if (!sourceSession || currentSession !== sourceSession || currentSession._closing) {
      safePageClose(guardedPopupPage).catch(() => {});
      return;
    }`;
  if ((modernPopup.split(popupOpening).length - 1) !== 1) throw new Error('expected Camofox 1.15 popup opening');
  modernPopup = modernPopup.replace(popupOpening, popupFence)
    .replace('createTabState(popupPage)', 'createTabState(guardedPopupPage)')
    .replaceAll('safePageUrl(popupPage)', 'safePageUrl(guardedPopupPage)')
    .replace('page: popupPage', 'page: guardedPopupPage')
    .replace('attachPopupHandler(popupPage, userId, sessionKey)', 'attachPopupHandler(guardedPopupPage, userId, sessionKey)');
  if (!modernPopup.includes('currentSession !== sourceSession')) throw new Error('popup generation fence failed');
  source = source.slice(0, popupStart) + modernPopup + source.slice(popupEnd + 2);

  const createStart = source.indexOf("app.post('/tabs', async (req, res) => {");
  const createEnd = source.indexOf("app.post('/tabs/:tabId/navigate', async (req, res) => {", createStart);
  if (createStart === -1 || createEnd === -1) throw new Error('expected Camofox 1.15 create route');
  let modernCreate = source.slice(createStart, createEnd);
  function replaceCreate(marker, replacement, label) {
    const count = modernCreate.split(marker).length - 1;
    if (count !== 1) throw new Error(`expected one Camofox 1.15 create ${label}, found ${count}`);
    modernCreate = modernCreate.replace(marker, replacement);
  }
  replaceCreate("app.post('/tabs', async (req, res) => {\n  try {", `app.post('/tabs', async (req, res) => {
  const spartanCreateFence = { active: true, stage: 'validate', startedAt: Date.now(), session: null, page: null, group: null, tabId: null, userKey: null };
  let spartanCreateNavigation = null;
  try {`, 'fence start');
  replaceCreate('    const result = await withTimeout((async () => {', `    const spartanCreateTimeoutMs = requestTimeoutMs();
    const spartanCreateDeadlineAt = Date.now() + spartanCreateTimeoutMs;
    const result = await withTimeout(spartanWithPendingUserCreate(userId, spartanCreateFence, () => spartanBrowserLifecycle.run('tab_create', async () => {`, 'admission start');
  replaceCreate('      let session = await getSession(userId, { trace: !!trace });', `      spartanCreateFence.stage = 'get_session';
      let session = await getSession(userId, { trace: !!trace });
      spartanBindPendingCreateSession(userId, spartanCreateFence, session);`, 'session bind');
  replaceCreate('      const createdPage = await createPageWithRecoveryForUser(userId, session, { trace: !!trace });', `      spartanCreateFence.stage = 'new_page';
      const spartanCreatedPagePromise = createPageWithRecoveryForUser(userId, session, { trace: !!trace });
      spartanCreatedPagePromise.then(async late => {
        if (!spartanCreateFence.active && late?.page) await closeLeasedPage(late.session, late.page, late.lease).catch(() => {});
      }).catch(() => {});
      const createdPage = await spartanCreatedPagePromise;`, 'leased page');
  replaceCreate('      session = createdPage.session;', `      session = createdPage.session;
      spartanBindPendingCreateSession(userId, spartanCreateFence, session);`, 'recovered session bind');
  replaceCreate('      const page = createdPage.page;', `      const page = createdPage.page;
      spartanCreateFence.page = page;`, 'page bind');
  replaceCreate('      const group = getTabGroup(session, resolvedSessionKey);', `      const group = getTabGroup(session, resolvedSessionKey);
      spartanCreateFence.group = group;`, 'group bind');
  replaceCreate('      const tabId = fly.makeTabId();', `      const tabId = fly.makeTabId();
      spartanCreateFence.tabId = tabId;`, 'tab bind');
  replaceCreate('      if (url) {\n        const urlErr', `      if (url) {
        spartanCreateFence.stage = 'navigate';
        spartanCreateNavigation = spartanNavigationReadiness.begin();
        const urlErr`, 'navigation start');
  replaceCreate('        tabState.visitedUrls.add(url);\n      }', `        tabState.visitedUrls.add(url);
        spartanCreateNavigation?.success();
      }`, 'navigation success');
  replaceCreate("    })(), requestTimeoutMs(), 'tab create');", `    }, {
      priority: 'foreground', waitTimeoutMs: Math.max(1, spartanCreateDeadlineAt - Date.now() - 1000), deadlineAt: spartanCreateDeadlineAt,
    }), spartanCreateDeadlineAt), spartanCreateTimeoutMs, 'tab create');`, 'admission end');
  replaceCreate("  } catch (err) {\n    log('error', 'tab create failed'", `  } catch (err) {
    spartanCreateFence.active = false;
    spartanSettleNavigationFailure(spartanCreateNavigation, err);
    if (spartanCreateFence.group && spartanCreateFence.tabId) spartanCreateFence.group.delete(spartanCreateFence.tabId);
    if (spartanCreateFence.page && isTimeoutError(err)) await safePageClose(spartanCreateFence.page).catch(() => {});
    log('error', 'tab create failed'`, 'failure cleanup');
  source = source.slice(0, createStart) + modernCreate + source.slice(createEnd);

  const navigateStart = source.indexOf("app.post('/tabs/:tabId/navigate', async (req, res) => {");
  const navigateEnd = source.indexOf("\n});", navigateStart);
  if (navigateStart === -1 || navigateEnd === -1) throw new Error('expected Camofox 1.15 navigate route');
  let modernNavigate = source.slice(navigateStart, navigateEnd);
  modernNavigate = modernNavigate.replace('  const tabId = req.params.tabId;\n  \n  try {', '  const tabId = req.params.tabId;\n  const spartanNavigateOutcome = spartanNavigationReadiness.begin();\n  \n  try {');
  modernNavigate = modernNavigate.replace('    res.json(result);\n  } catch (err) {', '    spartanNavigateOutcome.success();\n    res.json(result);\n  } catch (err) {\n    spartanSettleNavigationFailure(spartanNavigateOutcome, err);');
  if (!modernNavigate.includes('spartanNavigateOutcome.success()')) throw new Error('expected Camofox 1.15 navigate outcome anchors');
  source = source.slice(0, navigateStart) + modernNavigate + source.slice(navigateEnd);

  modernReplaceOnce(
    `      // Memory admission control (Fly.io only) — reject new sessions when
      // system memory is critically low. 503 tells Fly Proxy to try another machine.`,
    `      if (process.env.SPARTAN_CAMOFOX_CGROUP_ADMISSION === 'true') {
        const admission = spartanCgroupAdmissionAllowed({ threshold: Number(process.env.SPARTAN_CAMOFOX_MEMORY_SOFT_RATIO || 0.75) });
        if (!admission.allowed) throw Object.assign(new Error('Camofox cgroup memory admission rejected'), { statusCode: 503, code: admission.code || 'memory_pressure' });
      }
      // Memory admission control (Fly.io only) — reject new sessions when
      // system memory is critically low. 503 tells Fly Proxy to try another machine.`,
    'cgroup admission',
  );
  // Upstream now preserves fixed-proxy sessions. Retain our immediate tab
  // retirement: upstream's non-navigation reaper does not cover this branch.
  modernReplaceOnce(
    `    recordNavFailure(userId);
  }
  // Track consecutive timeouts per tab`,
    `    recordNavFailure(userId);
    const timedOutSession = sessions.get(normalizeUserId(userId));
    const timedOutTabId = req.body?.tabId || req.query?.tabId || req.params?.tabId;
    if (timedOutSession && timedOutTabId) destroyTab(timedOutSession, timedOutTabId, 'navigation_timeout', userId);
  }
  // Track consecutive timeouts per tab`,
    'targeted timeout retirement',
  );
  source = source.replaceAll("if (recordNavFailure(userId)) {\n              await recoverUserSession(userId, 'navigate_failure');\n            }", "if (!isTimeoutError(navErr) && recordNavFailure(userId)) {\n              await recoverUserSession(userId, 'navigate_failure');\n            }");
  source = source.replaceAll("if (recordNavFailure(userId)) {\n              await recoverUserSession(userId, 'tab_create_nav_failure');\n            }", "if (!isTimeoutError(navErr) && recordNavFailure(userId)) {\n              await recoverUserSession(userId, 'tab_create_nav_failure');\n            }");

  // Both launch diagnostics and options obey the local Tinyproxy policy.
  if (source.split('geoip: !!launchProxy,').length - 1 !== 2) {
    throw new Error('expected two Camofox proxy geoip anchors');
  }
  source = source.replaceAll('geoip: !!launchProxy,', "geoip: process.env.CAMOFOX_GEOIP !== 'false' && !!launchProxy,");

  // Upstream protects established browsers, but browser is null during launch.
  // The outer launch timeout can also clear browserLaunchPromise before the
  // underlying launch settles. Preserve our no-periodic-scan contract until
  // upstream protects those owned profiles too. Startup/post-close remain native.
  const profileCleanupStart = source.indexOf('  // Periodic cleanup is liveness-aware:');
  const profileCleanupEnd = source.indexOf('  const traceSweep = sweepOldTraces({', profileCleanupStart);
  if (profileCleanupStart === -1 || profileCleanupEnd === -1
      || !source.slice(profileCleanupStart, profileCleanupEnd).includes('cleanupStaleFirefoxProfiles({ protectedPaths: profiles })')) {
    throw new Error('expected upstream periodic profile cleanup boundary');
  }
  source = source.slice(0, profileCleanupStart)
    + '  // Spartan: periodic profile cleanup disabled during browser ownership transitions.\n'
    + source.slice(profileCleanupEnd);

  const healthRouteStart = source.indexOf("app.get('/health',");
  if (healthRouteStart === -1) throw new Error('expected Camofox 1.15 health route');
  const healthRouteEnd = spartanFindCallExpressionEnd(source, healthRouteStart);
  if (healthRouteEnd === -1) throw new Error('expected Camofox 1.15 health route end');
  const healthRoutes = `const spartanDeepHealthSecretPath = process.env.SPARTAN_CAMOFOX_INTERNAL_SECRET_FILE || '/run/spartan-camofox/internal.key';
const SPARTAN_DEEP_PROBE_TIMEOUT_MS = Number(process.env.SPARTAN_CAMOFOX_DEEP_TIMEOUT_MS || 8000);
const SPARTAN_DEEP_CLEANUP_TIMEOUT_MS = Number(process.env.SPARTAN_CAMOFOX_DEEP_CLEANUP_TIMEOUT_MS || 1000);
const SPARTAN_DEEP_BUSY_MAX_AGE_MS = Number(process.env.SPARTAN_CAMOFOX_DEEP_BUSY_MAX_AGE_MS || 45000);
const spartanDeepHealth = createSpartanDeepHealthController({
  getBrowser: ensureBrowser,
  recoverBrowser: () => restartBrowser('spartan_deep_health'),
  getGeneration: () => spartanBrowserGeneration,
  probeTimeoutMs: SPARTAN_DEEP_PROBE_TIMEOUT_MS,
  cleanupTimeoutMs: SPARTAN_DEEP_CLEANUP_TIMEOUT_MS,
  busyMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,
  cacheMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,
  recoveryTimeoutMs: Number(process.env.SPARTAN_CAMOFOX_SERVER_RECOVERY_TIMEOUT_MS || 20000),
  runExclusive: async operation => {
    try {
      // Queue behind the active owner in the reserved health slot; checking
      // ownerKind here would starve probes during sustained browser traffic.
      return await spartanBrowserLifecycle.run('deep_probe', operation, {
        priority: 'health', waitTimeoutMs: Math.max(1, SPARTAN_DEEP_PROBE_TIMEOUT_MS - 1000), deadlineAt: Date.now() + SPARTAN_DEEP_PROBE_TIMEOUT_MS, allowDegraded: true,
      });
    } catch (error) {
      if (['browser_lifecycle_queue_timeout', 'browser_lifecycle_queue_full', 'browser_lifecycle_recovering'].includes(error?.code)) {
        throw spartanBrowserLifecycleError('browser_lifecycle_busy', 'browser lifecycle is busy');
      }
      throw error;
    }
  },
});
function spartanRequireInternalHealth(req, res) {
  if (spartanInternalRequestAuthorized(req, spartanDeepHealthSecretPath)) return true;
  res.status(403).json({ ok: false, code: 'forbidden' });
  return false;
}
function spartanDeepHealthPayload(result) {
  return { ok: Boolean(result?.ok), code: String(result?.code || 'deep_probe_failed'), stage: String(result?.stage || 'lifecycle'), browserGeneration: Number.isSafeInteger(result?.generation) ? result.generation : spartanBrowserGeneration };
}
app.get('/spartan/health/deep', async (req, res) => {
  if (!spartanRequireInternalHealth(req, res)) return;
  const lifecycle = spartanBrowserLifecycle.snapshot();
  const pending = spartanPendingUserCreateSnapshot();
  let result;
  if (lifecycle.degradedCode === 'browser_navigation_degraded') result = { ok: false, code: 'browser_navigation_degraded', stage: 'lifecycle', generation: spartanBrowserGeneration };
  else if (pending.deadlineExceeded || lifecycle.ownerDeadlineExceeded) result = { ok: false, code: 'browser_lifecycle_stuck', stage: 'lifecycle', generation: spartanBrowserGeneration };
  else result = await spartanDeepHealth.probe();
  const payload = spartanDeepHealthPayload(result);
  res.status(payload.ok ? 200 : 503).json(payload);
});
app.post('/spartan/recover', async (req, res) => {
  if (!spartanRequireInternalHealth(req, res)) return;
  const result = await spartanDeepHealth.recover();
  res.status(result.ok ? 200 : 503).json(spartanDeepHealthPayload(result));
});
app.get('/health', (_req, res) => {
  const lifecycle = spartanBrowserLifecycle.snapshot();
  const navigation = spartanNavigationReadiness.snapshot();
  const readiness = spartanDeepHealth.snapshot();
  const ok = readiness.ok && !lifecycle.degradedCode && !navigation.latched;
  res.status(ok ? 200 : 503).json({
    ok, engine: 'camoufox', browserGeneration: spartanBrowserGeneration,
    browserRunning: browser !== null && (browser.isConnected?.() ?? false),
    activeTabs: getTotalTabCount(), activeSessions: sessions.size,
    consecutiveFailures: navigation.consecutiveFailures,
    readiness: { code: readiness.code, stage: readiness.stage || null, ageMs: readiness.ageMs ?? null },
  });
});
app.get('/live', (_req, res) => res.status(200).json({ ok: true, engine: 'camoufox', live: true }));`;
  source = source.slice(0, healthRouteStart) + healthRoutes + source.slice(healthRouteEnd);

  const activeHealthStart = '// Active health probe -- detect hung browser even when isConnected() lies\n';
  const activeHealthEnd = '\n// Crash logging\n';
  const activeHealthIndex = source.indexOf(activeHealthStart);
  if (activeHealthIndex !== -1) {
    const end = source.indexOf(activeHealthEnd, activeHealthIndex);
    if (end === -1) throw new Error('expected Camofox 1.15 active health end');
    source = source.slice(0, activeHealthIndex) + source.slice(end + 1);
  }

  for (const [marker, label] of [
    ['SPARTAN_CAMOFOX_UPSTREAM_LEASES_REVISION = 1', 'upstream lease integration'],
    ['spartanTrackLifecyclePromise(promise)', 'timeout retention'],
    ['if (timer) clearTimeout(timer);', 'timeout timer cleanup'],
    ['if (state.queue.length >= MAX_SESSIONS)', 'bounded per-user waiters'],
    ['currentSession !== sourceSession', 'popup generation fence'],
    ["code: 'legacy_tab_open_disabled'", 'legacy tabs-open disablement'],
    ['await session.context.close();', 'context-close error propagation'],
    ['Spartan queued-create fence: pressure cleanup.', 'pressure cleanup create fence'],
    ['Spartan queued-create fence: tab reaper.', 'tab reaper create fence'],
    ['spartanBindPendingCreateSession(userId, spartanCreateFence, session)', 'create generation bind'],
    ['closeLeasedPage(late.session, late.page, late.lease)', 'late leased-page cleanup'],
    ["app.get('/spartan/health/deep'", 'deep health'],
    ["app.get('/live'", 'liveness'],
    ['browser_navigation_degraded', 'navigation readiness'],
    ['Spartan: periodic profile cleanup disabled during browser ownership transitions.', 'live profile protection'],
    ['if (isNavigationTimeout && proxyPool?.canRotateSessions)', 'upstream timeout proxy guard'],
    ["destroyTab(timedOutSession, timedOutTabId, 'navigation_timeout'", 'targeted timeout retirement'],
  ]) {
    if (!source.includes(marker)) throw new Error(`Camofox 1.15 patch incomplete: ${label}`);
  }
  source = installSingleAttemptClick(source);
  fs.writeFileSync(path, source);
  process.exit(0);
}

throw new Error('unsupported Camofox upstream source; expected the pinned 1.15 recovery and page-lease model');
