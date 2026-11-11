/** Optional native API client. Only private aliases select identities; no browser patches. */
import http from 'node:http';
import { constants } from 'node:fs';
import { open, lstat, realpath } from 'node:fs/promises';
import { dirname, resolve, isAbsolute } from 'node:path';
import { createHash } from 'node:crypto';
import { BrowserError } from './browser-http.mjs';
import { debugScript, waitScript } from './browser-debug.mjs';

const id = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const keys = (value, names) => object(value) && Object.keys(value).sort().join(',') === names.split(',').sort().join(',');
const fail = code => { throw new BrowserError(code); };
const copy = (value, names) => Object.fromEntries(names.filter(name => value[name] !== undefined).map(name => [name, value[name]]));
export const NATIVE_ACTIONS = new Set(['evaluate', 'wait_for', 'stats', 'downloads', 'attachable_tabs', 'attach', 'debug_start', 'debug_read', 'debug_stop']);

function nativeOrigin(value) {
  const url = new URL(value);
  if (!(url.origin === 'http://camofox:9377' || (url.hostname === '127.0.0.1' && url.port))
      || url.protocol !== 'http:' || url.pathname !== '/' || url.username || url.password || url.search || url.hash) fail('binding_unavailable');
  return url.origin;
}

function validateProfile(alias, profile) {
  if (!id(alias) || !object(profile) || !id(profile.userId)) fail('binding_unavailable');
  if (keys(profile, 'userId,groupAccess') && profile.groupAccess === 'profile') return;
  if (!keys(profile, 'userId,sessionKeys') || !Array.isArray(profile.sessionKeys)
      || !profile.sessionKeys.length || profile.sessionKeys.length > 32
      || !profile.sessionKeys.every(id) || new Set(profile.sessionKeys).size !== profile.sessionKeys.length) fail('binding_unavailable');
}

function validateProfiles(profiles, managedUserId) {
  if (!object(profiles) || Object.keys(profiles).length > 8) fail('binding_unavailable');
  const used = new Set();
  for (const [alias, profile] of Object.entries(profiles)) {
    validateProfile(alias, profile);
    if (profile.groupAccess === 'profile') {
      if (Object.keys(profiles).length !== 1 || profile.userId === managedUserId) fail('binding_unavailable');
      continue;
    }
    for (const group of profile.sessionKeys) {
      const identity = profile.userId + ':' + group;
      if (used.has(identity) || profile.userId === managedUserId) fail('binding_unavailable');
      used.add(identity);
    }
  }
}

function groupAllowed(profile, group) {
  return id(group) && (profile.groupAccess === 'profile' || profile.sessionKeys.includes(group));
}

function validateUploadRoot(root) {
  if (typeof root !== 'string' || !/^\/(?:[A-Za-z0-9_.-]+\/?)+$/.test(root)
      || root.length > 512 || root.endsWith('/') || resolve(root) !== root) fail('binding_unavailable');
}

export function validateNativeBinding(value) {
  if (!keys(value, 'baseUrl,token,managedUserId,profiles,uploadRoot')) fail('binding_unavailable');
  const baseUrl = nativeOrigin(value.baseUrl);
  if (typeof value.token !== 'string' || !/^[\x21-\x7e]{16,512}$/.test(value.token) || !id(value.managedUserId)) fail('binding_unavailable');
  validateProfiles(value.profiles, value.managedUserId);
  validateUploadRoot(value.uploadRoot);
  return { ...value, baseUrl };
}

export async function loadNativeBinding(path) {
  if (!isAbsolute(path)) fail('binding_unavailable');
  const parent = await lstat(dirname(path));
  if (!parent.isDirectory() || parent.uid !== process.getuid() || (parent.mode & 0o7777) !== 0o700
      || await realpath(dirname(path)) !== resolve(dirname(path))) fail('binding_unavailable');
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o777) !== 0o600 || stat.size > 16384) fail('binding_unavailable');
    const buffer = Buffer.alloc(16385); const { bytesRead } = await file.read(buffer);
    if (bytesRead > 16384) fail('binding_unavailable');
    return validateNativeBinding(JSON.parse(buffer.subarray(0, bytesRead).toString('utf8')));
  } finally { await file.close(); }
}

function safeURL(value) {
  try {
    const url = new URL(value);
    if (!['http:', 'https:', 'about:'].includes(url.protocol)) return undefined;
    url.username = ''; url.password = ''; url.search = ''; url.hash = '';
    return url.href.slice(0, 1024);
  } catch { return undefined; }
}

