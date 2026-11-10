#!/usr/bin/env node
/** Shared MCP façade with explicit session handles and no automatic mutation replay. */
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { BrowserState } from './browser-state.mjs';
import { BrowserHTTP, BrowserError, loadBinding, validateBinding } from './browser-http.mjs';
import { BROWSER_TOOLS, actionPath } from './browser-contracts.mjs';

const require = createRequire('/app/package.json');
const { Server } = require('@modelcontextprotocol/sdk-v1/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk-v1/server/stdio.js');
const { ListToolsRequestSchema, CallToolRequestSchema } = require('@modelcontextprotocol/sdk-v1/types.js');
const { AjvJsonSchemaValidator } = require('@modelcontextprotocol/sdk-v1/validation/ajv-provider.js');
const validator = new AjvJsonSchemaValidator();
const TOOLS = BROWSER_TOOLS.map(tool => ({ ...tool, validate: validator.getValidator(tool.inputSchema) }));
const SAFE_ERRORS = new Set(['invalid_request', 'capacity', 'workflow_busy', 'upstream_error', 'unavailable', 'cancelled', 'deadline',
  'unauthorized', 'forbidden', 'stale_session', 'stale_tab', 'stale_workflow', 'unsupported_backend',
  'remote_outcome_unknown', 'operation_conflict', 'operation_capacity', 'state_unavailable',
  'egress_denied', 'file_too_large', 'file_in_use', 'reconciliation_required', 'viewport_mismatch', 'viewport_unsupported', 'response_too_large']);
const failure = code => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ error: { code: SAFE_ERRORS.has(code) ? code : 'unavailable' } }) }] });
const text = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }] });
const ownKeys = (value, keys) => Object.fromEntries(keys.filter(key => value[key] !== undefined).map(key => [key, value[key]]));

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
const fingerprint = (name, args) => createHash('sha256').update(JSON.stringify(canonical([name, args]))).digest('hex');

const PAGE_FIELDS = { snapshot: ['url', 'snapshot', 'refsCount', 'truncated', 'hasMore', 'nextOffset'],
  links: ['links', 'hasMore'], images: ['images'], extract: ['data'] };

function pageResponse(action, data) {
  const framed = 'BEGIN_UNTRUSTED_WEB_CONTENT\n' + JSON.stringify(ownKeys(data, PAGE_FIELDS[action])) + '\nEND_UNTRUSTED_WEB_CONTENT\n';
  if (Buffer.byteLength(framed) > 131072) throw new BrowserError('response_too_large');
  return { content: [{ type: 'text', text: framed }] };
}

function stagedFileResponse(data) {
  if (!/^[a-f0-9]{32}$/.test(data.file_id) || !/^[a-f0-9]{64}$/.test(data.sha256)
      || typeof data.name !== 'string' || Buffer.byteLength(data.name) > 128
      || !Number.isInteger(data.size) || data.size < 0 || data.size > 20971520) throw new BrowserError('remote_outcome_unknown', true);
  return text(ownKeys(data, ['file_id', 'name', 'size', 'sha256']));
}

function tabsResponse(action, data) {
  if (action === 'create_tab') {
    if (typeof data.tabId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(data.tabId)) throw new BrowserError('remote_outcome_unknown', true);
    return text({ tab_id: data.tabId });
  }
  if (!Array.isArray(data.tabs) || data.tabs.length > 4 || data.tabs.some(row => !/^[A-Za-z0-9_-]{1,128}$/.test(row.tabId))) throw new BrowserError('upstream_error');
  return text({ tabs: data.tabs.map(row => ({ tab_id: row.tabId })) });
}

function response(action, data) {
  if (action === 'screenshot') return { content: [data] };
  if (['create_tab', 'list_tabs'].includes(action)) return tabsResponse(action, data);
  if (PAGE_FIELDS[action]) return pageResponse(action, data);
  if (action === 'file_stage') return stagedFileResponse(data);
  if (data.ok !== true) throw new BrowserError('remote_outcome_unknown', true);
  return text(action === 'viewport' ? ownKeys(data, ['ok', 'requested', 'effective', 'strategy']) : { ok: true });
}

