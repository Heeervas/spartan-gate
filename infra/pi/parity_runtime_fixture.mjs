// Explicit isolated synthetic integration program; never invoke against a live home.
import assert from 'node:assert/strict';
import { createServer } from 'node:https';
import { cp, readFile, writeFile, mkdir, readdir, rename } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { once } from 'node:events';

const root = process.argv[2];
const missingChildProvider = process.argv[3] === 'missing-child-provider';
const fastInheritance = process.argv[3] === 'fast-inheritance';
assert.equal(process.env.HOME, root);
const agentDir = join(root, 'agent');
process.env.PI_CODING_AGENT_DIR = agentDir;
const modelId = 'codex/gpt-6.1-sol-282kcontext';
const requests = [];
const server = createServer({ key: await readFile(join(root, 'key.pem')), cert: await readFile(join(root, 'cert.pem')) }, async (req, res) => {
  let text = '';
  for await (const chunk of req) text += chunk;
  if (req.url === '/v1/responses') {
    const body = JSON.parse(text); requests.push(body);
    const n = requests.length;
    const reasoning = { type: 'reasoning', id: `rs_${n}`, summary: [], encrypted_content: `sgr:synthetic-${n}` };
    const message = { type: 'message', id: `msg_${n}`, role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: 'synthetic child complete', annotations: [] }] };
    const response = { id: `resp_${n}`, status: 'completed', model: modelId, output: [reasoning, message],
      usage: { input_tokens: 10, output_tokens: 4, output_tokens_details: { reasoning_tokens: 2 } } };
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    for (const data of [{ type: 'response.created', response: { id: response.id } },
      { type: 'response.output_item.done', output_index: 0, item: reasoning },
      { type: 'response.output_item.added', output_index: 1, item: { ...message, content: [] } },
      { type: 'response.content_part.added', output_index: 1, content_index: 0, part: { type: 'output_text', text: '', annotations: [] } },
      { type: 'response.output_text.delta', output_index: 1, content_index: 0, delta: message.content[0].text },
      { type: 'response.output_item.done', output_index: 1, item: message }, { type: 'response.completed', response }]) {
      res.write(`data: ${JSON.stringify(data)}\n\n`);
    }
    res.end(); return;
  }
  // Telemetry is optional; explicit failure is synthetic and never contacts Route.
  res.writeHead(503, { 'Content-Type': 'application/json' }); res.end('{"error":"synthetic unavailable"}');
});
server.listen(0, '127.0.0.1'); await once(server, 'listening');
const baseUrl = `https://127.0.0.1:${server.address().port}/v1`;
const routeRoot = process.env.PI_PARITY_ROUTE_SOURCE || resolve('infra/pi/extensions');
const installedRoute = join(root, 'route');
await mkdir(installedRoute, { recursive: true });
// Use coherent current source; only the historical installed entry filename differs.
for (const entry of ['spartan-route-provider', 'spartan-route-usage', 'spartan-route-cache.ts', 'spartan-route-usage.ts',
  process.env.PI_PARITY_ROUTE_SOURCE ? 'spartan-route.ts' : 'spartan-route-provider.ts']) {
  await cp(join(routeRoot, entry), join(installedRoute, entry), { recursive: true });
}
if (!process.env.PI_PARITY_ROUTE_SOURCE) await rename(join(installedRoute, 'spartan-route-provider.ts'), join(installedRoute, 'spartan-route.ts'));
await mkdir(agentDir, { recursive: true });
await mkdir(join(agentDir, 'agents'), { recursive: true });
await mkdir(join(agentDir, 'extensions/subagent'), { recursive: true });
process.env.PI_SPARTAN_ROUTE_CONFIG = join(root, 'route-config.json');
await writeFile(process.env.PI_SPARTAN_ROUTE_CONFIG, JSON.stringify({ baseUrl, credentialsFile: join(root, 'credentials.env') }));
await writeFile(join(root, 'credentials.env'), 'CADDY_AUTH_USER=synthetic\nCADDY_AUTH_PASSWORD=synthetic\n');
await writeFile(join(root, 'catalog.json'), JSON.stringify({ baseUrl, fetchedAt: Date.now(), document: { data: [{
  id: modelId, tool_capable: true, owned_by: 'codex', max_context: 282000, max_output_tokens: 4096,
  reasoning: true, reasoning_efforts: ['medium', 'high'], api_modes: ['responses'], reasoning_realm: 'codex_subscription',
}] } }));
const { execFileSync } = await import('node:child_process');
assert.ok(process.env.PI_CODEX_FAST_SOURCE, 'PI_CODEX_FAST_SOURCE must select the reviewed fast extension program file');
const fastRoot = join(root, 'fast-program');
await mkdir(join(fastRoot, 'extensions'), { recursive: true });
await cp(process.env.PI_CODEX_FAST_SOURCE, join(fastRoot, 'extensions/codex-fast.ts'));
const bindings = { home: root, agentDir, routeRoot: installedRoute, containerRoot: resolve('infra/pi/container'), skillsRoot: join(root, 'skills'), fastRoot };
await writeFile(join(root, 'bindings.json'), JSON.stringify(bindings));
await writeFile(join(root, 'before.json'), '{}');
execFileSync('python3', ['infra/pi/prepare_parity.py', '--settings', join(root, 'before.json'), '--bindings', join(root, 'bindings.json'), '--output', join(root, 'candidates')]);
for (const name of ['shared-skills', 'agent-managed-projects', 'agent-team-orchestration', 'builder-assurance',
  'builder-engineering-rules', 'builder-behavioral-verification', 'systematic-debugging', 'test-audit', 'requesting-code-review']) {
  await mkdir(join(root, 'skills', name), { recursive: true });
  await writeFile(join(root, 'skills', name, 'SKILL.md'), `---\nname: ${name}\ndescription: Synthetic resource discovery fixture\n---\nNo actions.\n`);
}
const settings = JSON.parse(await readFile(join(root, 'candidates/settings.json')));
settings.retry = { enabled: false };
settings['pi-codex-fast'] = { mode: 'off', enabled: false };
if (missingChildProvider) settings.subagents.defaultExtensions = settings.subagents.defaultExtensions.filter(p => !p.endsWith('/spartan-route.ts'));
await writeFile(join(agentDir, 'settings.json'), JSON.stringify(settings));
await writeFile(join(agentDir, 'extensions/subagent/config.json'), await readFile(join(root, 'candidates/subagent-config.json')));
for (const name of ['builder', 'reviewer', 'general-purpose']) {
  const role = process.env.PI_PARITY_ROLE_ROOT
    ? await readFile(join(process.env.PI_PARITY_ROLE_ROOT, `${name}.md`), 'utf8')
    : `---\nname: ${name}\ndescription: Synthetic selected role\ntools: read\ninheritProjectContext: false\nacceptanceRole: read-only\n---\nReturn synthetic result.\n`;
  await writeFile(join(agentDir, `agents/${name}.md`), role);
}
// A legacy skill/support file must not enroll as an agent.
await mkdir(join(root, '.agents/skills/accidental/references'), { recursive: true });
await writeFile(join(root, '.agents/skills/accidental/references/pi.md'), '---\nname: accidental\ndescription: must not be an agent\n---\nNo.');
const sdk = await import(pathToFileURL(join(process.env.PI_TEST_PACKAGE_DIR, 'dist/index.js')).href);
const settingsManager = sdk.SettingsManager.create(root, agentDir);
const loader = new sdk.DefaultResourceLoader({ cwd: root, agentDir, settingsManager, noContextFiles: true, noThemes: true, noPromptTemplates: true });
let session;
let activeId;
try {
  await loader.reload();
  assert.deepEqual(loader.getExtensions().errors, []);
  assert.equal(loader.getExtensions().extensions.length, 5);
  if (fastInheritance) loader.getExtensions().runtime.flagValues.set('fast', true);
  assert.equal(loader.getSkills().skills.filter(s => s.name !== 'accidental').length, 9, 'nine explicit common skills');
  assert.ok(loader.getSkills().skills.some(s => s.name === 'accidental'), 'native skill discovery remains enabled, unlike agent discovery');
  const manager = sdk.SessionManager.create(root, join(root, 'parent-sessions'));
  ({ session } = await sdk.createAgentSession({ cwd: root, agentDir, settingsManager, resourceLoader: loader, sessionManager: manager }));
  await session.bindExtensions({ mode: 'rpc', onError: e => { throw new Error(JSON.stringify(e)); } });
  const tools = loader.getExtensions().extensions.flatMap(e => [...e.tools.values()]);
  const subagent = tools.find(t => t.definition.name === 'subagent')?.definition;
  assert.ok(subagent, 'actual subagent tool is registered');
  assert.equal(session.modelRuntime.getRegisteredProviderIds().filter(p => p === 'spartan-route').length, 1);
  const ctx = session.extensionRunner.createContext();
  const call = params => subagent.execute('fixture', params, undefined, undefined, ctx);
  const listed = await call({ action: 'list', options: { capabilities: true } });
  assert.deepEqual(listed.details.agentCapabilities.agents.map(a => a.name).sort(), ['builder', 'general-purpose', 'reviewer']);
  const listing = JSON.stringify(listed);
  for (const name of ['builder', 'reviewer', 'general-purpose']) assert.match(listing, new RegExp(name));
  assert.doesNotMatch(listing, /accidental|researcher|oracle/);
  // Provider registrations bind before selecting the newly discovered model.
  const routeModel = session.modelRuntime.getModel('spartan-route', modelId);
  assert.ok(routeModel);
  await session.setModel(routeModel);
  // The fixture owns orchestration: capture notices without parent inference.
  const sendMessage = loader.getExtensions().runtime.sendMessage;
  loader.getExtensions().runtime.sendMessage = (message, options) => sendMessage(message, { ...options, triggerTurn: false });
  async function wait(id) {
    for (let n = 0; n < 300; n++) {
      const status = await call({ action: 'status', id });
      const raw = JSON.stringify(status);
      if (/State: (?:complete|failed|partial|rejected|stopped)/.test(raw)
          && status.details?.lifecycleStatus?.processTerminal?.state === 'observed') return status;
      await new Promise(r => setTimeout(r, 100));
    }
    throw new Error('Synthetic child did not settle');
  }
  if (fastInheritance) {
    for (const [mode, diskMode] of [['fast', 'off'], ['ultrafast', 'off'], ['off', 'fast']]) {
      if (mode !== 'fast') {
        await session.prompt('/codex-ultrafast');
        // Drain the normal async persistence queue, then deliberately leave a
        // conflicting disk default: the next child must use live parent state.
        for (let n = 0; n < 100; n++) {
          const saved = JSON.parse(await readFile(join(agentDir, 'settings.json'), 'utf8'));
          if (saved['pi-codex-fast'].mode === mode) break;
          await new Promise(r => setTimeout(r, 10));
        }
        settings['pi-codex-fast'] = { mode: diskMode, enabled: diskMode !== 'off' };
        await writeFile(join(agentDir, 'settings.json'), JSON.stringify(settings));
      }
      assert.equal(process.env.PI_CODEX_FAST_PARENT_MODE, mode);
      const launched = await call({ agent: 'general-purpose', task: 'Synthetic tier inheritance', async: true,
        model: `spartan-route/${modelId}:high`, options: { context: 'fresh', timeoutMs: 10000,
          acceptance: { level: 'none', reason: 'Synthetic tier transport only' } } });
      assert.notEqual(launched.isError, true);
      assert.ok(launched.details?.runId);
      assert.match(JSON.stringify(await wait(launched.details.runId)), /State: complete/);
      assert.equal(process.env.PI_CODEX_FAST_PARENT_MODE, mode, 'detached child cannot change parent mode');
    }
    assert.equal(requests.length, 3);
    assert.deepEqual(requests.map(r => r.service_tier), ['priority', 'ultrafast', undefined]);
    console.log('PARITY_FAST_OK: parent startup --fast over disk off, live ultrafast over disk off, live off over disk fast => child tiers priority/ultrafast/default; observed runners terminal');
  } else {
  // Persist real parent replay state first, then freeze bytes during child turns.
  await session.prompt('Synthetic parent');
  assert.equal(session.messages.at(-1).stopReason, 'stop');
  const parentBefore = await readFile(manager.getSessionFile(), 'utf8');
  const launched = await call({ agent: 'general-purpose', task: 'Synthetic child', async: true,
    model: `spartan-route/${modelId}:high`, options: { context: 'fresh', timeoutMs: 10000,
      acceptance: { level: 'none', reason: 'Synthetic transport/resource fixture, not coding-task acceptance' } } });
  console.log('launch extension snapshot', JSON.stringify(launched.details?.launchResolvedExtensions));
  assert.notEqual(launched.isError, true);
  activeId = launched.details?.runId;
  assert.ok(activeId, 'async receipt must supply run id');
  const finished = await wait(activeId);
  console.log('child process receipt', JSON.stringify(finished.details?.lifecycleStatus));
  if (missingChildProvider) {
    assert.match(JSON.stringify(finished), /State: failed/);
    assert.equal(requests.length, 1, 'missing child provider must refuse before any child HTTP');
    assert.ok((await readFile(manager.getSessionFile(), 'utf8')).startsWith(parentBefore));
    console.log('PARITY_NEGATIVE_OK: omitting explicit child provider fails closed before child inference');
  } else {
  assert.match(JSON.stringify(finished), /State: complete/);
  assert.ok((await readFile(manager.getSessionFile(), 'utf8')).startsWith(parentBefore), 'parent history cannot be rewritten');
  const files = await readdir(join(agentDir, 'subagents/sessions'), { recursive: true });
  const childPath = join(agentDir, 'subagents/sessions', files.find(f => f.endsWith('.jsonl')));
  const child = await readFile(childPath, 'utf8');
  assert.match(child, /spartan-route-replay-realm-v2/);
  const parentLock = parentBefore.split('\n').filter(Boolean).map(JSON.parse).find(e => e.customType === 'spartan-route-replay-realm-v2');
  const childLock = child.split('\n').filter(Boolean).map(JSON.parse).find(e => e.customType === 'spartan-route-replay-realm-v2');
  assert.notEqual(parentLock.data.identity, childLock.data.identity);
  const resumed = await call({ action: 'resume', id: activeId, message: 'Synthetic continuation' });
  console.log('resume extension snapshot', JSON.stringify(resumed.details?.launchResolvedExtensions));
  assert.notEqual(resumed.isError, true);
  activeId = resumed.details?.runId;
  assert.ok(activeId);
  const continued = await wait(activeId);
  console.log('continuation process receipt', JSON.stringify(continued.details?.lifecycleStatus));
  assert.match(JSON.stringify(continued), /State: complete/);
  const parentAfter = await readFile(manager.getSessionFile(), 'utf8');
  assert.ok(parentAfter.startsWith(parentBefore), 'parent history cannot be rewritten');
  const replayState = text => text.split('\n').filter(Boolean).map(JSON.parse)
    .filter(e => e.customType === 'spartan-route-replay-realm-v2' || e.message?.role === 'assistant');
  assert.deepEqual(replayState(parentAfter), replayState(parentBefore), 'child cannot change parent Route state');
  assert.equal(requests.length, 3);
  assert.equal(requests[1].input.some(i => i.type === 'reasoning'), false, 'fresh child must not inherit parent ciphertext');
  assert.equal(requests[2].input.find(i => i.type === 'reasoning')?.encrypted_content, 'sgr:synthetic-2');
  assert.equal(requests[1].reasoning.effort, 'high');
  console.log('PARITY_NATIVE_OK: real loader, nine selected skills, three selected agents, async Route child + retained continuation, distinct child-owned replay, parent replay unchanged; synthetic HTTPS only');
  }
  }
} finally {
  if (session) {
    await session.extensionRunner.emit({ type: 'session_shutdown', reason: 'quit' });
    session.dispose();
  }
  server.closeAllConnections(); await new Promise(r => server.close(r));
}