function statsMetadata(data) {
  const result = { url: safeURL(data.url) };
  for (const key of ['toolCalls', 'refsCount', 'downloadsCount']) {
    if (!Number.isSafeInteger(data[key]) || data[key] < 0) fail('upstream_error');
    result[key] = data[key];
  }
  return result;
}

function downloadMetadata(row) {
  if (!object(row)) fail('upstream_error');
  const result = copy(row, ['id', 'suggestedFilename', 'bytes', 'createdAt', 'mimeType']);
  for (const value of Object.values(result)) {
    if (value === null) continue;
    if (typeof value === 'string' && Buffer.byteLength(value) <= 1024) continue;
    if (Number.isSafeInteger(value) && value >= 0) continue;
    fail('upstream_error');
  }
  result.failed = typeof row.failure === 'string' && row.failure.length > 0;
  return result;
}

class NativeResponseLimit extends BrowserError {
  constructor(status) { super('response_too_large'); this.status = status; }
}

function nativeBody(response, mutation) {
  if (response.status !== 200) throw new BrowserError(mutation ? 'remote_outcome_unknown' : 'upstream_error', mutation);
  try {
    const data = JSON.parse(response.bytes);
    if (!object(data)) throw new Error('invalid');
    return data;
  } catch { throw new BrowserError(mutation ? 'remote_outcome_unknown' : 'upstream_error', mutation); }
}

function nativeRequest(binding, method, path, userId, body, params) {
  const suffix = /^\/tabs\/[A-Za-z0-9_-]{1,128}\/(evaluate|stats|downloads|upload)$/.exec(path)?.[1];
  const allowed = method === 'GET' ? ['/tabs', '/spartan/capabilities'].includes(path) || ['stats', 'downloads'].includes(suffix)
    : method === 'POST' && ['evaluate', 'upload'].includes(suffix);
  if (!allowed || (userId !== undefined && !id(userId))) fail('invalid_request');
  const url = new URL(path, binding.baseUrl);
  if (method === 'GET' && userId) url.searchParams.set('userId', userId);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
  const encoded = method === 'POST' ? JSON.stringify({ ...body, userId }) : undefined;
  if (encoded && Buffer.byteLength(encoded) > 65536) fail('invalid_request');
  return { url, encoded };
}

function transportFailure(error, signal, mutation) {
  if (mutation) throw new BrowserError('remote_outcome_unknown', true);
  if (signal?.aborted) fail('cancelled');
  if (error instanceof BrowserError) throw error;
  fail('upstream_error');
}

