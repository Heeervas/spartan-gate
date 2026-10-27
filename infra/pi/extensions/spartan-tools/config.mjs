import { constants, openSync, closeSync, fstatSync, readFileSync, realpathSync } from 'node:fs';
import { isAbsolute, resolve, join } from 'node:path';

export const TOKEN = /^[0-9a-f]{64}$/;
export const FAILURE = 'Managed tools unavailable: configuration or service rejected the request.';

// No symlinks (including ancestors), shared files, or non-owner credential inputs.
export function privateFile(path, limit = 16384) {
  if (!isAbsolute(path) || realpathSync(path) !== resolve(path)) throw new Error(FAILURE);
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o7777) !== 0o600 || stat.nlink !== 1 || stat.size > limit) throw new Error(FAILURE);
    const data = readFileSync(fd);
    if (data.length > limit) throw new Error(FAILURE);
    return data.toString('utf8');
  } finally { closeSync(fd); }
}

export function endpoint(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname)) throw new Error(FAILURE);
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error(FAILURE);
  return url.origin;
}

export function loadConfig(path) {
  const config = JSON.parse(privateFile(path));
  const keys = ['version', 'baseUrl', 'credentialFile', 'stateDir', 'clientId'];
  if (Object.keys(config).length !== keys.length || keys.some(key => !Object.hasOwn(config, key)) || config.version !== 1) throw new Error(FAILURE);
  if (typeof config.clientId !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(config.clientId)) throw new Error(FAILURE);
  if (typeof config.stateDir !== 'string' || !isAbsolute(config.stateDir)) throw new Error(FAILURE);
  const token = privateFile(config.credentialFile, 65).trim();
  if (!TOKEN.test(token)) throw new Error(FAILURE);
  const baseUrl = endpoint(config.baseUrl);
  if (process.env.SEARXNG_BASE_URL !== undefined && endpoint(process.env.SEARXNG_BASE_URL) !== baseUrl) throw new Error(FAILURE);
  return { ...config, baseUrl, token };
}

export function checkSearchConfig(config, agentDir) {
  const web = JSON.parse(privateFile(join(agentDir, 'web-search.json')));
  if ((web.searchProvider ?? web.provider) !== 'searxng' || web.workflow !== 'none' || endpoint(web.searxngBaseUrl) !== config.baseUrl) throw new Error(FAILURE);
  if (JSON.stringify(web.webSearch?.allowedProviders) !== '["searxng"]') throw new Error(FAILURE);
  checkDisabledFeatures(web);
  checkSearchHeaders(web.searxngHeaders, config.token);
}

function checkSearchHeaders(value, token) {
  const headers = Object.entries(value ?? {});
  if (headers.length !== 1 || headers[0][0].toLowerCase() !== 'authorization' || headers[0][1] !== `Bearer ${token}`) throw new Error(FAILURE);
}

function checkDisabledFeatures(web) {
  const tools = web.tools ?? {};
  const commands = web.commands ?? {};
  const ssrf = web.ssrf ?? {};
  const names = web.toolNames ?? {};
  const disabled = [tools.sourceCheck, tools.fetchContent, commands.websearch,
    commands.curator, commands.search, commands['google-account']];
  if (disabled.some(option => option?.enabled !== false)) throw new Error(FAILURE);
  if (web.proxy || ssrf.trustEnvProxy || ssrf.allowRanges?.length) throw new Error(FAILURE);
  if (names.webSearch && names.webSearch !== 'web_search') throw new Error(FAILURE);
}

export function searchArguments(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error(FAILURE);
  const provider = input.provider;
  const allowed = Array.isArray(provider)
    ? provider.length === 1 && provider[0] === 'searxng'
    : [undefined, 'auto', 'searxng'].includes(provider);
  if (!allowed) throw new Error(FAILURE);
  if (input.includeContent !== undefined && input.includeContent !== false) throw new Error(FAILURE);
  if (input.workflow !== undefined && input.workflow !== 'none') throw new Error(FAILURE);
  if ((Object.hasOwn(input, 'proxy') && input.proxy !== '') || Buffer.byteLength(JSON.stringify(input)) > 16384) throw new Error(FAILURE);
  normalizeSearchDefaults(input);
}

// Separate schema-default normalization from security validation above. Only
// validated calls are mutated, using the native tool_call input contract.
function normalizeSearchDefaults(input) {
  if (input.proxy === '') delete input.proxy;
  if (Array.isArray(input.queries) && input.queries.length === 0 && typeof input.query === 'string' && input.query.trim()) delete input.queries;
}

export function redact(value, secrets) {
  const json = JSON.stringify(value);
  if (json === undefined) return value;
  const clean = secrets.filter(Boolean).reduce((text, secret) => text.split(secret).join('[redacted]'), json);
  return JSON.parse(clean);
}
