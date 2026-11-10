/** Fixed internal REST transport. No ambient proxy, redirect, retry or raw errors. */
import http from 'node:http';
import { constants } from 'node:fs';
import { open } from 'node:fs/promises';
import { isAbsolute } from 'node:path';

const STATUS = { unauthorized: 401, forbidden: 403, invalid_request: 400, capacity: 429,
  workflow_busy: 429, upstream_error: 502, unavailable: 503, deadline: 504,
  stale_workflow: 409, stale_tab: 409, remote_outcome_unknown: 502, unsupported_backend: 503,
  egress_denied: 403, file_too_large: 413, file_in_use: 409,
  reconciliation_required: 503, viewport_mismatch: 409, viewport_unsupported: 503 };
export class BrowserError extends Error {
  constructor(code, ambiguous = false) { super(code); this.ambiguous = ambiguous; }
}
const invalidBinding = () => { throw new BrowserError('binding_unavailable'); };

function fixedOrigin(value) {
  if (typeof value !== 'string') invalidBinding();
  const url = new URL(value);
  const allowed = url.origin === 'http://tool-access:9081' || (url.hostname === '127.0.0.1' && url.port);
  if (!allowed || url.protocol !== 'http:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) invalidBinding();
  return url.origin;
}

export function validateBinding(value) {
  if (!value || Object.keys(value).sort().join(',') !== 'baseUrl,host,statePath,token') invalidBinding();
  if (typeof value.host !== 'string' || !/^tool-edge:[1-9][0-9]{0,4}$/.test(value.host) || Number(value.host.split(':')[1]) > 65535) invalidBinding();
  if (typeof value.token !== 'string' || !/^[a-f0-9]{64}$/.test(value.token)) invalidBinding();
  if (typeof value.statePath !== 'string' || !isAbsolute(value.statePath)) invalidBinding();
  return { ...value, baseUrl: fixedOrigin(value.baseUrl) };
}

export async function loadBinding(path) {
  if (typeof path !== 'string' || !isAbsolute(path)) invalidBinding();
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.uid !== process.getuid() || (stat.mode & 0o777) !== 0o600 || stat.size > 4096) invalidBinding();
    const data = Buffer.alloc(4097); const { bytesRead } = await file.read(data, 0, data.length, 0);
    if (bytesRead > 4096) invalidBinding();
    return validateBinding(JSON.parse(data.subarray(0, bytesRead).toString('utf8')));
  } finally { await file.close(); }
}

function body(response, limit) {
  return new Promise((resolve, reject) => {
    const fail = () => { reject(new BrowserError('upstream_error')); response.destroy(); };
    if (response.headers['content-encoding'] && response.headers['content-encoding'] !== 'identity') { fail(); return; }
    if (Number(response.headers['content-length']) > limit) { fail(); return; }
    const chunks = []; let length = 0;
    response.on('data', chunk => { length += chunk.length; if (length > limit) fail(); else chunks.push(chunk); });
    response.on('error', () => reject(new BrowserError('upstream_error')));
    response.on('aborted', () => reject(new BrowserError('upstream_error')));
    response.on('end', () => resolve(Buffer.concat(chunks)));
  });
}

function jsonResponse(response, mutation) {
  if (response.status !== 200) {
    let code;
    try { code = JSON.parse(response.body).error.code; } catch { /* No upstream text crosses. */ }
    if (STATUS[code] !== response.status) throw new BrowserError(mutation ? 'remote_outcome_unknown' : 'upstream_error', mutation);
    throw new BrowserError(code, code === 'remote_outcome_unknown');
  }
  try {
    const data = JSON.parse(response.body);
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('invalid');
    return data;
  } catch { throw new BrowserError(mutation ? 'remote_outcome_unknown' : 'upstream_error', mutation); }
}

function transportError(error, signal, mutation) {
  if (mutation) return new BrowserError('remote_outcome_unknown', true);
  if (signal?.aborted) return new BrowserError('cancelled');
  if (error instanceof BrowserError && error.message === 'deadline') return error;
  return new BrowserError('upstream_error');
}

export class BrowserHTTP {
  constructor(binding) { this.binding = validateBinding(binding); }
  async call(method, path, data, workflow, signal, { image = false, mutation = false } = {}) {
    if (!/^\/v1\/(?:workflows|browser\/[a-z_]+)$/.test(path)) throw new BrowserError('invalid_request');
    const encoded = data === undefined ? undefined : JSON.stringify(data);
    if (encoded && Buffer.byteLength(encoded) > 16384) throw new BrowserError('invalid_request');
    let response;
    try { response = await this.exchange(method, path, encoded, workflow, signal, image); }
    catch (error) { throw transportError(error, signal, mutation); }
    if (image && response.status === 200) return this.image(response);
    return jsonResponse(response, mutation);
  }
  async exchange(method, path, encoded, workflow, signal, image) {
    let timer;
    try {
      return await new Promise((resolve, reject) => {
        const req = http.request(new URL(path, this.binding.baseUrl), { method, agent: false, signal,
          headers: { Host: this.binding.host, Authorization: `Bearer ${this.binding.token}`,
            Accept: image ? 'image/png' : 'application/json', 'Accept-Encoding': 'identity',
            ...(encoded ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(encoded) } : {}),
            ...(workflow ? { 'X-Spartan-Workflow': workflow } : {}) } }, async res => {
          try { resolve({ status: res.statusCode, headers: res.headers, body: await body(res, image ? 2097152 : 131072) }); }
          catch (error) { reject(error); }
        });
        timer = setTimeout(() => req.destroy(new BrowserError('deadline')), 30000); timer.unref();
        req.on('error', reject); req.end(encoded);
      });
    } finally { clearTimeout(timer); }
  }
  image(response) {
    const bytes = response.body;
    if (response.headers['content-type']?.split(';')[0] !== 'image/png' || bytes.length < 33
        || !bytes.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) throw new BrowserError('upstream_error');
    const width = bytes.readUInt32BE(16); const height = bytes.readUInt32BE(20);
    if (!width || !height || width > 4096 || height > 4096 || width * height > 16000000) throw new BrowserError('upstream_error');
    return { type: 'image', mimeType: 'image/png', data: bytes.toString('base64') };
  }
}
