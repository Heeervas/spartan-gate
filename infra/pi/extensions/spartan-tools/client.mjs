import { loadConfig, checkSearchConfig, searchArguments, redact, TOKEN, FAILURE } from './config.mjs';
import { request, jsonResponse, readerText, pngImage } from './http.mjs';
import { ServiceError } from './errors.mjs';
import { readWorkflow, saveWorkflow, deleteWorkflow, stateKey } from './state.mjs';

const BROWSER_FIELDS = {
  create: ['url'], tabs: [], cleanup: [], snapshot: ['tabId'], navigate: ['tabId', 'url'],
  click: ['tabId', 'ref', 'selector'], type: ['tabId', 'ref', 'selector', 'text'],
  scroll: ['tabId', 'direction', 'amount'], screenshot: ['tabId'], close: ['tabId'],
};
const BROWSER_KNOWN_FIELDS = new Set(Object.values(BROWSER_FIELDS).flat());

export function browserBody(input) {
  const fields = BROWSER_FIELDS[input.action];
  if (!fields || Object.keys(input).some(key => key !== 'action' && !BROWSER_KNOWN_FIELDS.has(key))) throw new Error(FAILURE);
  const body = Object.fromEntries(fields.filter(key => input[key] !== undefined).map(key => [key, input[key]]));
  if (!['create', 'tabs', 'cleanup'].includes(input.action) && (typeof body.tabId !== 'string' || !body.tabId || body.tabId.length > 256)) throw new Error(FAILURE);
  return body;
}

function publicResult(action, data) {
  if (action === 'create' && typeof data.tabId === 'string') return { tabId: data.tabId };
  if (action === 'tabs' && Array.isArray(data.tabs) && data.tabs.length <= 4 && data.tabs.every(tab => typeof tab.tabId === 'string')) return { tabs: data.tabs.map(tab => ({ tabId: tab.tabId })) };
  if (action === 'snapshot') {
    if (!validSnapshot(data)) throw new Error(FAILURE);
    return Object.fromEntries(['url', 'snapshot', 'refsCount', 'truncated', 'hasMore', 'nextOffset'].filter(key => data[key] !== undefined).map(key => [key, data[key]]));
  }
  if (['navigate', 'click', 'type', 'scroll', 'close', 'cleanup'].includes(action) && data.ok === true) return { ok: true, ...(typeof data.url === 'string' ? { url: data.url } : {}) };
  throw new Error(FAILURE);
}

function validSnapshot(data) {
  const offset = data.nextOffset;
  return typeof data.snapshot === 'string' && typeof data.url === 'string'
    && Number.isInteger(data.refsCount) && data.refsCount >= 0
    && typeof data.truncated === 'boolean' && typeof data.hasMore === 'boolean'
    && (offset === null || (Number.isInteger(offset) && offset >= 0));
}

async function browserRequest(config, path, options, mutation) {
  try { return await request(config, path, options); }
  catch (error) {
    if (error instanceof ServiceError || !mutation) throw error;
    // Loss of the response does not prove a remote mutation was cancelled.
    throw new ServiceError('remote_outcome_unknown');
  }
}

export class ToolsClient {
  constructor(configPath, agentDir) {
    this.configPath = configPath;
    this.agentDir = agentDir;
    this.epoch = 0;
    this.pending = new Map();
    this.secrets = new Set();
  }

  start(ctx) { this.stop(); this.sessionId = ctx.sessionManager.getSessionId(); }
  stop() { this.epoch++; this.controller?.abort(); this.controller = new AbortController(); this.sessionId = undefined; this.pending.clear(); }

  config() {
    const config = loadConfig(this.configPath);
    this.secrets.add(config.token);
    return config;
  }

  guard(event) {
    // This is managed-tool policy, not confinement of native host tools/plugins.
    if (!['web_search', 'gate_reader', 'gate_browser', 'fetch_content', 'source_check'].includes(event.toolName)) return;
    if (['fetch_content', 'source_check'].includes(event.toolName)) return { block: true, reason: FAILURE };
    // Do not exempt nested calls: parentToolCallId is tracing, not authority.
    try {
      const config = this.config();
      checkSearchConfig(config, this.agentDir);
      if (event.toolName === 'web_search') searchArguments(event.input);
    } catch { return { block: true, reason: FAILURE }; }
  }

