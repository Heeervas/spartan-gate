'use strict';

const PROFILE_RE = /^[A-Za-z0-9._-]+$/;
const DEFAULT_BROWSERLESS_WS_BASE = 'ws://browserless:3000/chromium';
const DEFAULT_BROWSERLESS_LANG = 'es-ES';
const DEFAULT_BROWSERLESS_ROUTE = 'chromium';
const ROUTE_VALUES = 'chromium, chrome, stealth, chromium-stealth, or chrome-stealth';

function normalizeBool(value, defaultValue) {
  if (value === undefined || value === null || value === '') return defaultValue;
  return !['0', 'false', 'no', 'off'].includes(String(value).trim().toLowerCase());
}

function normalizeProfile(profile) {
  const value = String(profile || 'main').trim();
  if (!PROFILE_RE.test(value) || value === '.' || value === '..') {
    throw new Error(`Invalid Browserless profile name: ${value}`);
  }
  return value;
}

function normalizeRoot(root) {
  const value = String(root || '/profiles').replace(/\/+$/, '');
  if (!value.startsWith('/')) {
    throw new Error(`BROWSERLESS_PROFILE_ROOT must be an absolute container path: ${value}`);
  }
  return value;
}

function buildLaunch(env = {}, profile = null) {
  const width = String(env.BROWSERLESS_SCREEN_WIDTH || env.SCREEN_WIDTH || '1280');
  const height = String(env.BROWSERLESS_SCREEN_HEIGHT || env.SCREEN_HEIGHT || '720');
  const lang = String(env.BROWSERLESS_LANG || DEFAULT_BROWSERLESS_LANG).trim() || DEFAULT_BROWSERLESS_LANG;
  const subnet = env.SPARTAN_INTERNAL_SUBNET || '172.28.0.0/24';
  const proxyServer = env.BROWSERLESS_PROXY_SERVER || 'http://proxy:8888';
  const proxyBypass = env.BROWSERLESS_PROXY_BYPASS_LIST || `<local>;127.0.0.1;${subnet}`;

  const launch = {
    args: [
      `--window-size=${width},${height}`,
      `--lang=${lang}`,
      `--proxy-server=${proxyServer}`,
      `--proxy-bypass-list=${proxyBypass}`,
      '--disable-crash-reporter',
      '--no-crashpad',
    ],
  };

  if (profile) {
    const profileRoot = normalizeRoot(env.BROWSERLESS_PROFILE_ROOT);
    launch.userDataDir = `${profileRoot}/${normalizeProfile(profile)}`;
  }

  if (normalizeBool(env.BROWSERLESS_IGNORE_DEFAULT_ARGS, false)) {
    launch.ignoreDefaultArgs = true;
  }

  return launch;
}

function normalizeRoute(env = {}) {
  return String(
    env.BROWSERLESS_ROUTE
      || env.BROWSERLESS_STEALTH_ROUTE
      || DEFAULT_BROWSERLESS_ROUTE,
  ).trim().toLowerCase();
}

function stealthRoutePath(env = {}) {
  const route = normalizeRoute(env);
  if (route === 'chromium') return '/chromium';
  if (route === 'chrome') return '/chrome';
  if (route === 'stealth' || route === 'root') return '/stealth';
  if (route === 'chromium-stealth' || route === 'chromium/stealth') return '/chromium/stealth';
  if (route === 'chrome-stealth' || route === 'chrome/stealth') return '/chrome/stealth';
  throw new Error(`Invalid BROWSERLESS_ROUTE: ${route}. Use ${ROUTE_VALUES}.`);
}

function applyStealthEndpoint(url, env = {}) {
  const useStealthEndpoint = normalizeBool(
    env.BROWSERLESS_STEALTH_ENDPOINT,
    normalizeBool(env.BROWSERLESS_STEALTH, true),
  );
  if (!useStealthEndpoint) return;

  url.pathname = stealthRoutePath(env);
}

function buildBrowserlessCdpUrl(env = process.env, options = {}) {
  const profile = options.profile ? normalizeProfile(options.profile) : null;
  const base = options.base || env.BROWSERLESS_WS_BASE || DEFAULT_BROWSERLESS_WS_BASE;
  const url = new URL(base);
  const token = options.token || env.BROWSERLESS_TOKEN;

  applyStealthEndpoint(url, env);

  if (token && !url.searchParams.has('token')) {
    url.searchParams.set('token', token);
  }

  if (!url.searchParams.has('headless')) {
    url.searchParams.set('headless', String(normalizeBool(env.BROWSERLESS_HEADLESS, true)));
  }

  if (!url.searchParams.has('stealth')) {
    url.searchParams.set('stealth', String(normalizeBool(env.BROWSERLESS_STEALTH, true)));
  }

  const userAgent = String(env.BROWSERLESS_USER_AGENT || '').trim();
  if (userAgent && !url.searchParams.has('userAgent')) {
    url.searchParams.set('userAgent', userAgent);
  }

  url.searchParams.set('launch', JSON.stringify(buildLaunch(env, profile)));
  return url.toString();
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--profile') {
      index += 1;
      if (index >= argv.length) throw new Error('--profile requires a value');
      options.profile = argv[index];
    } else if (arg.startsWith('--profile=')) {
      options.profile = arg.slice('--profile='.length);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return options;
}

if (require.main === module) {
  try {
    process.stdout.write(`${buildBrowserlessCdpUrl(process.env, parseArgs(process.argv.slice(2)))}\n`);
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}

module.exports = {
  applyStealthEndpoint,
  buildBrowserlessCdpUrl,
  buildLaunch,
  normalizeRoute,
  normalizeProfile,
  stealthRoutePath,
};
