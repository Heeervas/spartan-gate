'use strict';

const { buildLaunch, normalizeProfile, normalizeRoute } = require('./browserless-cdp-url');

function normalizeBool(value, defaultValue) {
  if (value === undefined || value === null || value === '') return defaultValue;
  return !['0', 'false', 'no', 'off'].includes(String(value).trim().toLowerCase());
}

function parseIntEnv(name, defaultValue) {
  const raw = process.env[name];
  if (!raw) return defaultValue;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : defaultValue;
}

function assertHttpUrl(url) {
  if (!url) return null;
  const parsed = new URL(url);
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error(`Initial URL must be http or https: ${url}`);
  }
  return parsed.toString();
}

function buildNavigationCode(initialUrl) {
  if (!initialUrl) {
    return `export default async ({ page }: { page: Page }) => {\n  await page.goto('about:blank');\n};`;
  }

  return `export default async ({ page }: { page: Page }) => {\n  await page.goto(${JSON.stringify(initialUrl)}, { waitUntil: 'domcontentloaded' });\n};`;
}

function isGoogleLoginUrl(initialUrl) {
  if (!initialUrl) return false;
  try {
    return new URL(initialUrl).hostname === 'accounts.google.com';
  } catch {
    return false;
  }
}

function buildDebuggerUrl(env, profile, initialUrl, originOverride) {
  const origin = originOverride || env.BROWSERLESS_DEBUG_ORIGIN || `http://localhost:${env.BROWSERLESS_DEBUG_PORT || '3005'}`;
  const url = new URL('/debugger/', origin);
  const launch = buildLaunch(env, profile);
  const autostart = env.BROWSERLESS_DEBUG_AUTOSTART || (isGoogleLoginUrl(initialUrl) ? 'false' : 'true');

  url.searchParams.set('sgProfile', profile);
  url.searchParams.set('sgFlags', launch.args.join('\n'));
  url.searchParams.set('sgUserDataDir', launch.userDataDir);
  url.searchParams.set('sgHeadless', String(normalizeBool(env.BROWSERLESS_HEADLESS, true)));
  url.searchParams.set('sgStealthEndpoint', String(normalizeBool(env.BROWSERLESS_STEALTH_ENDPOINT, true)));
  url.searchParams.set('sgRoute', normalizeRoute(env));
  url.searchParams.set('sgStealthRoute', normalizeRoute(env));
  if (env.BROWSERLESS_USER_AGENT) {
    url.searchParams.set('sgUserAgent', env.BROWSERLESS_USER_AGENT);
  }
  if (launch.ignoreDefaultArgs === true) {
    url.searchParams.set('sgIgnoreDefaultArgs', 'true');
  }
  url.searchParams.set('sgCode', buildNavigationCode(initialUrl));
  url.searchParams.set('sgQuality', String(parseIntEnv('BROWSERLESS_LIVE_QUALITY', 90)));
  url.searchParams.set('sgAutostart', autostart);

  return url.toString();
}

function buildDebuggerOrigins(env) {
  const configured = String(env.BROWSERLESS_DEBUG_ORIGINS || '').trim();
  if (configured) {
    return configured.split('\n').filter(Boolean).map((entry) => {
      const separator = entry.indexOf('|');
      return separator === -1
        ? { label: '', origin: entry }
        : { label: entry.slice(0, separator), origin: entry.slice(separator + 1) };
    });
  }

  return [{
    label: '',
    origin: env.BROWSERLESS_DEBUG_ORIGIN || `http://localhost:${env.BROWSERLESS_DEBUG_PORT || '3005'}`,
  }];
}

function main() {
  const profile = normalizeProfile(process.argv[2] || process.env.BROWSERLESS_PROFILE || 'main');
  const initialUrl = assertHttpUrl(process.argv[3] || '');

  console.log(`Browserless profile: ${profile}`);
  for (const { label, origin } of buildDebuggerOrigins(process.env)) {
    const suffix = label ? ` (${label})` : '';
    console.log(`Debugger URL${suffix}: ${buildDebuggerUrl(process.env, profile, initialUrl, origin)}`);
  }
  console.log('Open it in your browser. If the stream does not start automatically, click the run button.');
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(error.message || String(error));
    process.exit(1);
  }
}

module.exports = {
  buildDebuggerOrigins,
  buildDebuggerUrl,
  isGoogleLoginUrl,
};