class BrowserRuntime {
  constructor(binding, state) {
    this.state = state; this.http = new BrowserHTTP(binding);
    this.shutdown = new AbortController(); this.calls = new Set(); this.busy = new Set(); this.inflight = 0;
  }
  call(params, signal) {
    const run = this.execute(params, AbortSignal.any([this.shutdown.signal, ...(signal ? [signal] : [])]));
    this.calls.add(run);
    void run.then(() => this.calls.delete(run), () => this.calls.delete(run));
    return run;
  }
  async execute(params, signal) {
    const tool = TOOLS.find(item => item.name === params.name);
    const args = params.arguments;
    if (!tool || !tool.validate(args).valid) return failure('invalid_request');
    if (signal.aborted) return failure('cancelled');
    let operation; let id;
    try {
      if (!tool.annotations.readOnlyHint) {
        operation = await this.state.begin(args.operation_id, fingerprint(tool.name, args), args.session_id, tool.action === 'session_create');
        if (operation.result) return operation.result;
        if (operation.pending) return failure('remote_outcome_unknown');
      }
      id = operation?.session_id ?? args.session_id;
      const session = this.state.session(id);
      if (!operation && session.status !== 'active') return failure('stale_session');
      const denied = this.admissionFailure(id);
      if (denied) return await this.rejectAdmission(tool, args, operation, denied);
      this.busy.add(id); this.inflight++;
      try { return await this.dispatch(tool, args, session, id, signal); }
      finally { this.busy.delete(id); this.inflight--; }
    } catch (error) { return failure(error.message); }
  }
  admissionFailure(id) {
    if (this.busy.has(id)) return 'workflow_busy';
    return this.inflight >= 2 ? 'capacity' : undefined;
  }
  async rejectAdmission(tool, args, operation, code) {
    const result = failure(code);
    if (operation) await this.state.complete(args.operation_id, result, tool.action === 'session_create' ? { status: 'closed' } : undefined);
    return result;
  }
  async dispatch(tool, args, session, id, signal) {
    const mutation = !tool.annotations.readOnlyHint;
    let result; let update;
    try {
      if (tool.action === 'session_create') {
        const data = await this.http.call('POST', '/v1/workflows', {}, undefined, signal, { mutation: true });
        if (typeof data.workflowToken !== 'string' || !/^[a-f0-9]{64}$/.test(data.workflowToken)) throw new BrowserError('remote_outcome_unknown', true);
        update = { status: 'active', workflow: data.workflowToken };
        result = text({ session_id: id });
      } else if (tool.action === 'session_close') {
        const data = await this.http.call('DELETE', '/v1/workflows', undefined, session.workflow, signal, { mutation: true });
        if (data.ok !== true) throw new BrowserError('remote_outcome_unknown', true);
        update = { status: 'closed', workflow: null }; result = text({ ok: true });
      } else {
        const input = ownKeys(args, Object.keys(args).filter(key => !['session_id', 'operation_id', 'tab_id'].includes(key)));
        if (args.tab_id) input.tabId = args.tab_id;
        const listing = tool.action === 'list_tabs';
        const data = await this.http.call(listing ? 'GET' : 'POST', '/v1/browser/' + actionPath(tool.action),
          listing ? undefined : input, session.workflow, signal, { image: tool.action === 'screenshot', mutation });
        result = response(tool.action, data);
      }
    } catch (error) {
      if (mutation && (error.ambiguous || error.message === 'remote_outcome_unknown')) return failure('remote_outcome_unknown');
      result = failure(error.message);
      if (tool.action === 'session_create') update = { status: 'closed', workflow: null };
    }
    // Only acknowledge a durable known outcome. Failure poisons state until recovery.
    if (mutation) await this.state.complete(args.operation_id, result, update);
    return result;
  }
  async close() {
    this.shutdown.abort();
    await Promise.allSettled([...this.calls]);
    await this.state.close();
  }
}

export async function createBrowserToolsServer(settings) {
  const binding = validateBinding(settings);
  const realm = fingerprint('browser-binding-v1', ownKeys(binding, ['baseUrl', 'host', 'token']));
  const state = await BrowserState.open(binding.statePath, realm);
  const runtime = new BrowserRuntime(binding, state);
  const server = new Server({ name: 'spartan-browser-tools', version: '1.0.0' }, { capabilities: { tools: {} } });
  server.setRequestHandler(ListToolsRequestSchema, () => ({ tools: TOOLS.map(tool => ownKeys(tool, ['name', 'description', 'inputSchema', 'annotations'])) }));
  server.setRequestHandler(CallToolRequestSchema, ({ params }, extra) => runtime.call(params, extra.signal));
  const close = server.close.bind(server);
  server.close = async () => { await runtime.close(); await close(); };
  server.onclose = () => { void runtime.close(); };
  return server;
}

async function main() {
  try {
    const binding = await loadBinding(process.env.SPARTAN_BROWSER_TOOLS_BINDING);
    const server = await createBrowserToolsServer(binding);
    await server.connect(new StdioServerTransport());
    process.once('SIGTERM', () => { void server.close(); });
    process.once('SIGINT', () => { void server.close(); });
  } catch {
    process.stderr.write('spartan-browser-tools: binding_or_state_unavailable\n'); process.exitCode = 1;
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