export class NativeBrowser {
  constructor(binding) {
    this.binding = validateNativeBinding(binding);
    this.realm = createHash('sha256').update(JSON.stringify(this.binding)).digest('hex');
  }
  async request(method, path, userId, { body, signal, mutation = false, params = {} } = {}) {
    const { url, encoded } = nativeRequest(this.binding, method, path, userId, body, params);
    try { return nativeBody(await this.exchange(url, method, encoded, signal), mutation); }
    catch (error) {
      // A bounded evaluate-result refusal is a durable local receipt, not a
      // success/absence-of-effects claim. Never apply this to upload claims.
      if (path.endsWith('/evaluate') && error instanceof NativeResponseLimit && error.status === 200) throw error;
      transportFailure(error, signal, mutation);
    }
  }
  exchange(url, method, encoded, signal) {
    return new Promise((resolveResult, reject) => {
      let timer;
      const request = http.request(url, { method, signal, agent: false, headers: {
        Authorization: `Bearer ${this.binding.token}`, Accept: 'application/json', 'Accept-Encoding': 'identity',
        ...(encoded ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(encoded) } : {}),
      } }, response => {
        const chunks = []; let length = 0;
        const tooLarge = () => { reject(new NativeResponseLimit(response.statusCode)); response.destroy(); };
        if (response.headers['content-encoding'] && response.headers['content-encoding'] !== 'identity') {
          reject(new BrowserError('upstream_error')); response.destroy(); return;
        }
        if (Number(response.headers['content-length']) > 131072) { tooLarge(); return; }
        response.on('data', chunk => { length += chunk.length; if (length > 131072) tooLarge(); else chunks.push(chunk); });
        response.on('end', () => resolveResult({ status: response.statusCode, bytes: Buffer.concat(chunks) }));
        response.on('error', reject); response.on('aborted', () => reject(new BrowserError('upstream_error')));
      });
      timer = setTimeout(() => request.destroy(new BrowserError('deadline')), 15000); timer.unref();
      request.on('close', () => clearTimeout(timer)); request.on('error', reject); request.end(encoded);
    });
  }
  profile(alias) {
    const profile = Object.hasOwn(this.binding.profiles, alias) && this.binding.profiles[alias];
    if (!profile) fail('forbidden');
    return profile;
  }
  async list(alias, signal) {
    const profile = this.profile(alias);
    const data = await this.request('GET', '/tabs', profile.userId, { signal });
    if (!Array.isArray(data.tabs) || data.tabs.length > 256) fail('upstream_error');
    return { tabs: data.tabs.filter(row => object(row) && id(row.tabId) && groupAllowed(profile, row.listItemId))
      .slice(0, 32).map(row => ({ tab_id: row.tabId, session_key: row.listItemId, url: safeURL(row.url) })) };
  }
  async inspect(userId, tabId, signal) {
    if (!id(tabId)) fail('invalid_request');
    const data = await this.request('GET', `/tabs/${tabId}/stats`, userId, { signal });
    if (data.tabId !== tabId || !id(data.sessionKey)) fail('stale_tab');
    return data;
  }
  async attach(args, signal) {
    const profile = this.profile(args.profile);
    if (!groupAllowed(profile, args.session_key)) fail('forbidden');
    const data = await this.inspect(profile.userId, args.tab_id, signal);
    if (data.sessionKey !== args.session_key) fail('forbidden');
    return { profile: args.profile, group: args.session_key, tab_id: args.tab_id, realm: this.realm };
  }
  async context(session, tabId, restTabs, signal) {
    const attached = session.attachment;
    let userId = this.binding.managedUserId;
    if (attached) {
      if (attached.realm !== this.realm || attached.tab_id !== tabId) fail('forbidden');
      const profile = this.profile(attached.profile);
      if (!groupAllowed(profile, attached.group)) fail('forbidden');
      userId = profile.userId;
    } else if (!restTabs.some(row => row.tabId === tabId)) fail('forbidden');
    const data = await this.inspect(userId, tabId, signal);
    if (attached && data.sessionKey !== attached.group) fail('forbidden');
    return { userId, metadata: data };
  }
  async evaluate(userId, tabId, expression, signal, mutation = true) {
    const data = await this.request('POST', `/tabs/${tabId}/evaluate`, userId, { body: { expression }, signal, mutation });
    if (data.ok !== true) throw new BrowserError(mutation ? 'remote_outcome_unknown' : 'upstream_error', mutation);
    // Express omits undefined results; ok:true still acknowledges that execution.
    return Object.hasOwn(data, 'result') ? data.result : null;
  }
  async upload(userId, args, files, signal) {
    const caps = await this.request('GET', '/spartan/capabilities', undefined, { signal });
    if (caps.singleAttemptBrowserUpload !== true) fail('unsupported_backend');
    const paths = args.file_ids.map(fileId => this.binding.uploadRoot + '/' + fileId + '/' + files[fileId].name);
    const target = copy(args, ['ref', 'selector']);
    const data = await this.request('POST', `/tabs/${args.tab_id}/upload`, userId,
      { body: { ...target, path: paths, singleAttempt: true }, signal, mutation: true });
    if (data.ok !== true) throw new BrowserError('remote_outcome_unknown', true);
    return { ok: true };
  }
  async perform(action, args, context, signal) {
    const { userId, metadata } = context;
    if (action === 'stats') return statsMetadata(metadata);
    if (action === 'downloads') return this.downloads(userId, args.tab_id, signal);
    if (action === 'evaluate') return { result: await this.evaluate(userId, args.tab_id, args.expression, signal) };
    if (action === 'wait_for') return this.evaluate(userId, args.tab_id, waitScript(args.selector, args.timeout_ms ?? 5000), signal);
    const debugAction = action.replace('debug_', '');
    const result = await this.evaluate(userId, args.tab_id, debugScript(debugAction, {
      capture_id: action === 'debug_start' ? args.operation_id : args.capture_id,
      ttl_ms: args.ttl_ms ?? 60000, cursor: args.cursor ?? 0, limit: args.limit ?? 20,
    }), signal, action !== 'debug_read');
    if (!object(result)) fail('upstream_error');
    if (result.ok !== true) {
      const known = ['capture_missing', 'capture_busy', 'capture_unsupported'].includes(result.code);
      fail(known ? result.code : 'unavailable');
    }
    return result;
  }
  async downloads(userId, tabId, signal) {
    const data = await this.request('GET', `/tabs/${tabId}/downloads`, userId,
      { signal, params: { includeData: false, consume: false } });
    if (!Array.isArray(data.downloads) || data.downloads.length > 64) fail('upstream_error');
    return { downloads: data.downloads.map(downloadMetadata) };
  }
}