  searchResult(event) {
    if (event.toolName !== 'web_search') return;
    const details = event.details;
    const failed = event.isError || details?.error || details?.queries?.some(query => query.error);
    if (failed) {
      const message = details?.error === 'No query provided'
        ? 'Managed search requires a non-empty query or queries.' : FAILURE;
      return { content: [{ type: 'text', text: message }], details: { error: message }, isError: true };
    }
    return redact({ content: event.content, details: event.details, structuredContent: event.structuredContent }, [...this.secrets]);
  }

  current(ctx, epoch) {
    if (this.epoch !== epoch || this.sessionId !== ctx.sessionManager.getSessionId()) throw new Error(FAILURE);
  }

  async workflow(config, ctx, epoch, signal) {
    const id = ctx.sessionManager.getSessionId();
    const key = stateKey(config, id);
    let pending = this.pending.get(key);
    if (!pending) {
      pending = this.recoverWorkflow(config, id, ctx, epoch, signal);
      this.pending.set(key, pending);
      void pending.catch(() => { if (this.pending.get(key) === pending) this.pending.delete(key); });
    }
    const token = await pending;
    this.current(ctx, epoch);
    return token;
  }

  async recoverWorkflow(config, id, ctx, epoch, signal) {
    const saved = await readWorkflow(config, id);
    this.current(ctx, epoch);
    if (saved) { this.secrets.add(saved); return saved; }
    const result = jsonResponse(await request(config, '/v1/workflows', { body: {}, signal }));
    this.current(ctx, epoch);
    if (!TOKEN.test(result.workflowToken)) throw new Error(FAILURE);
    this.secrets.add(result.workflowToken);
    await saveWorkflow(config, id, result.workflowToken);
    this.current(ctx, epoch);
    return result.workflowToken;
  }

  async execute(kind, input, signal, ctx) {
    try {
      const epoch = this.epoch;
      this.current(ctx, epoch);
      const config = this.config();
      const combined = AbortSignal.any([this.controller.signal, ...(signal ? [signal] : [])]);
      const content = kind === 'reader'
        ? await this.reader(config, input, combined)
        : await this.browser(config, input, combined, ctx, epoch);
      this.current(ctx, epoch);
      return redact({ content, details: {} }, [...this.secrets]);
    } catch (error) {
      return { content: [{ type: 'text', text: error instanceof ServiceError ? error.message : FAILURE }], details: {}, isError: true };
    }
  }

  async reader(config, input, signal) {
    const url = new URL(input.url);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error(FAILURE);
    const path = `/fetch?${new URLSearchParams({ url: url.href })}`;
    if (Buffer.byteLength(path) > 16384) throw new Error(FAILURE);
    const text = readerText(await request(config, path, { signal, limit: 524288 }));
    return [{ type: 'text', text }];
  }

  async browser(config, input, signal, ctx, epoch) {
    const body = browserBody(input);
    if (input.action === 'cleanup') return this.cleanup(config, ctx, epoch, signal);
    const workflow = await this.workflow(config, ctx, epoch, signal);
    const screenshot = input.action === 'screenshot';
    const path = input.action === 'tabs' ? '/v1/browser/tabs' : `/v1/browser/${input.action}`;
    const mutation = !['tabs', 'snapshot', 'screenshot'].includes(input.action);
    const response = await browserRequest(config, path, { body: input.action === 'tabs' ? undefined : body,
      workflow, signal, limit: screenshot ? 2097152 : 524288, binary: screenshot }, mutation);
    if (screenshot) {
      if (response.headers.get('content-type')?.split(';')[0] !== 'image/png') throw new Error(FAILURE);
      return [pngImage(response.data)];
    }
    return [{ type: 'text', text: JSON.stringify(publicResult(input.action, jsonResponse(response))) }];
  }

  async cleanup(config, ctx, epoch, signal) {
    const id = ctx.sessionManager.getSessionId();
    const workflow = await readWorkflow(config, id);
    this.current(ctx, epoch);
    if (!workflow) return [{ type: 'text', text: '{"ok":true}' }];
    const data = jsonResponse(await browserRequest(config, '/v1/workflows', { method: 'DELETE', workflow, signal }, true));
    this.current(ctx, epoch);
    if (data.ok !== true) throw new Error(FAILURE);
    await deleteWorkflow(config, id);
    this.current(ctx, epoch);
    this.pending.delete(stateKey(config, id));
    return [{ type: 'text', text: '{"ok":true}' }];
  }
}
