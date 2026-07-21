const fs = require('fs');

const path = 'server.js';
let source = fs.readFileSync(path, 'utf8');

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
if (source.includes(safeErrorCodeMarker)) {
  source = source.replace(safeErrorCodeMarker, safeErrorCodeReplacement);
} else if (!source.includes('if (err.code) return err.message;')) {
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
if (source.includes(sendErrorCodeMarker)) {
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

const spartanCreateLimitMarker = 'const userConcurrency = new Map();';
const spartanCreateLimitReplacement = `const userConcurrency = new Map();

const SPARTAN_MAX_CONCURRENT_CREATES_RAW = Number(process.env.SPARTAN_CAMOFOX_MAX_CONCURRENT_CREATES || '1');
const SPARTAN_MAX_CONCURRENT_CREATES = SPARTAN_MAX_CONCURRENT_CREATES_RAW;
if (
  !Number.isInteger(SPARTAN_MAX_CONCURRENT_CREATES)
  || SPARTAN_MAX_CONCURRENT_CREATES < 1
  || SPARTAN_MAX_CONCURRENT_CREATES > MAX_SESSIONS
) throw new Error('SPARTAN_CAMOFOX_MAX_CONCURRENT_CREATES must be an integer between 1 and MAX_SESSIONS');

const spartanCreateWaiters = [];
let spartanActiveCreates = 0;
function spartanCreateQueueError(code, message) {
  const error = new Error(message);
  error.statusCode = 503;
  error.code = code;
  return error;
}
async function spartanWithCreateLimit(operation, queueTimeoutMs = 30000) {
  if (spartanActiveCreates < SPARTAN_MAX_CONCURRENT_CREATES) {
    spartanActiveCreates++;
  } else {
    if (spartanCreateWaiters.length >= MAX_SESSIONS) {
      throw spartanCreateQueueError('tab_create_queue_full', 'Camofox tab create queue is full');
    }
    const timeoutMs = Number(queueTimeoutMs);
    if (!Number.isFinite(timeoutMs) || timeoutMs < 1) {
      throw spartanCreateQueueError('tab_create_queue_timeout', 'Camofox tab create queue deadline expired');
    }
    await new Promise((resolve, reject) => {
      const waiter = {
        activate() {
          clearTimeout(waiter.timer);
          resolve();
        },
        timer: null,
      };
      waiter.timer = setTimeout(() => {
        const index = spartanCreateWaiters.indexOf(waiter);
        if (index < 0) return;
        spartanCreateWaiters.splice(index, 1);
        reject(spartanCreateQueueError('tab_create_queue_timeout', 'Camofox tab create queue deadline expired'));
      }, timeoutMs);
      spartanCreateWaiters.push(waiter);
    });
  }
  try {
    return await operation();
  } finally {
    const next = spartanCreateWaiters.shift();
    if (next) next.activate();
    else spartanActiveCreates--;
  }
}`;
if (!source.includes('async function spartanWithCreateLimit(')) {
  if (!source.includes(spartanCreateLimitMarker)) {
    throw new Error('expected Camofox user-concurrency marker not found');
  }
  source = source.replace(spartanCreateLimitMarker, spartanCreateLimitReplacement);
}

const createTabRouteStart = "app.post('/tabs', async (req, res) => {";
const createTabRouteEnd = "\n});\n\n// Navigate";
const createTabRouteStartIndex = source.indexOf(createTabRouteStart);
if (createTabRouteStartIndex !== -1) {
  const createTabRouteEndIndex = source.indexOf(createTabRouteEnd, createTabRouteStartIndex);
  if (createTabRouteEndIndex === -1) throw new Error('expected Camofox create-tab route end marker not found');
  let createTabRoute = source.slice(createTabRouteStartIndex, createTabRouteEndIndex);
  if (!createTabRoute.includes('const spartanCreateFence =')) {
    const routeOpen = `${createTabRouteStart}\n  try {`;
    const routeOpenReplacement = `${createTabRouteStart}\n  const spartanCreateFence = {\n    active: true,\n    stage: 'validate',\n    startedAt: Date.now(),\n    session: null,\n    page: null,\n    group: null,\n    tabId: null,\n    userKey: null,\n  };\n  try {`;
    const getSessionMarker = '      let session = await getSession(userId, { trace: !!trace });';
    const getSessionReplacement = `      spartanCreateFence.stage = 'get_session';
      spartanCreateFence.userKey = normalizeUserId(userId);
      let session = await getSession(userId, { trace: !!trace });
      spartanCreateFence.session = session;
      if (!(session._spartanPendingCreates instanceof Set)) session._spartanPendingCreates = new Set();
      session._spartanPendingCreates.add(spartanCreateFence);
      if (!spartanCreateFence.active) {
        if (sessions.get(spartanCreateFence.userKey) === session) sessions.delete(spartanCreateFence.userKey);
        session._closing = true;
        closeSession(spartanCreateFence.userKey, session, { reason: 'late_tab_create', clearDownloads: true, clearLocks: true }).catch(() => {});
        throw Object.assign(new Error('tab create completed after deadline'), { statusCode: 503, code: 'tab_create_timeout' });
      }`;
    const groupMarker = '      const group = getTabGroup(session, resolvedSessionKey);';
    const groupReplacement = `${groupMarker}
      spartanCreateFence.group = group;`;
    const newPageMarker = '      const page = await session.context.newPage();';
    const newPageReplacement = `      spartanCreateFence.stage = 'new_page';
      const spartanPagePromise = session.context.newPage();
      spartanPagePromise.then(latePage => {
        if (!spartanCreateFence.active) safePageClose(latePage).catch(() => {});
      }).catch(() => {});
      const page = await spartanPagePromise;
      if (!spartanCreateFence.active) {
        safePageClose(page).catch(() => {});
        throw Object.assign(new Error('tab create completed after deadline'), { statusCode: 503, code: 'tab_create_timeout' });
      }
      spartanCreateFence.page = page;`;
    const tabIdMarker = '      const tabId = fly.makeTabId();';
    const tabIdReplacement = `${tabIdMarker}
      spartanCreateFence.tabId = tabId;`;
    const navigationMarker = `      if (url) {
        const urlErr = validateUrl(url);`;
    const navigationReplacement = `      if (url) {
        spartanCreateFence.stage = 'navigate';
        const urlErr = validateUrl(url);`;
    const createPromiseCloseMarker = `    })(), requestTimeoutMs(), 'tab create');`;
    const createOperationOpenMarker = 'const result = await withTimeout((async () => {';
    const createOperationOpenReplacement = 'const result = await withTimeout(spartanWithCreateLimit(async () => {';
    const createPromiseCloseReplacement = `    }, Math.max(1, requestTimeoutMs() - 1000)).finally(() => {
      const spartanPendingCreates = spartanCreateFence.session?._spartanPendingCreates;
      if (spartanPendingCreates instanceof Set) {
        spartanPendingCreates.delete(spartanCreateFence);
        if (spartanPendingCreates.size === 0) delete spartanCreateFence.session._spartanPendingCreates;
      }
    }), requestTimeoutMs(), 'tab create');`;
    const catchMarker = `  } catch (err) {
    log('error', 'tab create failed', { reqId: req.reqId, error: err.message });`;
    const catchReplacement = `  } catch (err) {
    spartanCreateFence.active = false;
    const spartanCreateTimedOut = err?.code === 'tab_create_timeout'
      || String(err?.message || '').includes('tab create timed out');
    if (spartanCreateTimedOut) {
      err.statusCode = 503;
      err.code = 'tab_create_timeout';
      log('error', 'tab create stage failed', {
        reqId: req.reqId,
        stage: spartanCreateFence.stage,
        durationMs: Date.now() - spartanCreateFence.startedAt,
        code: err.code,
      });
      if (spartanCreateFence.group && spartanCreateFence.tabId) {
        spartanCreateFence.group.delete(spartanCreateFence.tabId);
        refreshActiveTabsGauge();
      }
      if (spartanCreateFence.page) {
        await withTimeout(safePageClose(spartanCreateFence.page), 2000, 'tab create cleanup').catch(() => {
          restartBrowser('tab_create_cleanup_timeout').catch(() => {});
        });
      } else if (spartanCreateFence.session && spartanCreateFence.userKey) {
        const current = sessions.get(spartanCreateFence.userKey);
        if (current === spartanCreateFence.session) sessions.delete(spartanCreateFence.userKey);
        spartanCreateFence.session._closing = true;
        await withTimeout(closeSession(spartanCreateFence.userKey, spartanCreateFence.session, {
          reason: 'tab_create_timeout', clearDownloads: true, clearLocks: true,
        }), 2000, 'tab create session cleanup').catch(() => {
          restartBrowser('tab_create_session_cleanup_timeout').catch(() => {});
        });
      }
    }
    log('error', 'tab create failed', { reqId: req.reqId, error: err.message });`;

    for (const [marker, replacement, label] of [
      [routeOpen, routeOpenReplacement, 'route open'],
      [getSessionMarker, getSessionReplacement, 'getSession'],
      [groupMarker, groupReplacement, 'group'],
      [newPageMarker, newPageReplacement, 'newPage'],
      [tabIdMarker, tabIdReplacement, 'tabId'],
      [navigationMarker, navigationReplacement, 'navigation'],
      [createOperationOpenMarker, createOperationOpenReplacement, 'operation open'],
      [createPromiseCloseMarker, createPromiseCloseReplacement, 'promise close'],
      [catchMarker, catchReplacement, 'catch'],
    ]) {
      if (!createTabRoute.includes(marker)) throw new Error(`expected Camofox create-tab ${label} marker not found`);
      createTabRoute = createTabRoute.replace(marker, replacement);
    }
    source = source.slice(0, createTabRouteStartIndex) + createTabRoute + source.slice(createTabRouteEndIndex);
  }
}

const spartanCloseSessionDeleteMarker = `  await session.context.close().catch(() => {});
  sessions.delete(key);`;
const spartanCloseSessionDeleteReplacement = `  await session.context.close().catch(() => {});
  // A reaped session may already have been replaced for the same user while
  // context.close() was pending. Never delete that replacement.
  if (sessions.get(key) === session) sessions.delete(key);`;
if (source.includes(spartanCloseSessionDeleteMarker)) {
  source = source.replace(
    spartanCloseSessionDeleteMarker,
    spartanCloseSessionDeleteReplacement,
  );
} else if (!source.includes(
  'if (sessions.get(key) === session) sessions.delete(key);',
)) {
  throw new Error('expected Camofox close-session delete marker not found');
}

const spartanEmptyGroupReaperMarker = `      if (group.size === 0) {
        session.tabGroups.delete(listItemId);
      }`;
const spartanEmptyGroupReaperReplacement = `      const spartanGroupCreatePending = session._spartanPendingCreates instanceof Set
        && [...session._spartanPendingCreates].some(pending => pending.group === group);
      if (group.size === 0 && !spartanGroupCreatePending) {
        session.tabGroups.delete(listItemId);
      }`;
if (source.includes(spartanEmptyGroupReaperMarker)) {
  source = source.replace(
    spartanEmptyGroupReaperMarker,
    spartanEmptyGroupReaperReplacement,
  );
} else if (!source.includes('const spartanGroupCreatePending =')) {
  throw new Error('expected Camofox empty-group reaper marker not found');
}

const spartanEmptySessionReaperMarker = `    // Clean up sessions with zero tabs remaining -- free browser context memory
    if (session.tabGroups.size === 0) {`;
const spartanEmptySessionReaperReplacement = `    // A create fence lives until the underlying work settles, not merely until
    // the HTTP timeout. Never tear down a context that is still creating a tab.
    const spartanSessionCreatePending = session._spartanPendingCreates instanceof Set
      && session._spartanPendingCreates.size > 0;
    if (
      session.tabGroups.size === 0
      && !spartanSessionCreatePending
    ) {`;
if (source.includes(spartanEmptySessionReaperMarker)) {
  source = source.replace(
    spartanEmptySessionReaperMarker,
    spartanEmptySessionReaperReplacement,
  );
} else if (!source.includes('const spartanSessionCreatePending =')) {
  throw new Error('expected Camofox empty-session reaper marker not found');
}

const spartanOrphanReaperMarker = `    if (session._closing) continue;
    let contextPages;
    try {
      contextPages = session.context.pages();`;
const spartanOrphanReaperReplacement = `    if (session._closing) continue;
    if (session._spartanPendingCreates instanceof Set && session._spartanPendingCreates.size > 0) continue;
    let contextPages;
    try {
      contextPages = session.context.pages();`;
if (source.includes(spartanOrphanReaperMarker)) {
  source = source.replace(
    spartanOrphanReaperMarker,
    spartanOrphanReaperReplacement,
  );
} else if (!source.includes(spartanOrphanReaperReplacement)) {
  throw new Error('expected Camofox orphan reaper marker not found');
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

const spartanCgroupAdmissionMarker = `      // Memory admission control (Fly.io only) — reject new sessions when
      // system memory is critically low. 503 tells Fly Proxy to try another machine.`;
const spartanCgroupAdmission = `      if (process.env.SPARTAN_CAMOFOX_CGROUP_ADMISSION === 'true') {
        const admission = spartanCgroupAdmissionAllowed({
          threshold: Number(process.env.SPARTAN_CAMOFOX_MEMORY_SOFT_RATIO || 0.90),
        });
        if (!admission.allowed) {
          const err = new Error('Camofox cgroup memory admission rejected');
          err.statusCode = 503;
          err.code = admission.code || 'memory_pressure';
          throw err;
        }
      }
`;
if (!source.includes('spartanCgroupAdmissionAllowed({')) {
  if (!source.includes(spartanCgroupAdmissionMarker)) {
    throw new Error('expected Camofox memory admission marker not found');
  }
  source = source.replace(spartanCgroupAdmissionMarker, spartanCgroupAdmission + spartanCgroupAdmissionMarker);
}

const spartanDeepHealthMarker = "app.get('/health',";
const spartanDeepHealthRoutes = `const spartanDeepHealthSecretPath = process.env.SPARTAN_CAMOFOX_INTERNAL_SECRET_FILE
  || '/run/spartan-camofox/internal.key';
const spartanDeepHealth = createSpartanDeepHealthController({
  getBrowser: ensureBrowser,
  recoverBrowser: () => restartBrowser('spartan_deep_health'),
  probeTimeoutMs: Number(process.env.SPARTAN_CAMOFOX_DEEP_TIMEOUT_MS || 8000),
  cleanupTimeoutMs: Number(process.env.SPARTAN_CAMOFOX_DEEP_CLEANUP_TIMEOUT_MS || 2000),
  recoveryTimeoutMs: Number(process.env.SPARTAN_CAMOFOX_SERVER_RECOVERY_TIMEOUT_MS || 20000),
});

function spartanRequireInternalHealth(req, res) {
  if (spartanInternalRequestAuthorized(req, spartanDeepHealthSecretPath)) return true;
  res.status(403).json({ ok: false, code: 'forbidden' });
  return false;
}

app.get('/spartan/health/deep', async (req, res) => {
  if (!spartanRequireInternalHealth(req, res)) return;
  const result = await spartanDeepHealth.probe();
  res.status(result.ok ? 200 : 503).json(result);
});

app.post('/spartan/recover', async (req, res) => {
  if (!spartanRequireInternalHealth(req, res)) return;
  const result = await spartanDeepHealth.recover();
  res.status(result.ok ? 200 : 503).json(result);
});

`;
if (!source.includes("app.get('/spartan/health/deep'")) {
  if (!source.includes(spartanDeepHealthMarker)) {
    throw new Error('expected Camofox health route marker not found');
  }
  source = source.replace(spartanDeepHealthMarker, spartanDeepHealthRoutes + spartanDeepHealthMarker);
}

// Camoufox can wedge when one context close overlaps one page create or
// navigation. Converge pristine upstream and the deployed create-only FIFO on
// one versioned, re-entrant lifecycle coordinator. Unknown partial variants
// fail before the single server.js write at the end of this patcher.
const spartanLifecycleVersionMarker = 'const SPARTAN_LIFECYCLE_COORDINATOR_VERSION = 1;';
const spartanLifecycleRevisionMarker = 'const SPARTAN_LIFECYCLE_COORDINATOR_REVISION = 2;';
const spartanLifecycleQueuedCreateMarker = 'const SPARTAN_QUEUED_CREATE_FENCE_REVISION = 1;';
const spartanReadinessRevisionMarker = 'const SPARTAN_CAMOFOX_READINESS_REVISION = 2;';
const spartanReadinessRuntimeBlock = `let spartanBrowserGeneration = 0;

function spartanPublishBrowser(candidate) {
  browser = candidate;
  spartanBrowserGeneration++;
  return candidate;
}

const spartanNavigationReadiness = createSpartanNavigationReadiness({
  getGeneration: () => spartanBrowserGeneration,
  markDegraded: code => spartanBrowserLifecycle.markDegraded(code),
});
const SPARTAN_NAVIGATION_ATTEMPT_FAILURE = Symbol('spartanNavigationAttemptFailure');

function spartanMarkNavigationAttemptError(error) {
  if (!error || (typeof error !== 'object' && typeof error !== 'function')) return error;
  const message = String(error.message || '');
  const excluded = isProxyError(error)
    || error.code === 'proxy_forbidden'
    || error.statusCode === 400
    || message.includes('Navigation aborted')
    || message.includes('SEC_ERROR')
    || message.includes('SSL_ERROR')
    || message.includes('MOZILLA_PKIX_ERROR');
  if (excluded) return error;
  const failureClass = isTimeoutError(error) ? 'internal_timeout' : null;
  if (!failureClass) return error;
  try {
    Object.defineProperty(error, SPARTAN_NAVIGATION_ATTEMPT_FAILURE, {
      value: failureClass,
      configurable: true,
    });
  } catch {
    // A frozen upstream error still fails the request, but cannot poison readiness.
  }
  return error;
}

async function spartanObserveNavigationAttempt(operation) {
  try {
    return await operation();
  } catch (navErr) {
    spartanMarkNavigationAttemptError(navErr);
    throw navErr;
  }
}

function spartanSettleNavigationFailure(ticket, error) {
  ticket?.failure({ qualifying: Boolean(error?.[SPARTAN_NAVIGATION_ATTEMPT_FAILURE]) });
}

function spartanClassifyPublicFailure(error) {
  const code = String(error?.code || '');
  const message = String(error?.message || '');
  const capacityCodes = new Set([
    'admission_rejected',
    'memory_pressure',
    'tab_capacity_reached',
    'tab_create_queue_full',
    'browser_lifecycle_queue_full',
  ]);
  const capacityMessage = message.includes('Maximum concurrent sessions reached')
    || message.includes('Maximum tabs')
    || message.includes('User concurrency limit reached')
    || message.includes('queue is full');
  if (error?.statusCode === 429 || capacityCodes.has(code) || capacityMessage) {
    let publicCode = 'local_capacity_reached';
    if (code === 'tab_capacity_reached' || message.includes('Maximum tabs')) {
      publicCode = 'tab_capacity_reached';
    } else if (message.includes('Maximum concurrent sessions')) {
      publicCode = 'session_capacity_reached';
    } else if (message.includes('User concurrency')) {
      publicCode = 'user_concurrency_reached';
    }
    return {
      statusCode: 429,
      code: publicCode,
      failureClass: 'capacity_limit',
      publicMessage: 'Local Camofox capacity reached; retry later.',
    };
  }

  const browserStateCodes = new Set([
    'browser_navigation_degraded',
    'browser_lifecycle_degraded',
    'browser_lifecycle_stuck',
    'browser_lifecycle_recovering',
    'browser_recovering',
    'browser_recovery_failed',
  ]);
  if (
    error?.[SPARTAN_NAVIGATION_ATTEMPT_FAILURE] === 'browser_state'
    || browserStateCodes.has(code)
    || isDeadContextError(error)
  ) {
    return {
      statusCode: 503,
      code: code === 'browser_navigation_degraded'
        ? code
        : 'browser_navigation_unavailable',
      failureClass: 'browser_state',
      publicMessage: 'Camofox browser state is unavailable; retry after recovery.',
    };
  }

  const timeoutCodes = new Set([
    'tab_create_timeout',
    'tab_create_queue_timeout',
    'browser_lifecycle_queue_timeout',
    'deep_probe_timeout',
  ]);
  if (
    error?.[SPARTAN_NAVIGATION_ATTEMPT_FAILURE] === 'internal_timeout'
    || timeoutCodes.has(code)
    || isTimeoutError(error)
  ) {
    return {
      statusCode: 503,
      code: error?.[SPARTAN_NAVIGATION_ATTEMPT_FAILURE] === 'internal_timeout'
        ? 'navigation_timeout'
        : (timeoutCodes.has(code) ? code : 'request_timeout'),
      failureClass: 'internal_timeout',
      publicMessage: 'Camofox operation timed out; retry after recovery.',
    };
  }
  return null;
}

function spartanApplyPublicFailure(error) {
  const classification = spartanClassifyPublicFailure(error);
  if (!classification || !error) return false;
  error.statusCode = classification.statusCode;
  error.code = classification.code;
  error.failureClass = classification.failureClass;
  error.spartanPublicMessage = classification.publicMessage;
  return true;
}

function spartanIsCapacityFailure(error) {
  return spartanClassifyPublicFailure(error)?.failureClass === 'capacity_limit';
}

function spartanPublicReadinessSnapshot() {
  const generation = spartanBrowserGeneration;
  const navigation = spartanNavigationReadiness.snapshot();
  if (navigation.latched) {
    return {
      ok: false,
      code: 'browser_navigation_degraded',
      stage: 'lifecycle',
      generation,
      ageMs: navigation.latchedAt === null ? null : Math.max(0, Date.now() - navigation.latchedAt),
    };
  }
  if (healthState.isRecovering) {
    return { ok: false, code: 'browser_recovering', stage: 'recovery', generation, ageMs: 0 };
  }
  const lifecycle = spartanBrowserLifecycle.snapshot();
  if (lifecycle.recovering) {
    return { ok: false, code: 'browser_recovering', stage: 'recovery', generation, ageMs: 0 };
  }
  if (lifecycle.degradedCode) {
    return { ok: false, code: lifecycle.degradedCode, stage: 'lifecycle', generation, ageMs: null };
  }
  const connected = browser !== null && (browser.isConnected?.() ?? false);
  if (!connected) {
    return { ok: false, code: 'browser_disconnected', stage: 'browser', generation, ageMs: null };
  }
  return spartanDeepHealth.snapshot();
}

const SPARTAN_DEEP_PUBLIC_CODES = new Set([
  'browser_lifecycle_degraded',
  'browser_lifecycle_invalidated',
  'browser_lifecycle_queue_timeout',
  'browser_lifecycle_recovering',
  'browser_lifecycle_stuck',
  'browser_navigation_degraded',
  'busy',
  'deep_probe_busy_stale',
  'deep_probe_cleanup_failed',
  'deep_probe_failed',
  'deep_probe_sentinel',
  'deep_probe_timeout',
  'ready',
]);
const SPARTAN_DEEP_PUBLIC_STAGES = new Set([
  'cleanup', 'complete', 'ensure_browser', 'evaluate', 'goto', 'lifecycle',
  'lifecycle_settle', 'new_context', 'new_page',
]);

function spartanDeepHealthPayload(result) {
  let ok = result?.ok === true;
  let code = SPARTAN_DEEP_PUBLIC_CODES.has(result?.code) ? result.code : 'deep_probe_failed';
  let stage = SPARTAN_DEEP_PUBLIC_STAGES.has(result?.stage) ? result.stage : 'lifecycle';
  if (code === 'ready' && stage !== 'complete') {
    ok = false;
    code = 'deep_probe_failed';
    stage = 'lifecycle';
  } else if (code === 'busy') {
    ok = ok && stage === 'lifecycle';
  } else if (code === 'browser_navigation_degraded') {
    ok = false;
    stage = 'lifecycle';
  } else if (code !== 'ready' && code !== 'busy') {
    ok = false;
  }
  const browserGeneration = Number.isSafeInteger(result?.generation)
    && result.generation >= 0
    ? result.generation
    : spartanBrowserGeneration;
  return { ok, code, stage, browserGeneration };
}
`;

const spartanReadinessHealthRoutes = `app.get('/health', (_req, res) => {
  const readiness = spartanPublicReadinessSnapshot();
  const navigation = spartanNavigationReadiness.snapshot();
  const running = browser !== null && (browser.isConnected?.() ?? false);
  const mem = process.memoryUsage();
  const rssMb = Math.round(mem.rss / 1048576);
  const heapUsedMb = Math.round(mem.heapUsed / 1048576);
  const nativeMemMb = rssMb - heapUsedMb;
  res.status(readiness.ok ? 200 : 503).json({
    ok: readiness.ok,
    engine: 'camoufox',
    browserConnected: running,
    browserRunning: running,
    browserGeneration: spartanBrowserGeneration,
    activeTabs: getTotalTabCount(),
    activeSessions: sessions.size,
    consecutiveFailures: navigation.consecutiveFailures,
    readiness: {
      code: readiness.code,
      stage: readiness.stage || null,
      ageMs: readiness.ageMs ?? null,
      generation: readiness.generation ?? spartanBrowserGeneration,
    },
    memory: { rssMb, heapUsedMb, nativeMemMb },
    ...(FLY_MACHINE_ID ? { machineId: FLY_MACHINE_ID } : {}),
  });
});

app.get('/live', (_req, res) => {
  res.status(200).json({ ok: true, engine: 'camoufox', live: true });
});`;

function spartanReplaceReadinessOnce(text, marker, replacement, label) {
  const count = text.split(marker).length - 1;
  if (count !== 1) {
    throw new Error(`expected one Camofox readiness ${label} marker, found ${count}`);
  }
  return text.replace(marker, replacement);
}

function spartanReplaceReadinessVariant(text, variants, label) {
  const matches = variants.filter(([marker]) => text.includes(marker));
  if (matches.length !== 1) {
    throw new Error(`expected one Camofox readiness ${label} variant, found ${matches.length}`);
  }
  return spartanReplaceReadinessOnce(text, matches[0][0], matches[0][1], label);
}

function spartanReadinessRoute(prefix, label) {
  const start = source.indexOf(prefix);
  if (start === -1 || source.indexOf(prefix, start + prefix.length) !== -1) {
    throw new Error(`expected one Camofox readiness ${label} route`);
  }
  const end = spartanFindCallExpressionEnd(source, start);
  if (end === -1) throw new Error(`expected Camofox readiness ${label} route end`);
  return { value: source.slice(start, end) };
}

const spartanLifecycleRuntimeBlock = `const userConcurrency = new Map();

const SPARTAN_LIFECYCLE_COORDINATOR_VERSION = 1;
const SPARTAN_MAX_CONCURRENT_CREATES_RAW = Number(process.env.SPARTAN_CAMOFOX_MAX_CONCURRENT_CREATES || '1');
if (!Number.isInteger(SPARTAN_MAX_CONCURRENT_CREATES_RAW) || SPARTAN_MAX_CONCURRENT_CREATES_RAW !== 1) {
  throw new Error('SPARTAN_CAMOFOX_MAX_CONCURRENT_CREATES must be exactly 1');
}
const SPARTAN_RECOVERY_DRAIN_TIMEOUT_MS = Number(
  process.env.SPARTAN_CAMOFOX_RECOVERY_DRAIN_TIMEOUT_MS || 2000,
);
if (!Number.isFinite(SPARTAN_RECOVERY_DRAIN_TIMEOUT_MS) || SPARTAN_RECOVERY_DRAIN_TIMEOUT_MS < 1) {
  throw new Error('SPARTAN_CAMOFOX_RECOVERY_DRAIN_TIMEOUT_MS must be positive');
}

const spartanBrowserLifecycle = createSpartanBrowserLifecycleCoordinator({
  maxQueued: MAX_SESSIONS,
  backgroundAgingMs: 1000,
});
const spartanGuardedBrowsers = new WeakSet();
const spartanGuardedContexts = new WeakSet();
const spartanGuardedPages = new WeakSet();
const spartanClosePromises = new WeakMap();
const SPARTAN_PAGE_LIFECYCLE_METHODS = Object.freeze(['close']);

function spartanLifecycleMethodKind(prefix, methodName) {
  return \`\${prefix}_\${String(methodName).replace(/[A-Z]/g, value => \`_\${value.toLowerCase()}\`)}\`;
}

function spartanTrackLifecyclePromise(promise) {
  return spartanBrowserLifecycle.hasLease()
    ? spartanBrowserLifecycle.track(promise)
    : promise;
}

function spartanGuardLifecycleMethod(target, methodName, {
  prefix,
  priority = 'foreground',
  transform = value => value,
} = {}) {
  const original = target?.[methodName];
  if (typeof original !== 'function') return;
  const kind = spartanLifecycleMethodKind(prefix, methodName);
  target[methodName] = function spartanGuardedLifecycleMethod(...args) {
    if (methodName === 'close') {
      const existing = spartanClosePromises.get(target);
      if (existing) return existing;
    }
    const invoke = () => Promise.resolve()
      .then(() => original.apply(this, args))
      .then(transform);
    const running = spartanBrowserLifecycle.hasLease()
      ? spartanBrowserLifecycle.track(invoke())
      : spartanBrowserLifecycle.run(kind, invoke, {
          priority,
          waitTimeoutMs: priority === 'background'
            ? null
            : Math.max(1, HANDLER_TIMEOUT_MS - 1000),
          deadlineAt: priority === 'background'
            ? null
            : Date.now() + HANDLER_TIMEOUT_MS,
        });
    if (methodName === 'close') spartanClosePromises.set(target, running);
    return running;
  };
}

function spartanGuardPage(page) {
  if (!page || spartanGuardedPages.has(page)) return page;
  spartanGuardedPages.add(page);
  for (const methodName of SPARTAN_PAGE_LIFECYCLE_METHODS) {
    spartanGuardLifecycleMethod(page, methodName, {
      prefix: 'page',
      priority: methodName === 'close' ? 'background' : 'foreground',
    });
  }
  return page;
}

function spartanGuardContext(context) {
  if (!context || spartanGuardedContexts.has(context)) return context;
  spartanGuardedContexts.add(context);
  spartanGuardLifecycleMethod(context, 'newPage', {
    prefix: 'context',
    transform: spartanGuardPage,
  });
  spartanGuardLifecycleMethod(context, 'addCookies', { prefix: 'context' });
  spartanGuardLifecycleMethod(context, 'close', {
    prefix: 'context',
    priority: 'background',
  });
  try {
    for (const page of context.pages?.() || []) spartanGuardPage(page);
  } catch {
    // Keep a stale context guarded without masking the original browser error.
  }
  return context;
}

function spartanGuardBrowser(candidate) {
  if (!candidate || spartanGuardedBrowsers.has(candidate)) return candidate;
  spartanGuardedBrowsers.add(candidate);
  spartanGuardLifecycleMethod(candidate, 'newContext', {
    prefix: 'browser',
    transform: spartanGuardContext,
  });
  spartanGuardLifecycleMethod(candidate, 'close', {
    prefix: 'browser',
    priority: 'background',
  });
  return candidate;
}

async function spartanWithCreateLimit(operation, queueTimeoutMs = 30000) {
  const timeoutMs = Number(queueTimeoutMs);
  if (!Number.isFinite(timeoutMs) || timeoutMs < 1) {
    throw spartanBrowserLifecycleError(
      'browser_lifecycle_queue_timeout',
      'Camofox browser lifecycle queue deadline expired',
    );
  }
  return await spartanBrowserLifecycle.run('tab_create', operation, {
    priority: 'foreground',
    waitTimeoutMs: timeoutMs,
    deadlineAt: Date.now() + timeoutMs,
  });
}`;

function spartanReplaceLifecycleOnce(marker, replacement, label) {
  const first = source.indexOf(marker);
  if (first === -1) throw new Error(`expected Camofox lifecycle ${label} marker not found`);
  if (source.indexOf(marker, first + marker.length) !== -1) {
    throw new Error(`duplicate Camofox lifecycle ${label} marker`);
  }
  source = source.slice(0, first) + replacement + source.slice(first + marker.length);
}

function spartanLifecycleRange(startMarker, endMarker, label) {
  const start = source.indexOf(startMarker);
  if (start === -1) throw new Error(`expected Camofox lifecycle ${label} start not found`);
  const end = source.indexOf(endMarker, start + startMarker.length);
  if (end === -1) throw new Error(`expected Camofox lifecycle ${label} end not found`);
  return { start, end, value: source.slice(start, end) };
}

function spartanReplaceLifecycleRange(range, replacement) {
  const currentStart = source.indexOf(range.value);
  if (currentStart === -1) {
    throw new Error('Camofox lifecycle range changed after preflight');
  }
  if (source.indexOf(range.value, currentStart + range.value.length) !== -1) {
    throw new Error('duplicate Camofox lifecycle range after preflight');
  }
  source = source.slice(0, currentStart)
    + replacement
    + source.slice(currentStart + range.value.length);
}

const spartanLifecycleVersionCount = source.split(spartanLifecycleVersionMarker).length - 1;
if (spartanLifecycleVersionCount > 1) {
  throw new Error('duplicate Camofox lifecycle coordinator version marker');
}
if (spartanLifecycleVersionCount === 0) {
  const legacyCreateBlock = spartanCreateLimitReplacement;
  if (!source.includes(legacyCreateBlock)) {
    throw new Error('unsupported Camofox create limiter variant; refusing partial lifecycle migration');
  }

  const tabLockRange = spartanLifecycleRange(
    'async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {',
    '\n}\n\nfunction withTimeout(',
    'tab lock',
  );
  const expectedTabLockBody = `async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {
  const lock = getTabLock(tabId);
  await lock.acquire(TAB_LOCK_TIMEOUT_MS);
  try {
    return await withTimeout(operation(), timeoutMs, 'action');
  } finally {
    lock.release();
  }`;
  if (tabLockRange.value !== expectedTabLockBody) {
    throw new Error('unsupported Camofox tab-lock variant; refusing lifecycle migration');
  }

  const ensureRange = spartanLifecycleRange(
    'async function ensureBrowser() {',
    '\n}\n\n// Helper to normalize userId',
    'ensure browser',
  );
  const closeSessionRange = spartanLifecycleRange(
    'async function closeSession(userId, session, {',
    '\n}\n\nasync function closeAllSessions(',
    'close session',
  );
  const restartRange = spartanLifecycleRange(
    'async function restartBrowser(reason) {',
    '\n}\n\nfunction getTotalTabCount(',
    'restart browser',
  );
  const popupRange = spartanLifecycleRange(
    'function attachPopupHandler(page, userId, sessionKey) {',
    '\n}\n\nfunction pressureHash(',
    'popup handler',
  );
  const openRouteRange = spartanLifecycleRange(
    "app.post('/tabs/open', async (req, res) => {",
    "\n});\n\n// POST /start",
    'tabs open route',
  );

  for (const [marker, label] of [
    ['async function closeBrowserFully(reason) {\n  if (_browserClosePromise)', 'close browser'],
    ['async function closeAllSessions(reason, { clearDownloads = true, clearLocks = true } = {}) {', 'close all sessions'],
    ['async function getSession(userId, { trace = false } = {}) {', 'get session'],
    ['async function withUserLimit(userId, operation) {', 'user operation'],
    ['  return Promise.race([\n    promise,\n    new Promise((_, reject) =>', 'timeout tracking'],
    ['await session.context.close().catch(() => {});', 'context close propagation'],
    ["  } catch (err) {\n    log('error', 'browser restart failed', { error: err.message });\n  } finally {", 'restart error propagation'],
    ['session.tabGroups.size === 0\n      && !spartanSessionCreatePending', 'empty-session reaper'],
    ['    const session = await getSession(userId);\n    await session.context.addCookies(sanitized);', 'cookie import'],
    [spartanDeepHealthRoutes, 'legacy deep health'],
  ]) {
    if (!source.includes(marker)) {
      throw new Error(`expected Camofox lifecycle ${label} marker not found`);
    }
  }

  spartanReplaceLifecycleOnce(legacyCreateBlock, spartanLifecycleRuntimeBlock, 'legacy create limiter');

  spartanReplaceLifecycleOnce(
    '  return Promise.race([\n    promise,\n    new Promise((_, reject) =>',
    '  return Promise.race([\n    spartanTrackLifecyclePromise(promise),\n    new Promise((_, reject) =>',
    'timeout promise tracking',
  );

  spartanReplaceLifecycleOnce(
    'async function withUserLimit(userId, operation) {',
    `async function withUserLimit(userId, operation) {
  if (!spartanBrowserLifecycle.hasLease()) {
    const lifecycleTimeoutMs = requestTimeoutMs();
    return await spartanBrowserLifecycle.run('user_operation', () => withUserLimit(userId, operation), {
      priority: 'foreground',
      waitTimeoutMs: Math.max(1, lifecycleTimeoutMs - 1000),
      deadlineAt: Date.now() + lifecycleTimeoutMs,
    });
  }`,
    'user operation guard',
  );

  const coordinatedTabLock = `async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {
  if (spartanBrowserLifecycle.hasLease()) {
    const lock = getTabLock(tabId);
    await lock.acquire(TAB_LOCK_TIMEOUT_MS);
    try {
      return await operation();
    } finally {
      lock.release();
    }
  }
  const underlying = spartanBrowserLifecycle.run('tab_operation', async () => {
    const lock = getTabLock(tabId);
    await lock.acquire(TAB_LOCK_TIMEOUT_MS);
    try {
      return await operation();
    } finally {
      lock.release();
    }
  }, {
    priority: 'foreground',
    waitTimeoutMs: Math.max(1, Number(timeoutMs) - 1000),
    deadlineAt: Date.now() + Number(timeoutMs),
  });
  return await withTimeout(underlying, timeoutMs, 'action');`;
  spartanReplaceLifecycleRange(tabLockRange, coordinatedTabLock);

  let coordinatedEnsure = ensureRange.value.replace(
    'async function ensureBrowser() {',
    `async function ensureBrowser() {
  if (!spartanBrowserLifecycle.hasLease()) {
    return await spartanBrowserLifecycle.run('browser_ensure', () => ensureBrowser(), {
      priority: 'foreground',
      waitTimeoutMs: Math.max(1, HANDLER_TIMEOUT_MS - 1000),
      deadlineAt: Date.now() + HANDLER_TIMEOUT_MS,
    });
  }`,
  );
  coordinatedEnsure = coordinatedEnsure
    .replace('if (browser) return browser;', 'if (browser) return spartanGuardBrowser(browser);')
    .replace(
      'if (browserLaunchPromise) return browserLaunchPromise;',
      'if (browserLaunchPromise) return browserLaunchPromise.then(spartanGuardBrowser);',
    )
    .replace('return browserLaunchPromise;', 'return browserLaunchPromise.then(spartanGuardBrowser);');
  if (!coordinatedEnsure.includes('return browserLaunchPromise.then(spartanGuardBrowser);')) {
    throw new Error('expected Camofox ensure-browser return marker not found');
  }
  spartanReplaceLifecycleRange(ensureRange, coordinatedEnsure);

  spartanReplaceLifecycleOnce(
    'async function closeBrowserFully(reason) {\n  if (_browserClosePromise)',
    `async function closeBrowserFully(reason) {
  if (!spartanBrowserLifecycle.hasLease()) {
    return await spartanBrowserLifecycle.run(
      'browser_close',
      () => closeBrowserFully(reason),
      { priority: 'background', waitTimeoutMs: null },
    );
  }
  if (_browserClosePromise)`,
    'close browser guard',
  );

  let coordinatedCloseSession = closeSessionRange.value.replace(
    `} = {}) {
  if (!session) return;`,
    `} = {}) {
  if (!session) return;
  if (!spartanBrowserLifecycle.hasLease()) {
    if (session._spartanClosePromise) return await session._spartanClosePromise;
    session._closing = true;
    const closing = spartanBrowserLifecycle.run(
      'session_close',
      async () => {
        if (session._spartanCloseComplete) return;
        return await closeSession(userId, session, { reason, clearDownloads, clearLocks });
      },
      { priority: 'background', waitTimeoutMs: null },
    ).catch(error => {
      const expectedRecoveryAbort = error?.code === 'browser_lifecycle_invalidated'
        || error?.code === 'browser_lifecycle_recovering';
      if (!expectedRecoveryAbort) {
        spartanBrowserLifecycle.markDegraded('session_close_failed');
      }
      throw error;
    });
    session._spartanClosePromise = closing;
    return await closing;
  }
  if (session._spartanCloseComplete) return;
  session._closing = true;`,
  );
  coordinatedCloseSession = coordinatedCloseSession.replace(
    '  await session.context.close().catch(() => {});',
    `  try {
    await session.context.close();
  } catch (error) {
    const closeMessage = String(error?.message || '');
    const alreadyClosed = closeMessage.includes('Target page, context or browser has been closed')
      || closeMessage.includes('browser has been closed')
      || closeMessage.includes('Context closed')
      || closeMessage.includes('Browser closed');
    if (!alreadyClosed) {
      spartanBrowserLifecycle.markDegraded('session_close_failed');
      throw error;
    }
  }`,
  );
  coordinatedCloseSession = coordinatedCloseSession.replace(
    '  if (sessions.get(key) === session) sessions.delete(key);',
    `  if (sessions.get(key) === session) sessions.delete(key);
  session._spartanCloseComplete = true;`,
  );
  if (!coordinatedCloseSession.includes('session._spartanClosePromise = closing;')) {
    throw new Error('expected Camofox close-session body marker not found');
  }
  spartanReplaceLifecycleRange(closeSessionRange, coordinatedCloseSession);

  spartanReplaceLifecycleOnce(
    'async function closeAllSessions(reason, { clearDownloads = true, clearLocks = true } = {}) {',
    `async function closeAllSessions(reason, { clearDownloads = true, clearLocks = true } = {}) {
  if (!spartanBrowserLifecycle.hasLease()) {
    return await spartanBrowserLifecycle.run(
      'sessions_close_all',
      () => closeAllSessions(reason, { clearDownloads, clearLocks }),
      { priority: 'background', waitTimeoutMs: null },
    );
  }`,
    'close all sessions guard',
  );

  spartanReplaceLifecycleOnce(
    'async function getSession(userId, { trace = false } = {}) {',
    `async function getSession(userId, { trace = false } = {}) {
  if (!spartanBrowserLifecycle.hasLease()) {
    return await spartanBrowserLifecycle.run(
      'session_get',
      () => getSession(userId, { trace }),
      {
        priority: 'foreground',
        waitTimeoutMs: Math.max(1, HANDLER_TIMEOUT_MS - 1000),
        deadlineAt: Date.now() + HANDLER_TIMEOUT_MS,
      },
    );
  }`,
    'get session guard',
  );

  let coordinatedRestart = restartRange.value.replace(
    'async function restartBrowser(reason) {',
    `async function restartBrowser(reason) {
  if (!spartanBrowserLifecycle.isRecoveryLease()) {
    if (spartanBrowserLifecycle.hasLease()) {
      spartanBrowserLifecycle.markDegraded('browser_recovery_reentrant');
      throw spartanBrowserLifecycleError(
        'browser_recovery_reentrant',
        'browser recovery requested by an active lifecycle operation',
      );
    }
    return await spartanBrowserLifecycle.recover(
      () => restartBrowser(reason),
      { drainTimeoutMs: SPARTAN_RECOVERY_DRAIN_TIMEOUT_MS },
    );
  }`,
  );
  coordinatedRestart = coordinatedRestart.replace(
    "  } catch (err) {\n    log('error', 'browser restart failed', { error: err.message });\n  } finally {",
    "  } catch (err) {\n    log('error', 'browser restart failed', { error: err.message });\n    throw err;\n  } finally {",
  );
  spartanReplaceLifecycleRange(restartRange, coordinatedRestart);

  let coordinatedPopup = popupRange.value
    .replace(
      `function attachPopupHandler(page, userId, sessionKey) {
  page.on('popup', (popupPage) => {
    const key = normalizeUserId(userId);
    const currentSession = sessions.get(key);
    if (!currentSession || currentSession._closing) return;`,
      `function attachPopupHandler(page, userId, sessionKey) {
  const key = normalizeUserId(userId);
  const sourceSession = sessions.get(key);
  page = spartanGuardPage(page);
  page.on('popup', (popupPage) => {
    const guardedPopup = spartanGuardPage(popupPage);
    const currentSession = sessions.get(key);
    if (!sourceSession || currentSession !== sourceSession || currentSession._closing) {
      safePageClose(guardedPopup).catch(() => {});
      return;
    }`,
    )
    .replace('const popupTabState = createTabState(popupPage);', 'const popupTabState = createTabState(guardedPopup);')
    .replaceAll('url: popupPage.url()', 'url: guardedPopup.url()')
    .replace('attachPopupHandler(popupPage, userId, sessionKey);', 'attachPopupHandler(guardedPopup, userId, sessionKey);');
  if (!coordinatedPopup.includes('currentSession !== sourceSession')) {
    throw new Error('expected Camofox popup handler body marker not found');
  }
  spartanReplaceLifecycleRange(popupRange, coordinatedPopup);

  spartanReplaceLifecycleOnce(
    'session.tabGroups.size === 0\n      && !spartanSessionCreatePending',
    'session.tabGroups.size === 0\n      && !spartanSessionCreatePending\n      && !session._closing',
    'empty-session reaper guard',
  );

  spartanReplaceLifecycleOnce(
    '    const session = await getSession(userId);\n    await session.context.addCookies(sanitized);',
    `    const session = await spartanBrowserLifecycle.run('cookie_import', async () => {
      const current = await getSession(userId);
      await current.context.addCookies(sanitized);
      return current;
    }, {
      priority: 'foreground',
      waitTimeoutMs: Math.max(1, HANDLER_TIMEOUT_MS - 1000),
      deadlineAt: Date.now() + HANDLER_TIMEOUT_MS,
    });`,
    'cookie import operation',
  );

  const openRouteBody = openRouteRange.value.slice(
    "app.post('/tabs/open', async (req, res) => {".length,
  );
  const indentedOpenRouteBody = openRouteBody
    .split('\n')
    .map(line => line ? `    ${line}` : line)
    .join('\n');
  const coordinatedOpenRoute = `app.post('/tabs/open', async (req, res) => {
  try {
    return await spartanBrowserLifecycle.run('tab_open', async () => {${indentedOpenRouteBody}
    }, {
      priority: 'foreground',
      waitTimeoutMs: Math.max(1, requestTimeoutMs() - 1000),
      deadlineAt: Date.now() + requestTimeoutMs(),
    });
  } catch (err) {
    log('error', 'openclaw tab lifecycle admission failed', {
      reqId: req.reqId,
      code: err?.code || 'browser_lifecycle_failed',
    });
    handleRouteError(err, req, res);
  }`;
  spartanReplaceLifecycleRange(openRouteRange, coordinatedOpenRoute);

  const coordinatedDeepHealthRoutes = `const spartanDeepHealthSecretPath = process.env.SPARTAN_CAMOFOX_INTERNAL_SECRET_FILE
  || '/run/spartan-camofox/internal.key';
const SPARTAN_DEEP_PROBE_TIMEOUT_MS = Number(process.env.SPARTAN_CAMOFOX_DEEP_TIMEOUT_MS || 8000);
const SPARTAN_DEEP_CLEANUP_TIMEOUT_MS = Number(process.env.SPARTAN_CAMOFOX_DEEP_CLEANUP_TIMEOUT_MS || 1000);
const spartanDeepHealth = createSpartanDeepHealthController({
  getBrowser: ensureBrowser,
  recoverBrowser: () => restartBrowser('spartan_deep_health'),
  runExclusive: operation => {
    const lifecycle = spartanBrowserLifecycle.snapshot();
    const fallbackDeadlineExceeded = lifecycle.ownerKind
      && lifecycle.ownerDeadlineAt === null
      && lifecycle.ownerAgeMs > HANDLER_TIMEOUT_MS;
    if (lifecycle.ownerDeadlineExceeded || fallbackDeadlineExceeded) {
      throw spartanBrowserLifecycleError(
        'browser_lifecycle_stuck',
        'browser lifecycle owner exceeded its declared deadline',
      );
    }
    return spartanBrowserLifecycle.run('deep_probe', operation, {
      priority: 'health',
      waitTimeoutMs: 500,
      deadlineAt: Date.now() + SPARTAN_DEEP_PROBE_TIMEOUT_MS + SPARTAN_DEEP_CLEANUP_TIMEOUT_MS,
    }).catch(error => {
      if (error?.code !== 'browser_lifecycle_queue_timeout') throw error;
      const afterWait = spartanBrowserLifecycle.snapshot();
      const ownerWithinDeadline = afterWait.ownerKind
        && !afterWait.ownerDeadlineExceeded
        && !(afterWait.ownerDeadlineAt === null && afterWait.ownerAgeMs > HANDLER_TIMEOUT_MS);
      if (!ownerWithinDeadline) throw error;
      throw spartanBrowserLifecycleError(
        'browser_lifecycle_busy',
        'browser lifecycle is busy within its declared deadline',
      );
    });
  },
  probeTimeoutMs: SPARTAN_DEEP_PROBE_TIMEOUT_MS,
  cleanupTimeoutMs: SPARTAN_DEEP_CLEANUP_TIMEOUT_MS,
  recoveryTimeoutMs: Number(process.env.SPARTAN_CAMOFOX_SERVER_RECOVERY_TIMEOUT_MS || 20000),
});

function spartanRequireInternalHealth(req, res) {
  if (spartanInternalRequestAuthorized(req, spartanDeepHealthSecretPath)) return true;
  res.status(403).json({ ok: false, code: 'forbidden' });
  return false;
}

app.get('/spartan/health/deep', async (req, res) => {
  if (!spartanRequireInternalHealth(req, res)) return;
  const result = await spartanDeepHealth.probe();
  res.status(result.ok ? 200 : 503).json(result);
});

app.post('/spartan/recover', async (req, res) => {
  if (!spartanRequireInternalHealth(req, res)) return;
  const result = await spartanDeepHealth.recover();
  res.status(result.ok ? 200 : 503).json(result);
});

`;
  spartanReplaceLifecycleOnce(
    spartanDeepHealthRoutes,
    coordinatedDeepHealthRoutes,
    'deep health coordination',
  );
} else {
  for (const [marker, label] of [
    ["spartanBrowserLifecycle.run('deep_probe'", 'deep health'],
    ['spartanTrackLifecyclePromise(promise)', 'timeout promise tracking'],
    ['SPARTAN_MAX_CONCURRENT_CREATES_RAW !== 1', 'fixed lifecycle capacity'],
    ["spartanBrowserLifecycle.run('cookie_import'", 'cookie import'],
    ['const closing = spartanBrowserLifecycle.run(', 'session close'],
    ['currentSession !== sourceSession', 'popup generation fence'],
    ['&& !session._closing', 'reaper closing guard'],
  ]) {
    if (!source.includes(marker)) {
      throw new Error(`Camofox lifecycle v1 marker is incomplete: ${label}`);
    }
  }
  if (
    !source.includes("spartanBrowserLifecycle.run('tab_open'")
    && !source.includes("'legacy_tab_open_disabled'")
  ) {
    throw new Error('Camofox lifecycle v1 marker is incomplete: tabs open');
  }
}

// Revision 2 narrows the global lane to browser/context mutation, explicit page
// navigation/close, creates, cookie import, viewport relaunch, and deep health.
// It also migrates servers already patched with revision 1 before one write.
const spartanLifecycleRevisionCount = source.split(spartanLifecycleRevisionMarker).length - 1;
if (spartanLifecycleRevisionCount > 1) {
  throw new Error('duplicate Camofox lifecycle coordinator revision marker');
}
if (spartanLifecycleRevisionCount > 0 && spartanLifecycleVersionCount === 0) {
  throw new Error('Camofox lifecycle revision exists without its version marker');
}
if (spartanLifecycleRevisionCount === 0) {
  const broadUserGuard = `async function withUserLimit(userId, operation) {
  if (!spartanBrowserLifecycle.hasLease()) {
    const lifecycleTimeoutMs = requestTimeoutMs();
    return await spartanBrowserLifecycle.run('user_operation', () => withUserLimit(userId, operation), {
      priority: 'foreground',
      waitTimeoutMs: Math.max(1, lifecycleTimeoutMs - 1000),
      deadlineAt: Date.now() + lifecycleTimeoutMs,
    });
  }`;
  const broadTabLock = `async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {
  if (spartanBrowserLifecycle.hasLease()) {
    const lock = getTabLock(tabId);
    await lock.acquire(TAB_LOCK_TIMEOUT_MS);
    try {
      return await operation();
    } finally {
      lock.release();
    }
  }
  const underlying = spartanBrowserLifecycle.run('tab_operation', async () => {
    const lock = getTabLock(tabId);
    await lock.acquire(TAB_LOCK_TIMEOUT_MS);
    try {
      return await operation();
    } finally {
      lock.release();
    }
  }, {
    priority: 'foreground',
    waitTimeoutMs: Math.max(1, Number(timeoutMs) - 1000),
    deadlineAt: Date.now() + Number(timeoutMs),
  });
  return await withTimeout(underlying, timeoutMs, 'action');`;
  const upstreamTabLock = `async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {
  const lock = getTabLock(tabId);
  await lock.acquire(TAB_LOCK_TIMEOUT_MS);
  try {
    return await withTimeout(operation(), timeoutMs, 'action');
  } finally {
    lock.release();
  }`;
  const closeOnlyMethods = "const SPARTAN_PAGE_LIFECYCLE_METHODS = Object.freeze(['close']);";
  const narrowMethods = "const SPARTAN_PAGE_LIFECYCLE_METHODS = Object.freeze(['close', 'goBack', 'goForward', 'goto', 'reload']);";
  const viewportSignature = 'async function spartanRelaunchViewportTab({ userId, sessionKey, tabId, tabState, requested, effective, timeoutMs }) {';
  const viewportGuard = `async function spartanRelaunchViewportTab(options) {
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
  }`;
  const closeCacheMarker = "    if (methodName === 'close') spartanClosePromises.set(target, running);";
  const closeCacheReplacement = `    if (methodName === 'close') {
      spartanClosePromises.set(target, running);
      const clearClosePromise = () => {
        if (spartanClosePromises.get(target) === running) spartanClosePromises.delete(target);
      };
      running.then(clearClosePromise, clearClosePromise);
    }`;
  const revisionOneCloseCatch = `    ).catch(error => {
      spartanBrowserLifecycle.markDegraded('session_close_failed');
      throw error;
    });`;
  const recoverySafeCloseCatch = `    ).catch(error => {
      const expectedRecoveryAbort = error?.code === 'browser_lifecycle_invalidated'
        || error?.code === 'browser_lifecycle_recovering';
      if (!expectedRecoveryAbort) {
        spartanBrowserLifecycle.markDegraded('session_close_failed');
      }
      throw error;
    });`;
  const deepCleanupMarker = 'const SPARTAN_DEEP_CLEANUP_TIMEOUT_MS = Number(process.env.SPARTAN_CAMOFOX_DEEP_CLEANUP_TIMEOUT_MS || 1000);\n';
  const deepCleanupReplacement = `const SPARTAN_DEEP_CLEANUP_TIMEOUT_MS = Number(process.env.SPARTAN_CAMOFOX_DEEP_CLEANUP_TIMEOUT_MS || 1000);
const SPARTAN_DEEP_BUSY_MAX_AGE_MS = Number(process.env.SPARTAN_CAMOFOX_DEEP_BUSY_MAX_AGE_MS || 45000);
`;
  const deepOptionMarker = '  cleanupTimeoutMs: SPARTAN_DEEP_CLEANUP_TIMEOUT_MS,\n  recoveryTimeoutMs:';
  const deepOptionReplacement = '  cleanupTimeoutMs: SPARTAN_DEEP_CLEANUP_TIMEOUT_MS,\n  busyMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,\n  recoveryTimeoutMs:';

  for (const [marker, label] of [
    [broadUserGuard, 'revision-1 user guard'],
    [closeOnlyMethods, 'revision-1 page methods'],
    [closeCacheMarker, 'close single-flight cache'],
    [deepCleanupMarker, 'deep busy TTL constant'],
    [deepOptionMarker, 'deep busy TTL option'],
  ]) {
    const count = source.split(marker).length - 1;
    if (count !== 1) {
      throw new Error(`expected one Camofox lifecycle ${label} marker, found ${count}`);
    }
  }
  const guardedViewportMarker = "spartanBrowserLifecycle.run(\n      'viewport_relaunch'";
  const unguardedViewportCount = source.split(viewportSignature).length - 1;
  const guardedViewportCount = source.split(guardedViewportMarker).length - 1;
  if (unguardedViewportCount + guardedViewportCount !== 1) {
    throw new Error('expected exactly one guarded or revision-1 viewport relaunch');
  }
  const revisionOneCloseCatchCount = source.split(revisionOneCloseCatch).length - 1;
  const recoverySafeCloseCatchCount = source.split(recoverySafeCloseCatch).length - 1;
  if (revisionOneCloseCatchCount + recoverySafeCloseCatchCount !== 1) {
    throw new Error('expected exactly one revision-1 or recovery-safe session close catch');
  }
  const revisionTabLockRange = spartanLifecycleRange(
    'async function withTabLock(tabId, operation, timeoutMs = HANDLER_TIMEOUT_MS) {',
    '\n}\n\nfunction withTimeout(',
    'revision-1 tab lock',
  );
  if (revisionTabLockRange.value !== broadTabLock) {
    throw new Error('unsupported Camofox revision-1 tab-lock variant');
  }
  const revisionEnsureRange = spartanLifecycleRange(
    'async function ensureBrowser() {',
    '\n}\n\n// Helper to normalize userId',
    'revision-1 ensure browser',
  );
  if ((revisionEnsureRange.value.split('launchBrowserInstance(),').length - 1) !== 1) {
    throw new Error('expected one underlying browser launch marker');
  }

  spartanReplaceLifecycleOnce(
    broadUserGuard,
    'async function withUserLimit(userId, operation) {',
    'narrow user operation',
  );
  spartanReplaceLifecycleRange(revisionTabLockRange, upstreamTabLock);
  spartanReplaceLifecycleOnce(closeOnlyMethods, narrowMethods, 'narrow page methods');
  if (unguardedViewportCount === 1) {
    spartanReplaceLifecycleOnce(viewportSignature, viewportGuard, 'viewport relaunch guard');
  }
  spartanReplaceLifecycleOnce(closeCacheMarker, closeCacheReplacement, 'close cache cleanup');
  if (revisionOneCloseCatchCount === 1) {
    spartanReplaceLifecycleOnce(
      revisionOneCloseCatch,
      recoverySafeCloseCatch,
      'recovery-safe session close catch',
    );
  }
  spartanReplaceLifecycleOnce(deepCleanupMarker, deepCleanupReplacement, 'deep busy TTL constant');
  spartanReplaceLifecycleOnce(deepOptionMarker, deepOptionReplacement, 'deep busy TTL option');

  const trackedEnsure = revisionEnsureRange.value.replace(
    'launchBrowserInstance(),',
    'spartanTrackLifecyclePromise(launchBrowserInstance()),',
  );
  spartanReplaceLifecycleRange(revisionEnsureRange, trackedEnsure);
  spartanReplaceLifecycleOnce(
    spartanLifecycleVersionMarker,
    `${spartanLifecycleVersionMarker}
${spartanLifecycleRevisionMarker}`,
    'lifecycle revision marker',
  );
} else {
  for (const [marker, label] of [
    ["const SPARTAN_PAGE_LIFECYCLE_METHODS = Object.freeze(['close', 'goBack', 'goForward', 'goto', 'reload']);", 'narrow page methods'],
    ["spartanBrowserLifecycle.run(\n      'viewport_relaunch'", 'viewport relaunch'],
    ['spartanTrackLifecyclePromise(launchBrowserInstance())', 'tracked browser launch'],
    ['spartanClosePromises.delete(target)', 'retryable close cache'],
    ['const expectedRecoveryAbort =', 'recovery-safe session close'],
    ['SPARTAN_CAMOFOX_DEEP_BUSY_MAX_AGE_MS', 'deep busy TTL'],
  ]) {
    if (!source.includes(marker)) {
      throw new Error(`Camofox lifecycle revision 2 marker is incomplete: ${label}`);
    }
  }
  for (const forbidden of [
    "spartanBrowserLifecycle.run('tab_operation'",
    "spartanBrowserLifecycle.run('user_operation'",
  ]) {
    if (source.includes(forbidden)) {
      throw new Error('Camofox lifecycle revision 2 retains a broad operation guard');
    }
  }
}

// Revision 3 closes the admission-to-lease gap: queued creates protect an
// existing session before the lifecycle mutex is acquired, so automatic
// context reapers cannot close and reopen it in the same request burst.
const spartanLifecycleQueuedCreateCount = source.split(spartanLifecycleQueuedCreateMarker).length - 1;
if (spartanLifecycleQueuedCreateCount > 1) {
  throw new Error('duplicate Camofox queued-create coordinator revision marker');
}
if (
  spartanLifecycleQueuedCreateCount > 0
  && (
    source.split(spartanLifecycleVersionMarker).length - 1 !== 1
    || source.split(spartanLifecycleRevisionMarker).length - 1 !== 1
  )
) {
  throw new Error('Camofox queued-create revision exists without prior lifecycle revisions');
}
if (spartanLifecycleQueuedCreateCount === 0) {
  const coordinatorMarker = `const spartanBrowserLifecycle = createSpartanBrowserLifecycleCoordinator({
  maxQueued: MAX_SESSIONS,
  backgroundAgingMs: 1000,
});
`;
  const queuedCreateRegistry = `${coordinatorMarker}
const SPARTAN_PENDING_USER_CREATE_LIMIT = MAX_SESSIONS + 1;
const spartanPendingUserCreates = new Map();
let spartanPendingUserCreateCount = 0;

function spartanHasPendingUserCreate(userId) {
  const pending = spartanPendingUserCreates.get(normalizeUserId(userId));
  return pending instanceof Set && pending.size > 0;
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
  return {
    count: spartanPendingUserCreateCount,
    oldestAgeMs,
    deadlineExceeded,
  };
}

function spartanDeferUserSessionDestroy(userId, session) {
  const key = normalizeUserId(userId);
  const pending = spartanPendingUserCreates.get(key);
  if (!session || !(pending instanceof Set)) return false;
  const affected = [...pending].filter(token => token.fence?.session === session);
  if (affected.length === 0) return false;
  session._closing = true;
  if (sessions.get(key) === session) sessions.delete(key);
  if (!(session._spartanDeferredDestroyTokens instanceof Set)) {
    session._spartanDeferredDestroyTokens = new Set();
  }
  for (const token of affected) {
    token.invalidatedSession = session;
    token.fence.active = false;
    session._spartanDeferredDestroyTokens.add(token);
  }
  return true;
}
function spartanBindPendingCreateSession(userId, fence, session) {
  if (!fence || typeof fence !== 'object') {
    throw new TypeError('queued create fence must be an object');
  }
  const key = normalizeUserId(userId);
  fence.userKey = key;
  fence.session = session;
  if (!session || session._closing || sessions.get(key) !== session) {
    fence.active = false;
    throw spartanBrowserLifecycleError(
      'session_invalidated_during_create',
      'session was invalidated before tab creation could bind to it',
    );
  }
  return session;
}


async function spartanWithPendingUserCreate(userId, fence, operation, deadlineAt) {
  if (!fence || typeof fence !== 'object') {
    throw new TypeError('queued create fence must be an object');
  }
  if (typeof operation !== 'function') {
    throw new TypeError('queued create operation must be a function');
  }
  const startedAt = Date.now();
  const numericDeadlineAt = Number(deadlineAt ?? (startedAt + requestTimeoutMs()));
  if (!Number.isFinite(numericDeadlineAt) || numericDeadlineAt <= 0) {
    throw new TypeError('queued create deadline must be a positive finite timestamp');
  }
  if (spartanPendingUserCreateCount >= SPARTAN_PENDING_USER_CREATE_LIMIT) {
    throw spartanBrowserLifecycleError(
      'browser_lifecycle_queue_full',
      'Camofox queued create registry is full',
    );
  }
  const key = normalizeUserId(userId);
  let pending = spartanPendingUserCreates.get(key);
  if (!(pending instanceof Set)) {
    pending = new Set();
    spartanPendingUserCreates.set(key, pending);
  }
  const token = { fence, startedAt, deadlineAt: numericDeadlineAt, invalidatedSession: null };
  pending.add(token);
  spartanPendingUserCreateCount++;
  let result;
  let failure = null;
  try {
    result = await operation();
  } catch (error) {
    failure = error;
  }
  if (pending.delete(token)) spartanPendingUserCreateCount--;
  if (pending.size === 0 && spartanPendingUserCreates.get(key) === pending) {
    spartanPendingUserCreates.delete(key);
  }
  if (token.invalidatedSession) {
    const invalidatedSession = token.invalidatedSession;
    const deferred = invalidatedSession._spartanDeferredDestroyTokens;
    if (deferred instanceof Set) {
      deferred.delete(token);
      if (deferred.size === 0) {
        delete invalidatedSession._spartanDeferredDestroyTokens;
        closeSession(key, invalidatedSession, {
          reason: 'deferred_destroy_session',
          clearDownloads: true,
          clearLocks: true,
        }).catch(() => {});
      }
    }
    throw spartanBrowserLifecycleError(
      'session_destroyed_during_create',
      'session was invalidated while tab creation was active',
    );
  }
  if (failure) throw failure;
  return result;
}
`;
  const createAdmissionOpen = 'const result = await withTimeout(spartanWithCreateLimit(async () => {';
  const createAdmissionReplacement = `const spartanCreateTimeoutMs = requestTimeoutMs();
    const spartanCreateDeadlineAt = Date.now() + spartanCreateTimeoutMs;
    const result = await withTimeout(spartanWithPendingUserCreate(userId, spartanCreateFence, () => spartanWithCreateLimit(async () => {`;
  const createQueueTimeout = '    }, Math.max(1, requestTimeoutMs() - 1000)).finally(() => {';
  const createQueueTimeoutReplacement = '    }, Math.max(1, spartanCreateDeadlineAt - Date.now() - 1000)).finally(() => {';
  const createAdmissionClose = `    }), requestTimeoutMs(), 'tab create');`;
  const createAdmissionCloseReplacement = `    }), spartanCreateDeadlineAt), spartanCreateTimeoutMs, 'tab create');`;
  const createSessionBind = '      spartanCreateFence.session = session;';
  const createSessionBindReplacement = '      spartanBindPendingCreateSession(userId, spartanCreateFence, session);';
  const sessionPendingMarker = `const spartanSessionCreatePending = session._spartanPendingCreates instanceof Set
      && session._spartanPendingCreates.size > 0;`;
  const sessionPendingReplacement = `const spartanSessionCreatePending = spartanHasPendingUserCreate(userId)
      || (
        session._spartanPendingCreates instanceof Set
        && session._spartanPendingCreates.size > 0
      );`;
  const groupPendingMarker = `const spartanGroupCreatePending = session._spartanPendingCreates instanceof Set
        && [...session._spartanPendingCreates].some(pending => pending.group === group);`;
  const groupPendingReplacement = `const spartanGroupCreatePending = spartanHasPendingUserCreate(userId)
        || (
          session._spartanPendingCreates instanceof Set
          && [...session._spartanPendingCreates].some(pending => pending.group === group)
        );`;
  const explicitCloseMarker = `  if (!session) return;
  if (!spartanBrowserLifecycle.hasLease()) {`;
  const explicitCloseReplacement = `  if (!session) return;
  if (
    reason === 'api_delete_session'
    && spartanHasPendingUserCreate(userId)
  ) {
    throw spartanBrowserLifecycleError(
      'session_create_pending',
      'session has pending tab creation',
    );
  }
  if (!spartanBrowserLifecycle.hasLease()) {`;
  const deepSnapshotMarker = `    const lifecycle = spartanBrowserLifecycle.snapshot();
    const fallbackDeadlineExceeded`;
  const deepSnapshotReplacement = `    const lifecycle = spartanBrowserLifecycle.snapshot();
    const pendingCreate = spartanPendingUserCreateSnapshot();
    const fallbackDeadlineExceeded`;
  const deepDeadlineMarker = 'if (lifecycle.ownerDeadlineExceeded || fallbackDeadlineExceeded) {';
  const deepDeadlineReplacement = 'if (pendingCreate.deadlineExceeded || lifecycle.ownerDeadlineExceeded || fallbackDeadlineExceeded) {';
  const destroySessionMarker = `function destroySession(userId) {
  const key = normalizeUserId(userId);
  const session = sessions.get(key);
  if (!session) return;
  log('warn', 'destroying dead session', { userId: key });
  sessions.delete(key);
  closeSession(key, session, { reason: 'destroy_session', clearDownloads: true, clearLocks: true }).catch(() => {});
}`;
  const destroySessionReplacement = `function destroySession(userId) {
  const key = normalizeUserId(userId);
  const session = sessions.get(key);
  if (!session) return;
  if (spartanDeferUserSessionDestroy(key, session)) {
    log('warn', 'deferring dead session destroy until active create settles', { userId: key });
    return;
  }
  log('warn', 'destroying dead session', { userId: key });
  sessions.delete(key);
  closeSession(key, session, { reason: 'destroy_session', clearDownloads: true, clearLocks: true }).catch(() => {});
}`;
  const destroySessionPresent = source.includes('function destroySession(userId)');
  if (destroySessionPresent && !source.includes(destroySessionMarker)) {
    throw new Error(
      'unsupported Camofox destroy-session variant; refusing partial queued-create migration',
    );
  }
  const legacyOpenRange = spartanLifecycleRange(
    "app.post('/tabs/open', async (req, res) => {",
    '\n});\n\n// POST /start',
    'legacy tabs open',
  );
  if (!legacyOpenRange.value.includes("spartanBrowserLifecycle.run('tab_open'")) {
    throw new Error('unsupported Camofox legacy tabs-open variant');
  }
  const disabledLegacyOpenRoute = `app.post('/tabs/open', (_req, res) => {
  return res.status(410).json({
    error: 'Deprecated endpoint disabled; use POST /tabs',
    code: 'legacy_tab_open_disabled',
  });`;

  for (const [marker, label] of [
    [coordinatorMarker, 'coordinator registry insertion'],
    [createAdmissionOpen, 'queued create admission open'],
    [createQueueTimeout, 'queued create shared deadline'],
    [createAdmissionClose, 'queued create admission close'],
    [sessionPendingMarker, 'queued create empty-session fence'],
    [createSessionBind, 'queued create session generation bind'],
    [groupPendingMarker, 'queued create empty-group fence'],
    [explicitCloseMarker, 'explicit session close fence'],
    [deepSnapshotMarker, 'queued create health snapshot'],
    [deepDeadlineMarker, 'queued create health deadline'],
  ]) {
    const count = source.split(marker).length - 1;
    if (count !== 1) {
      throw new Error(`expected one Camofox ${label} marker, found ${count}`);
    }
  }

  spartanReplaceLifecycleOnce(coordinatorMarker, queuedCreateRegistry, 'queued create registry');
  spartanReplaceLifecycleOnce(createAdmissionOpen, createAdmissionReplacement, 'queued create admission open');
  spartanReplaceLifecycleOnce(createQueueTimeout, createQueueTimeoutReplacement, 'queued create shared deadline');
  spartanReplaceLifecycleOnce(createAdmissionClose, createAdmissionCloseReplacement, 'queued create admission close');
  spartanReplaceLifecycleOnce(createSessionBind, createSessionBindReplacement, 'queued create session generation bind');
  if (destroySessionPresent) {
    spartanReplaceLifecycleOnce(destroySessionMarker, destroySessionReplacement, 'deferred destroy session');
  }
  spartanReplaceLifecycleRange(legacyOpenRange, disabledLegacyOpenRoute);
  spartanReplaceLifecycleOnce(sessionPendingMarker, sessionPendingReplacement, 'queued create empty-session fence');
  spartanReplaceLifecycleOnce(groupPendingMarker, groupPendingReplacement, 'queued create empty-group fence');
  spartanReplaceLifecycleOnce(explicitCloseMarker, explicitCloseReplacement, 'explicit session close fence');
  spartanReplaceLifecycleOnce(deepSnapshotMarker, deepSnapshotReplacement, 'queued create health snapshot');
  spartanReplaceLifecycleOnce(deepDeadlineMarker, deepDeadlineReplacement, 'queued create health deadline');

  const optionalAutomaticClosers = [
    {
      presence: "reason: 'session_timeout'",
      marker: 'if (now - session.lastAccess > SESSION_TIMEOUT_MS) {',
      replacement: `// Spartan queued-create fence: session timeout.
    if (
      now - session.lastAccess > SESSION_TIMEOUT_MS
      && !spartanHasPendingUserCreate(userId)
    ) {`,
      label: 'session timeout fence',
    },
    {
      presence: "reason: 'memory_pressure'",
      marker: `for (const [key, session] of sessions) {
        if (session._closing) continue;
        if (session.lastAccess < oldestAccess) {`,
      replacement: `// Spartan queued-create fence: memory pressure.
      for (const [key, session] of sessions) {
        if (session._closing || spartanHasPendingUserCreate(key)) continue;
        if (session.lastAccess < oldestAccess) {`,
      label: 'memory pressure fence',
    },
    {
      presence: "reason: 'pressure_cleanup_empty_session'",
      marker: `for (const [userId, session] of Array.from(sessions.entries())) {
      for (const [listItemId, group] of Array.from(session.tabGroups.entries())) {`,
      replacement: `for (const [userId, session] of Array.from(sessions.entries())) {
      // Spartan queued-create fence: pressure cleanup.
      if (spartanHasPendingUserCreate(userId)) continue;
      for (const [listItemId, group] of Array.from(session.tabGroups.entries())) {`,
      label: 'pressure cleanup iteration fence',
    },
    {
      presence: "reason: 'pressure_cleanup_empty_session'",
      marker: 'if (closeEmptySessions && session.tabGroups.size === 0) {',
      replacement: `if (
        closeEmptySessions
        && session.tabGroups.size === 0
        && !spartanHasPendingUserCreate(userId)
      ) {`,
      label: 'pressure cleanup close fence',
    },
  ];
  for (const item of optionalAutomaticClosers) {
    if (!source.includes(item.presence)) continue;
    const count = source.split(item.marker).length - 1;
    if (count !== 1) {
      throw new Error(`expected one Camofox ${item.label} marker, found ${count}`);
    }
    spartanReplaceLifecycleOnce(item.marker, item.replacement, item.label);
  }

  spartanReplaceLifecycleOnce(
    spartanLifecycleRevisionMarker,
    `${spartanLifecycleRevisionMarker}
${spartanLifecycleQueuedCreateMarker}`,
    'queued create revision marker',
  );
} else {
  for (const [marker, label] of [
    ['const SPARTAN_PENDING_USER_CREATE_LIMIT = MAX_SESSIONS + 1;', 'bounded queued create registry'],
    ['async function spartanWithPendingUserCreate(', 'queued create scope'],
    ['const spartanCreateTimeoutMs = requestTimeoutMs();', 'queued create effective timeout'],
    ['spartanCreateDeadlineAt - Date.now() - 1000', 'queued create shared deadline'],
    ['spartanCreateDeadlineAt), spartanCreateTimeoutMs', 'queued create outer deadline'],
    ['now >= token.deadlineAt', 'per-token queued create deadline'],
    ['function spartanDeferUserSessionDestroy(', 'deferred session destroy'],
    ['const spartanSessionCreatePending = spartanHasPendingUserCreate(userId)', 'queued create empty-session fence'],
    ['spartanBindPendingCreateSession(userId, spartanCreateFence, session)', 'queued create session generation bind'],
    ['const spartanGroupCreatePending = spartanHasPendingUserCreate(userId)', 'queued create empty-group fence'],
    ["reason === 'api_delete_session'", 'explicit session close fence'],
    ['const pendingCreate = spartanPendingUserCreateSnapshot();', 'queued create health snapshot'],
    ['if (pendingCreate.deadlineExceeded || lifecycle.ownerDeadlineExceeded', 'queued create health deadline'],
    ["code: 'legacy_tab_open_disabled'", 'disabled legacy tabs open'],
  ]) {
    if (!source.includes(marker)) {
      throw new Error(`Camofox lifecycle revision 3 marker is incomplete: ${label}`);
    }
  }
  if (
    source.includes('function destroySession(userId)')
    && !source.includes('spartanDeferUserSessionDestroy(key, session)')
  ) {
    throw new Error(
      'Camofox lifecycle revision 3 marker is incomplete: deferred destroy session',
    );
  }
  for (const [presence, guard, label] of [
    ["reason: 'session_timeout'", '// Spartan queued-create fence: session timeout.', 'session timeout fence'],
    ["reason: 'memory_pressure'", '// Spartan queued-create fence: memory pressure.', 'memory pressure fence'],
    ["reason: 'pressure_cleanup_empty_session'", '// Spartan queued-create fence: pressure cleanup.', 'pressure cleanup iteration fence'],
    ["reason: 'pressure_cleanup_empty_session'", '&& !spartanHasPendingUserCreate(userId)', 'pressure cleanup close fence'],
  ]) {
    if (source.includes(presence) && !source.includes(guard)) {
      throw new Error(`Camofox lifecycle revision 3 marker is incomplete: ${label}`);
    }
  }
}

const spartanReadinessRevisionCount = source.split(spartanReadinessRevisionMarker).length - 1;
if (spartanReadinessRevisionCount > 1) {
  throw new Error('duplicate Camofox readiness revision marker');
}
if (spartanReadinessRevisionCount === 0) {
  for (const [marker, label] of [
    [spartanLifecycleVersionMarker, 'lifecycle version prerequisite'],
    [spartanLifecycleRevisionMarker, 'lifecycle revision prerequisite'],
    [spartanLifecycleQueuedCreateMarker, 'queued-create prerequisite'],
    ['const spartanGuardedBrowsers = new WeakSet();', 'runtime insertion'],
    ["  recoverBrowser: () => restartBrowser('spartan_deep_health'),\n  runExclusive: operation => {", 'deep generation'],
    ['  runExclusive: operation => {\n    const lifecycle = spartanBrowserLifecycle.snapshot();', 'deep navigation latch'],
    ['  busyMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,\n  recoveryTimeoutMs:', 'deep cache TTL'],
    ['  const result = await spartanDeepHealth.probe();\n  res.status(result.ok ? 200 : 503).json(result);', 'deep public payload'],
  ]) {
    const count = source.split(marker).length - 1;
    if (count !== 1) {
      throw new Error(`expected one Camofox readiness ${label} marker, found ${count}`);
    }
  }
  const optionalReadinessMarkers = [
    ['  const action = actionFromReq(req);\n  failuresTotal.labels(failureType, action).inc();', 'public failure handler'],
    ['if (FLY_MACHINE_ID && err.statusCode === 503)', 'Fly capacity replay'],
  ];
  for (const [marker, label] of optionalReadinessMarkers) {
    const count = source.split(marker).length - 1;
    if (count > 1) {
      throw new Error(`duplicate Camofox readiness ${label} marker`);
    }
  }
  if (!source.includes(spartanHealthImport)) {
    throw new Error('Camofox readiness requires the navigation health import');
  }
  if (!source.includes('if (err.failureClass && !body.failureClass) body.failureClass = err.failureClass;')) {
    throw new Error('Camofox readiness requires classified error responses');
  }

  const publishVariants = [
    [
      'browser = candidateBrowser; // publish AFTER PID is captured',
      'spartanPublishBrowser(candidateBrowser); // publish AFTER PID is captured',
    ],
    ['browser = value;', 'spartanPublishBrowser(value);'],
  ];
  const publishMatches = publishVariants.filter(([marker]) => source.includes(marker));
  if (publishMatches.length !== 1) {
    throw new Error(`expected one Camofox readiness browser publication variant, found ${publishMatches.length}`);
  }

  const healthRoute = spartanReadinessRoute("app.get('/health',", 'public health');
  const createRoute = spartanReadinessRoute("app.post('/tabs',", 'create-tab');
  const navigateRoute = spartanReadinessRoute("app.post('/tabs/:tabId/navigate',", 'navigate');

  let patchedCreateRoute = spartanReplaceReadinessOnce(
    createRoute.value,
    `    userKey: null,
  };
  try {`,
    `    userKey: null,
  };
  let spartanCreateNavigation = null;
  try {`,
    'create outcome declaration',
  );
  patchedCreateRoute = spartanReplaceReadinessVariant(
    patchedCreateRoute,
    [
      [
        `        tabState.lastRequestedUrl = url;
        try {`,
        `        tabState.lastRequestedUrl = url;
        spartanCreateNavigation = spartanNavigationReadiness.begin();
        try {`,
      ],
      [
        `        if (urlErr) throw new Error(urlErr);
        await page.goto(url);`,
        `        if (urlErr) throw new Error(urlErr);
        spartanCreateNavigation = spartanNavigationReadiness.begin();
        await page.goto(url);`,
      ],
    ],
    'create outcome begin',
  );

  const createGotoVariants = [
    [
      "await withPageLoadDuration('open_url', () => page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }));",
      "await spartanObserveNavigationAttempt(() => withPageLoadDuration('open_url', () => page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 })));",
    ],
    [
      "await withPageLoadDuration('open_url', () => retryPage.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }));",
      "await spartanObserveNavigationAttempt(() => withPageLoadDuration('open_url', () => retryPage.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 })));",
    ],
    ['await page.goto(url);', 'await spartanObserveNavigationAttempt(() => page.goto(url));'],
  ];
  let createGotoCount = 0;
  for (const [marker, replacement] of createGotoVariants) {
    const count = patchedCreateRoute.split(marker).length - 1;
    if (count > 1) throw new Error('duplicate Camofox readiness create goto marker');
    if (count === 1) {
      patchedCreateRoute = patchedCreateRoute.replace(marker, replacement);
      createGotoCount++;
    }
  }
  if (createGotoCount < 1) throw new Error('expected Camofox readiness create goto marker');

  patchedCreateRoute = spartanReplaceReadinessVariant(
    patchedCreateRoute,
    [
      [
        '        tabState.visitedUrls.add(url);',
        '        spartanCreateNavigation?.success();\n        tabState.visitedUrls.add(url);',
      ],
      [
        '        await spartanObserveNavigationAttempt(() => page.goto(url));\n      }',
        '        await spartanObserveNavigationAttempt(() => page.goto(url));\n        spartanCreateNavigation?.success();\n      }',
      ],
    ],
    'create outcome success',
  );

  const createSslMarker = `    if (isSslError) {
      return res.status(502).json({`;
  if (patchedCreateRoute.includes(createSslMarker)) {
    patchedCreateRoute = spartanReplaceReadinessOnce(
      patchedCreateRoute,
      createSslMarker,
      `    if (isSslError) {
      spartanSettleNavigationFailure(spartanCreateNavigation, err);
      return res.status(502).json({`,
      'create SSL settlement',
    );
  }
  const createFlyMarker = "    if (FLY_MACHINE_ID && err.statusCode === 503) {\n      res.set('fly-replay',";
  if (patchedCreateRoute.includes(createFlyMarker)) {
    patchedCreateRoute = spartanReplaceReadinessOnce(
      patchedCreateRoute,
      createFlyMarker,
      "    if (FLY_MACHINE_ID && err.statusCode === 503) {\n"
        + "      spartanSettleNavigationFailure(spartanCreateNavigation, err);\n"
        + "      res.set('fly-replay',",
      'create Fly settlement',
    );
  }
  patchedCreateRoute = spartanReplaceReadinessOnce(
    patchedCreateRoute,
    '    handleRouteError(err, req, res);',
    `    spartanApplyPublicFailure(err);
    spartanSettleNavigationFailure(spartanCreateNavigation, err);
    handleRouteError(err, req, res);`,
    'create failure settlement',
  );

  let patchedNavigateRoute = spartanReplaceReadinessOnce(
    navigateRoute.value,
    '  const tabId = req.params.tabId;',
    `  const tabId = req.params.tabId;
  let spartanNavigateOutcome = null;`,
    'navigate outcome declaration',
  );
  patchedNavigateRoute = spartanReplaceReadinessOnce(
    patchedNavigateRoute,
    'if (urlErr) throw new Error(urlErr);',
    `if (urlErr) throw new Error(urlErr);
      spartanNavigateOutcome = spartanNavigationReadiness.begin();`,
    'navigate outcome begin',
  );
  const navigateGotoMarker = 'await navigateCurrentPage();';
  const navigateGotoCount = patchedNavigateRoute.split(navigateGotoMarker).length - 1;
  if (navigateGotoCount < 1) throw new Error('expected Camofox readiness terminal navigate call');
  patchedNavigateRoute = patchedNavigateRoute.replaceAll(
    navigateGotoMarker,
    'await spartanObserveNavigationAttempt(() => navigateCurrentPage());',
  );
  patchedNavigateRoute = spartanReplaceReadinessVariant(
    patchedNavigateRoute,
    [
      [
        '\n        // For Google SERP: skip eager ref building during navigate.',
        '\n        spartanNavigateOutcome?.success();\n\n        // For Google SERP: skip eager ref building during navigate.',
      ],
      [
        '\n    // For Google SERP: skip eager ref building during navigate.',
        '\n    spartanNavigateOutcome?.success();\n    // For Google SERP: skip eager ref building during navigate.',
      ],
    ],
    'navigate outcome success',
  );
  const navigate400Marker = `    if (is400) {
      return res.status(400).json({ error: safeError(err) });`;
  if (patchedNavigateRoute.includes(navigate400Marker)) {
    patchedNavigateRoute = spartanReplaceReadinessOnce(
      patchedNavigateRoute,
      navigate400Marker,
      `    if (is400) {
      spartanSettleNavigationFailure(spartanNavigateOutcome, err);
      return res.status(400).json({ error: safeError(err) });`,
      'navigate validation settlement',
    );
  }
  const navigateSslMarker = `    if (isSslError) {
      return res.status(502).json({`;
  if (patchedNavigateRoute.includes(navigateSslMarker)) {
    patchedNavigateRoute = spartanReplaceReadinessOnce(
      patchedNavigateRoute,
      navigateSslMarker,
      `    if (isSslError) {
      spartanSettleNavigationFailure(spartanNavigateOutcome, err);
      return res.status(502).json({`,
      'navigate SSL settlement',
    );
  }
  patchedNavigateRoute = spartanReplaceReadinessOnce(
    patchedNavigateRoute,
    '    handleRouteError(err, req, res);',
    `    spartanApplyPublicFailure(err);
    spartanSettleNavigationFailure(spartanNavigateOutcome, err);
    handleRouteError(err, req, res);`,
    'navigate failure settlement',
  );

  source = spartanReplaceReadinessOnce(source, 'const spartanGuardedBrowsers = new WeakSet();', `${spartanReadinessRuntimeBlock}
const spartanGuardedBrowsers = new WeakSet();`, 'runtime insertion');
  source = spartanReplaceReadinessOnce(source, publishMatches[0][0], publishMatches[0][1], 'browser publication');
  source = spartanReplaceReadinessOnce(source, "  recoverBrowser: () => restartBrowser('spartan_deep_health'),\n  runExclusive: operation => {", "  recoverBrowser: () => restartBrowser('spartan_deep_health'),\n  getGeneration: () => spartanBrowserGeneration,\n  runExclusive: operation => {", 'deep generation');
  source = spartanReplaceReadinessOnce(
    source,
    '  runExclusive: operation => {\n    const lifecycle = spartanBrowserLifecycle.snapshot();',
    `  runExclusive: operation => {
    const navigation = spartanNavigationReadiness.snapshot();
    if (navigation.latched) {
      const error = spartanBrowserLifecycleError(
        'browser_navigation_degraded',
        'browser navigation readiness requires recovery',
      );
      error.stage = 'lifecycle';
      throw error;
    }
    const lifecycle = spartanBrowserLifecycle.snapshot();`,
    'deep navigation latch',
  );
  source = spartanReplaceReadinessOnce(source, '  busyMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,\n  recoveryTimeoutMs:', '  busyMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,\n  cacheMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,\n  recoveryTimeoutMs:', 'deep cache TTL');
  const publicFailureHandlerMarker = '  const action = actionFromReq(req);\n  failuresTotal.labels(failureType, action).inc();';
  if (source.includes(publicFailureHandlerMarker)) {
    source = spartanReplaceReadinessOnce(
      source,
      publicFailureHandlerMarker,
      '  const action = actionFromReq(req);\n  spartanApplyPublicFailure(err);\n  failuresTotal.labels(failureType, action).inc();',
      'public failure handler',
    );
  }
  source = spartanReplaceReadinessOnce(source, createRoute.value, patchedCreateRoute, 'create route');
  source = spartanReplaceReadinessOnce(source, navigateRoute.value, patchedNavigateRoute, 'navigate route');
  source = spartanReplaceReadinessOnce(source, '  const result = await spartanDeepHealth.probe();\n  res.status(result.ok ? 200 : 503).json(result);', '  const result = await spartanDeepHealth.probe();\n  const payload = spartanDeepHealthPayload(result);\n  res.status(payload.ok ? 200 : 503).json(payload);', 'deep public payload');
  if (source.includes('if (FLY_MACHINE_ID && err.statusCode === 503)')) {
    source = spartanReplaceReadinessOnce(
      source,
      'if (FLY_MACHINE_ID && err.statusCode === 503)',
      'if (FLY_MACHINE_ID && spartanIsCapacityFailure(err))',
      'Fly capacity replay',
    );
  }
  source = spartanReplaceReadinessOnce(source, healthRoute.value, spartanReadinessHealthRoutes, 'public health route');
  source = spartanReplaceReadinessOnce(source, spartanLifecycleQueuedCreateMarker, `${spartanLifecycleQueuedCreateMarker}
${spartanReadinessRevisionMarker}`, 'revision marker');
} else {
  for (const [marker, label] of [
    [spartanHealthImport, 'navigation health import'],
    ['let spartanBrowserGeneration = 0;', 'browser generation'],
    ['function spartanPublishBrowser(', 'browser publication helper'],
    ['spartanBrowserGeneration++;', 'browser generation increment'],
    ['const spartanNavigationReadiness = createSpartanNavigationReadiness({', 'navigation readiness'],
    ['spartanCreateNavigation = spartanNavigationReadiness.begin();', 'create outcome'],
    ['spartanNavigateOutcome = spartanNavigationReadiness.begin();', 'navigate outcome'],
    ['spartanMarkNavigationAttemptError(navErr);', 'terminal goto tagging'],
    ['spartanSettleNavigationFailure(spartanCreateNavigation, err);', 'create settlement'],
    ['spartanSettleNavigationFailure(spartanNavigateOutcome, err);', 'navigate settlement'],
    ['getGeneration: () => spartanBrowserGeneration,', 'deep generation'],
    ['cacheMaxAgeMs: SPARTAN_DEEP_BUSY_MAX_AGE_MS,', 'deep cache TTL'],
    ["app.get('/live'", 'browser-independent liveness'],
    ['const readiness = spartanPublicReadinessSnapshot();', 'passive public readiness'],
    ['res.status(readiness.ok ? 200 : 503)', 'fail-closed public readiness'],
    ['const payload = spartanDeepHealthPayload(result);', 'allowlisted deep payload'],
    ['return { ok, code, stage, browserGeneration };', 'deep browser generation'],
    ["failureClass: 'capacity_limit'", 'capacity classification'],
    ["failureClass: 'internal_timeout'", 'timeout classification'],
    ["failureClass: 'browser_state'", 'browser-state classification'],
  ]) {
    if (!source.includes(marker)) {
      throw new Error(`Camofox readiness revision 2 marker is incomplete: ${label}`);
    }
  }
  if (source.includes('browser = candidateBrowser; // publish AFTER PID is captured') || source.includes('browser = value;') || source.includes("app.get('/health', (_req, res) => res.json({ ok: true }))")) {
    throw new Error('Camofox readiness revision 2 marker retains a legacy implementation');
  }
}

const upstreamActiveHealthStart = '// Active health probe -- detect hung browser even when isConnected() lies\n';
const upstreamActiveHealthEnd = '\n// Crash logging\n';
const upstreamActiveHealthIndex = source.indexOf(upstreamActiveHealthStart);
if (upstreamActiveHealthIndex !== -1) {
  const end = source.indexOf(upstreamActiveHealthEnd, upstreamActiveHealthIndex);
  if (end === -1) throw new Error('expected Camofox active health probe end marker not found');
  source = source.slice(0, upstreamActiveHealthIndex) + source.slice(end + 1);
}

fs.writeFileSync(path, source);
