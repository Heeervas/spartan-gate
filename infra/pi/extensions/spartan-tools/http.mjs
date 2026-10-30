import { inflateSync } from 'node:zlib';
import { FAILURE } from './config.mjs';
import { SERVICE_ERRORS, ServiceError } from './errors.mjs';

export async function request(config, path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const signals = [controller.signal];
  if (options.signal) signals.push(options.signal);
  const signal = AbortSignal.any(signals);
  let response;
  try {
    response = await fetch(`${config.baseUrl}${path}`, requestInit(config, options, signal));
    return await checkedResponse(response, options.limit ?? 131072, signal);
  } catch (error) { throw error instanceof ServiceError ? error : new Error(FAILURE); }
  finally {
    clearTimeout(timer);
    await response?.body?.cancel().catch(() => {});
  }
}

function requestInit(config, options, signal) {
  const payload = options.body === undefined ? undefined : JSON.stringify(options.body);
  if (payload && Buffer.byteLength(payload) > 16384) throw new Error(FAILURE);
  const headers = { Authorization: `Bearer ${config.token}`, 'Accept-Encoding': 'identity',
    Accept: options.binary ? 'image/png' : 'application/json' };
  if (payload !== undefined) headers['Content-Type'] = 'application/json';
  if (options.workflow) headers['X-Spartan-Workflow'] = options.workflow;
  return { method: options.method ?? (payload === undefined ? 'GET' : 'POST'),
    redirect: 'error', signal, headers, body: payload };
}

async function checkedResponse(response, limit, signal) {
  const encoding = response.headers.get('content-encoding');
  if (encoding && encoding.toLowerCase() !== 'identity') throw new Error(FAILURE);
  if (!response.ok) throw await serviceFailure(response, signal);
  const data = await boundedBody(response, limit, signal);
  return { data, headers: response.headers };
}

async function serviceFailure(response, signal) {
  const raw = await boundedBody(response, 4096, signal);
  let error;
  try { error = JSON.parse(raw.toString('utf8'))?.error; } catch { /* Never expose raw upstream errors. */ }
  return new ServiceError(SERVICE_ERRORS.has(error?.code) ? error.code : 'service_rejected', error?.upstreamStatus);
}

async function boundedBody(response, limit, signal) {
  const chunks = [];
  let size = 0;
  const reader = response.body?.getReader();
  if (!reader) throw new Error(FAILURE);
  try {
    while (true) {
      signal.throwIfAborted();
      const next = await reader.read();
      if (next.done) return Buffer.concat(chunks, size);
      size += next.value.byteLength;
      if (size > limit) throw new Error(FAILURE);
      chunks.push(Buffer.from(next.value));
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export function jsonResponse(response) {
  if (!response.headers.get('content-type')?.toLowerCase().startsWith('application/json')) throw new Error(FAILURE);
  const data = JSON.parse(response.data.toString('utf8'));
  if (!data || typeof data !== 'object' || Array.isArray(data) || Object.hasOwn(data, 'error')) throw new Error(FAILURE);
  return data;
}

export function readerText(response) {
  const text = response.data.toString('utf8');
  if (response.headers.get('x-spartan-content-trust') !== 'untrusted-web-content' || !text.startsWith('BEGIN_UNTRUSTED_WEB_CONTENT\n') || !text.endsWith('\nEND_UNTRUSTED_WEB_CONTENT\n')) throw new Error(FAILURE);
  return text;
}

// Validate the decoded image as well as the header; Pi keeps originals on resize failure.
export function pngImage(bytes) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (bytes.length > 2097152 || bytes.length < 45 || !bytes.subarray(0, 8).equals(signature)) throw new Error(FAILURE);
  if (bytes.readUInt32BE(8) !== 13 || bytes.toString('ascii', 12, 16) !== 'IHDR') throw new Error(FAILURE);
  const width = bytes.readUInt32BE(16);
  const height = bytes.readUInt32BE(20);
  if (!width || !height || width > 4096 || height > 4096 || width * height > 16000000) throw new Error(FAILURE);
  validatePixels(bytes, width, height);
  return { type: 'image', data: bytes.toString('base64'), mimeType: 'image/png' };
}

function validatePixels(bytes, width, height) {
  // Managed screenshots are non-interlaced 8-bit RGB/RGBA PNG, not arbitrary uploads.
  const channels = new Map([[2, 3], [6, 4]]).get(bytes[25]);
  if (bytes[24] !== 8 || !channels || bytes[26] || bytes[27] || bytes[28]) throw new Error(FAILURE);
  const stride = width * channels + 1;
  const decoded = inflateSync(pngData(bytes), { maxOutputLength: height * stride });
  if (decoded.length !== height * stride) throw new Error(FAILURE);
  for (let row = 0; row < height; row++) if (decoded[row * stride] > 4) throw new Error(FAILURE);
}

function pngData(bytes) {
  let offset = 8;
  const image = [];
  let ended = false;
  while (offset + 12 <= bytes.length) {
    const size = bytes.readUInt32BE(offset);
    const end = offset + size + 12;
    if (end > bytes.length) throw new Error(FAILURE);
    const chunk = bytes.subarray(offset + 4, end - 4);
    if (crc32(chunk) !== bytes.readUInt32BE(end - 4)) throw new Error(FAILURE);
    const type = bytes.toString('ascii', offset + 4, offset + 8);
    // Reject repeated headers, animation and compressed metadata before native decoding.
    if (!['IHDR', 'IDAT', 'IEND', 'sRGB', 'gAMA', 'cHRM', 'pHYs'].includes(type)) throw new Error(FAILURE);
    if (type === 'IHDR' && offset !== 8) throw new Error(FAILURE);
    if (type === 'IDAT') image.push(bytes.subarray(offset + 8, end - 4));
    if (type === 'IEND') { ended = size === 0 && end === bytes.length; break; }
    offset = end;
  }
  if (!ended || !image.length) throw new Error(FAILURE);
  return Buffer.concat(image);
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
