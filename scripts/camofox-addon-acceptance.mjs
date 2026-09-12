import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const base = process.env.CAMOFOX_ADDON_ACCEPTANCE_BASE || 'http://127.0.0.1:9377';
const accessKey = process.env.CAMOFOX_ACCESS_KEY || '';
const targetUrl = process.env.CAMOFOX_ADDON_ACCEPTANCE_URL || 'https://example.com/';
const totalTimeoutMs = Number(process.env.CAMOFOX_ADDON_ACCEPTANCE_TIMEOUT_MS || 60000);
const configuredMode = process.env.CAMOFOX_DISABLE_DEFAULT_ADDONS || '';
const analyticsMode = configuredMode === 'true' || configuredMode === '1';
const customAddonConfigured = Boolean((process.env.CAMOFOX_ADDONS || '').trim());

if (!accessKey || /[\r\n]/.test(accessKey)) throw new Error('valid CAMOFOX_ACCESS_KEY is required');
if (!Number.isInteger(totalTimeoutMs) || totalTimeoutMs < 20000 || totalTimeoutMs > 120000) {
  throw new Error('addon acceptance timeout must be between 20000 and 120000 milliseconds');
}

const deadlineAt = Date.now() + totalTimeoutMs;
const operationDeadlineAt = deadlineAt - 5000;
const userId = `spartan-addon-accept-${crypto.randomUUID()}`;
const sessionKey = `addon-accept-${crypto.randomUUID()}`;
let tabId = null;

function endpointLabel(path) {
  return path.replace(/^\/tabs\/[^/]+/, '/tabs/:tabId')
    .replace(/^\/sessions\/[^/]+/, '/sessions/:userId');
}

async function request(path, options = {}, requestDeadlineAt = operationDeadlineAt) {
  const remainingMs = requestDeadlineAt - Date.now();
  if (remainingMs <= 0) throw new Error(`Camofox addon acceptance deadline exceeded before ${endpointLabel(path)}`);
  const headers = { ...(options.headers || {}), Authorization: `Bearer ${accessKey}` };
  if (options.body) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      ...options,
      headers,
      signal: AbortSignal.timeout(remainingMs),
    });
  } catch (error) {
    const wrapped = new Error(`Camofox addon acceptance ${options.method || 'GET'} ${endpointLabel(path)} timed out or disconnected`);
    wrapped.cause = error;
    throw wrapped;
  }
  if (!response.ok) {
    throw new Error(`Camofox addon acceptance ${options.method || 'GET'} ${endpointLabel(path)} HTTP ${response.status}`);
  }
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function evaluate(expression) {
  const response = await request(`/tabs/${encodeURIComponent(tabId)}/evaluate`, {
    method: 'POST',
    body: JSON.stringify({ userId, expression }),
  });
  return response?.result;
}

const trackerUrls = [
  'https://www.google-analytics.com/__spartan_refresh_probe__',
  'https://www.googletagmanager.com/__spartan_refresh_probe__',
  'https://www.facebook.com/tr/',
];
// These intentionally contain no analytics event IDs or event query parameters.
// The inline script is essential: debugger-invoked fetch from /evaluate can bypass
// WebExtension webRequest interception in Camoufox 152, while page-world loads do not.
const resultAttribute = `data-spartan-addon-probe-${crypto.randomUUID()}`;
const pageWorldProbe = `(async () => {
  const results = await Promise.all(${JSON.stringify(trackerUrls)}.map(async url => {
    try {
      await fetch(url, { mode: 'no-cors', cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(12000) });
      return true;
    } catch {
      return false;
    }
  }));
  document.documentElement.setAttribute(${JSON.stringify(resultAttribute)}, JSON.stringify(results));
})()`;
const injectionExpression = `(async () => {
  const attribute = ${JSON.stringify(resultAttribute)};
  const script = document.createElement('script');
  document.documentElement.removeAttribute(attribute);
  script.textContent = ${JSON.stringify(pageWorldProbe)};
  (document.head || document.documentElement).appendChild(script);
  try {
    for (let attempt = 0; attempt < 140; attempt++) {
      const value = document.documentElement.getAttribute(attribute);
      if (value) return JSON.parse(value);
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('page-world addon probe timed out');
  } finally {
    script.remove();
    document.documentElement.removeAttribute(attribute);
  }
})()`;

let primaryError = null;
let cleanupError = null;
let summary = null;
try {
  const created = await request('/tabs', {
    method: 'POST',
    body: JSON.stringify({ userId, sessionKey, url: targetUrl, nonDestructive: true }),
  });
  tabId = created?.tabId;
  if (typeof tabId !== 'string' || !tabId) throw new Error('Camofox addon acceptance create omitted tabId');

  const reachable = await evaluate(injectionExpression);
  const expected = trackerUrls.map(() => analyticsMode);
  assert.deepEqual(reachable, expected, analyticsMode
    ? 'analytics mode must let inert tracker probes reach the existing proxy policy'
    : 'protected mode must block every inert tracker probe in page-world');

  if (customAddonConfigured) {
    const marker = await evaluate('document.documentElement.getAttribute("data-spartan-refresh-addon")');
    assert.equal(marker, 'loaded', 'configured custom addon marker must be active');
  }

  summary = {
    ok: true,
    mode: analyticsMode ? 'analytics' : 'protected',
    reachable,
    customAddon: customAddonConfigured ? 'loaded' : 'not-configured',
  };
} catch (error) {
  primaryError = error;
} finally {
  try {
    await request(`/sessions/${encodeURIComponent(userId)}/storage_state`, {
      method: 'DELETE',
    }, deadlineAt);
    tabId = null;
  } catch (error) {
    cleanupError = error;
    if (tabId && Date.now() < deadlineAt) {
      await request(`/tabs/${encodeURIComponent(tabId)}`, {
        method: 'DELETE',
        body: JSON.stringify({ userId }),
      }, deadlineAt).catch(() => {});
    }
  }
}

if (primaryError || cleanupError) {
  const messages = [];
  if (primaryError) messages.push(primaryError?.message || 'addon acceptance failed');
  if (cleanupError) messages.push(`cleanup failed: ${cleanupError?.message || 'unknown error'}`);
  process.stderr.write(`${messages.join('; ')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(JSON.stringify(summary) + '\n');
}
