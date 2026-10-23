import { execFile } from 'node:child_process';
import { readFile, writeFile, rename, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const gatewayPath = fileURLToPath(new URL('../spartan-route-usage/gateway.py', import.meta.url));

function validateUrl(baseUrl) {
  let url;
  try { url = new URL(baseUrl); }
  catch { throw new Error('Spartan Route requires a credential-free HTTPS URL.'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new Error('Spartan Route requires a credential-free HTTPS URL.');
  }
}

function duration(value, fallback, minimum, maximum) {
  const result = value ?? fallback;
  if (!Number.isSafeInteger(result) || result < minimum || result > maximum) throw new Error('Invalid Route catalog interval.');
  return result;
}

export async function loadConfig(path) {
  const config = JSON.parse(await readFile(path, 'utf8'));
  validateUrl(config.baseUrl);
  return { ...config, refreshSeconds: duration(config.refreshSeconds, 300, 30, 86400),
    cacheMaxAgeSeconds: duration(config.cacheMaxAgeSeconds, 86400, 30, 31536000) };
}

export function gatewayRequest(action, document, signal) {
  return new Promise((resolve, reject) => {
    const child = execFile('python3', [gatewayPath, action], {
      timeout: 11000, maxBuffer: 3 * 1024 * 1024, signal,
    }, (error, stdout) => {
      if (error) return reject(new Error('Spartan Route unavailable'));
      try { resolve(JSON.parse(stdout)); } catch { reject(new Error('Invalid Spartan Route response')); }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(document));
  });
}

function responsesRealm(row) {
  if (!row.api_modes?.includes('responses')) return undefined;
  return ['codex_subscription', 'openai_api'].includes(row.reasoning_realm) ? row.reasoning_realm : undefined;
}

function thinkingMap(row) {
  if (!Array.isArray(row.reasoning_efforts)) return undefined;
  const efforts = new Set(row.reasoning_efforts);
  return {
    off: efforts.has('none') ? 'none' : null,
    ...Object.fromEntries(['minimal', 'low', 'medium', 'high', 'xhigh', 'max']
      .map(effort => [effort, efforts.has(effort) ? effort : null])),
  };
}

function validateRow(row, seen) {
  if (typeof row.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_./:+-]{0,255}$/.test(row.id) || seen.has(row.id)) throw new Error('Invalid or duplicate Route model ID');
  seen.add(row.id);
  const context = row.max_context ?? row.context_length;
  if (!Number.isSafeInteger(context) || context < 1024 || context > 10000000) throw new Error('Invalid Route context size');
  const maxTokens = row.max_output_tokens ?? Math.min(16384, context);
  if (!Number.isSafeInteger(maxTokens) || maxTokens < 1 || maxTokens > context) throw new Error('Invalid Route output size');
  return { contextWindow: context, maxTokens };
}

function catalogModel(row, seen) {
  const sizes = validateRow(row, seen);
  const realm = responsesRealm(row);
  return {
    id: row.id, name: typeof row.name === 'string' ? row.name : row.id,
    api: realm ? 'openai-responses' : 'openai-completions',
    spartanReasoningRealm: realm,
    input: row.multimodal === true ? ['text', 'image'] : ['text'],
    reasoning: typeof row.reasoning === 'boolean' ? row.reasoning : Boolean(realm) || row.owned_by === 'codex',
    thinkingLevelMap: thinkingMap(row),
    ...sizes,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  };
}

export function normalizeCatalog(document) {
  if (!document || !Array.isArray(document.data) || document.data.length > 1024) throw new Error('Invalid Route catalog');
  const seen = new Set();
  return document.data.filter(row => row?.tool_capable === true && !['realtime', 'images', 'embeddings', 'decisions'].includes(row.capabilities?.api)).map(row => catalogModel(row, seen));
}

export class Discovery {
  constructor(root, config, runner) {
    this.root = root; this.config = config; this.pending = undefined;
    this.fetchedAt = 0; this.models = []; this.source = 'unavailable';
    this.runner = runner ?? ((signal) => gatewayRequest('catalog', {}, signal));
  }
  async cached() {
    try {
      const cache = JSON.parse(await readFile(join(this.root, 'catalog.json'), 'utf8'));
      const age = Date.now() - cache.fetchedAt;
      if (cache.baseUrl !== this.config.baseUrl || !Number.isFinite(age) || age < 0 || age > this.config.cacheMaxAgeSeconds * 1000) return [];
      this.models = normalizeCatalog(cache.document); this.fetchedAt = cache.fetchedAt; this.source = 'cache';
      return this.models;
    } catch { return []; }
  }
  async get({ network = true, force = false, signal } = {}) {
    signal?.throwIfAborted();
    if (!this.fetchedAt) await this.cached();
    signal?.throwIfAborted();
    if (!network) return this.models;
    if (!force && this.source === 'live' && Date.now() - this.fetchedAt < this.config.refreshSeconds * 1000) return this.models;
    if (!this.pending) {
      this.pending = (async () => {
        const document = await this.runner(signal);
        signal?.throwIfAborted();
        const models = normalizeCatalog(document);
        const fetchedAt = Date.now();
        const temp = join(this.root, `.catalog-${process.pid}-${fetchedAt}.tmp`);
        try {
          await writeFile(temp, JSON.stringify({ baseUrl: this.config.baseUrl, fetchedAt, document }), { mode: 0o600, flag: 'wx' });
          signal?.throwIfAborted();
          await rename(temp, join(this.root, 'catalog.json'));
        } finally { await unlink(temp).catch(() => {}); }
        this.models = models; this.fetchedAt = fetchedAt; this.source = 'live';
        return models;
      })().finally(() => { this.pending = undefined; });
    }
    let onAbort;
    try {
      if (!signal) return await this.pending;
      return await Promise.race([this.pending, new Promise((_, reject) => {
        onAbort = () => reject(signal.reason ?? new Error('Discovery cancelled'));
        signal.addEventListener('abort', onAbort, { once: true });
        if (signal.aborted) onAbort();
      })]);
    } finally { if (onAbort) signal.removeEventListener('abort', onAbort); }
  }
}
