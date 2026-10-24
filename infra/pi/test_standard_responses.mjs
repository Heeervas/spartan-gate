import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const piDir = process.env.PI_CODING_AGENT_DIR || dirname(require.resolve('@earendil-works/pi-coding-agent/package.json'));
const sdk = pathToFileURL(createRequire(resolve(piDir, 'package.json')).resolve('openai')).href;
const { default: OpenAI } = await import(sdk);
const fixturePath = fileURLToPath(new URL('./responses_fixture.py', import.meta.url));
const model = 'codex/gpt-6-sol';
const tools = [{ type: 'function', name: 'answer', parameters: { type: 'object', properties: {} } }];

async function fixture(t) {
  const child = spawn(process.env.PYTHON || 'python3', ['-B', fixturePath], { stdio: ['pipe', 'pipe', 'pipe'] });
  const exited = once(child, 'exit');
  let stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  const lines = createInterface({ input: child.stdout });
  t.after(async () => {
    child.stdin.end('\n');
    const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
    const [code] = await exited;
    clearTimeout(timer);
    lines.close();
    assert.equal(code, 0, stderr);
  });
  const readiness = setTimeout(() => child.kill('SIGKILL'), 10000);
  const line = await Promise.race([once(lines, 'line').then(([line]) => line),
    exited.then(() => { throw new Error(stderr || 'Fixture exited before readiness'); })])
    .finally(() => clearTimeout(readiness));
  const config = JSON.parse(line);
  const client = new OpenAI({ baseURL: config.baseURL, apiKey: 'secret', maxRetries: 0 });
  const control = async (body = {}) => (await fetch(config.control, {
    method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' },
  })).json();
  return { ...config, client, control };
}

async function complete(client, input, stream = false) {
  const response = await client.responses.create({ model, input, tools, store: false, stream });
  if (!stream) { assert.equal(response.status, 'completed'); return response; }
  let terminal;
  for await (const event of response) {
    if (event.type === 'response.completed') terminal = event.response;
    assert.notEqual(event.type, 'response.failed');
  }
  assert.equal(terminal?.status, 'completed');
  return terminal;
}

async function resumedProcess(baseURL, input) {
  const script = `import OpenAI from ${JSON.stringify(sdk)};
    let raw=''; for await (const b of process.stdin) raw += b;
    const client = new OpenAI({baseURL:process.argv[1],apiKey:'secret',maxRetries:0});
    console.log(JSON.stringify(await client.responses.create({model:${JSON.stringify(model)},
      input:JSON.parse(raw),store:false})));`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', script, baseURL]);
  let output = '', error = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { error += chunk; });
  child.stdin.end(JSON.stringify(input));
  const timer = setTimeout(() => child.kill('SIGKILL'), 10000);
  const [code] = await once(child, 'exit');
  clearTimeout(timer);
  assert.equal(code, 0, error);
  const result = JSON.parse(output);
  assert.equal(result.status, 'completed');
  return result;
}

for (const stream of [false, true]) {
  test(`unmodified OpenAI SDK completes tool/encrypted replay and fresh-process resume (stream=${stream})`, async t => {
    const service = await fixture(t);
    const input = [{ role: 'user', content: 'Use answer to obtain the result' }];
    const first = await complete(service.client, input, stream);
    assert.ok(first.output.some(item => item.type === 'reasoning' && item.encrypted_content));
    const call = first.output.find(item => item.type === 'function_call');
    assert.equal(call.name, 'answer');
    assert.deepEqual(JSON.parse(call.arguments), {});
    // The inert tool actually runs; its result is required by the upstream fixture.
    const answer = () => String(6 * 7);
    input.push(...first.output, { type: 'function_call_output', call_id: call.call_id, output: answer() });
    const second = await complete(service.client, input, stream);
    assert.equal(second.output.find(item => item.type === 'message').content[0].text, 'answer=42');
    input.push(...second.output, { role: 'user', content: 'Continue after restart' });
    await service.control({ restart: true });
    const resumed = await resumedProcess(service.baseURL, input);
    assert.equal(resumed.output.find(item => item.type === 'message').content[0].text, 'answer=42');
    const observed = await service.control();
    assert.equal(observed.bindings, 0);
    assert.equal(observed.calls.length, 3);
    assert.equal(new Set(observed.calls.map(call => call.account)).size, 1);
    for (const call of observed.calls.slice(1)) {
      const opaque = call.body.input.filter(item => item.type === 'reasoning');
      assert.ok(opaque.length);
      assert.ok(opaque.every(item => item.encrypted_content === 'provider-opaque-A'));
      assert.equal(call.body.spartan_binding_mac, undefined);
    }
  });
}

async function rejected(client, input, code, extra = {}) {
  await assert.rejects(client.responses.create({ model, input, store: false, ...extra }),
    error => error.code === code);
}

test('synthetic upstream requires matching preceding tool call IDs, not only correct result text', async t => {
  const service = await fixture(t);
  const first = await complete(service.client, 'start');
  const call = first.output.find(item => item.type === 'function_call');
  const result = { type: 'function_call_output', call_id: call.call_id, output: '42' };
  await rejected(service.client, [...first.output, { ...result, call_id: 'foreign_call' }], 'fixture_wrong_tool_pair');
  await rejected(service.client, [result, ...first.output], 'fixture_wrong_tool_pair');
  await rejected(service.client, [...first.output, result, result], 'fixture_wrong_tool_pair');
  const valid = await complete(service.client, [...first.output, result]);
  assert.equal(valid.output.find(item => item.type === 'message').content[0].text, 'answer=42');
});

test('receipt integrity, realm and present-invalid-legacy denials occur before upstream submission', async t => {
  const service = await fixture(t);
  const first = await complete(service.client, 'start');
  const reason = first.output.find(item => item.type === 'reasoning');
  const parts = reason.encrypted_content.split('.');
  for (const field of [2, 3]) {
    const data = JSON.parse(Buffer.from(parts[1], 'base64url'));
    data[field] = field === 2 ? '0'.repeat(64) : 'changed-ciphertext';
    const tampered = [parts[0], Buffer.from(JSON.stringify(data)).toString('base64url'), parts[2]].join('.');
    await rejected(service.client, [{ ...reason, encrypted_content: tampered }], 'reasoning_replay_invalid');
  }
  await rejected(service.client, [{ ...reason, encrypted_content: 'sgr:9.invalid.invalid' }], 'reasoning_replay_invalid');
  await rejected(service.client, [{ ...reason, encrypted_content: 'foreign-raw' }], 'reasoning_binding_required');
  await rejected(service.client, [reason], 'reasoning_binding_invalid', { spartan_chain_id: 'invalid' });
  await rejected(service.client, [reason], 'reasoning_realm_mismatch', { model: 'openai/gpt-test' });
  assert.equal((await service.control()).calls.length, 1);
  await service.control({ rotate_secret: true });
  await rejected(service.client, [reason], 'reasoning_replay_invalid');
  assert.equal((await service.control()).calls.length, 1);
});

test('same-account replay survives token/slot changes but never fails over across accounts', async t => {
  const service = await fixture(t);
  const first = await complete(service.client, 'start');
  const reasonA = first.output.find(item => item.type === 'reasoning');
  await service.control({ disabled: [0, 2] });
  // The other account is operational, but must never receive A's ciphertext.
  await assert.rejects(service.client.responses.create({ model, input: [reasonA], store: false }),
    error => error.status === 503 && error.code === 'reasoning_replay_account_unavailable');
  assert.equal((await service.control()).calls.length, 1);
  const other = await complete(service.client, 'separate conversation');
  const reasonB = other.output.find(item => item.type === 'reasoning');
  await rejected(service.client, [reasonA, reasonB], 'reasoning_replay_scope_mismatch');
  await service.control({ disabled: [0], restart: true });
  await complete(service.client, [reasonA]);
  const observed = await service.control();
  assert.deepEqual(observed.calls.map(call => call.account), ['A', 'B', 'A']);
});

test('valid signed legacy raw history retains its wire contract against new Route', async t => {
  const service = await fixture(t);
  const response = await fetch(new URL('/api/pi/reasoning-binding', service.baseURL), {
    method: 'POST', headers: { Authorization: 'Bearer secret', 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope: 'a'.repeat(64), sessionId: 'a7becb62-8311-466d-89ca-e8b215d0a700', realm: 'codex_subscription' }),
  });
  assert.equal(response.status, 200);
  const { binding } = await response.json();
  const first = await service.client.responses.create({ model, input: 'start', store: false, ...binding });
  const reason = first.output.find(item => item.type === 'reasoning');
  assert.equal(reason.encrypted_content, 'provider-opaque-A');
  const second = await service.client.responses.create({ model, input: [reason], store: false, ...binding });
  assert.equal(second.status, 'completed');
  assert.equal(second.output.find(item => item.type === 'reasoning').encrypted_content, 'provider-opaque-A');
  const standard = await complete(service.client, 'new unsigned history');
  const receipt = standard.output.find(item => item.type === 'reasoning');
  const mixed = await service.client.responses.create({ model, input: [receipt], store: false, ...binding });
  assert.equal(mixed.status, 'completed');
  const count = (await service.control()).calls.length;
  await rejected(service.client, [{ ...receipt, encrypted_content: receipt.encrypted_content + '0' }],
    'reasoning_replay_invalid', binding);
  assert.equal((await service.control()).calls.length, count);
});

test('nonstream reconstruction wraps completed opaque items even when terminal output is absent', async t => {
  const service = await fixture(t);
  await service.control({ omit_output: true });
  const first = await complete(service.client, 'start');
  const reason = first.output.find(item => item.type === 'reasoning');
  assert.match(reason.encrypted_content, /^sgr:1\./);
  await complete(service.client, [reason]);
  assert.equal((await service.control()).calls[1].body.input[0].encrypted_content, 'provider-opaque-A');
});

async function nativeProcess(document) {
  const path = fileURLToPath(new URL('./native_replay_fixture.mjs', import.meta.url));
  const child = spawn(process.execPath, [path], { env: { ...process.env, PI_CODING_AGENT_DIR: piDir } });
  let output = '', error = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { error += chunk; });
  child.stdin.end(JSON.stringify(document));
  const timer = setTimeout(() => child.kill('SIGKILL'), 15000);
  const [code] = await once(child, 'exit'); clearTimeout(timer);
  assert.equal(code, 0, error);
  return JSON.parse(output);
}

test('real native Pi named session continues through fresh client and Route processes', async t => {
  const service = await fixture(t);
  const directory = await mkdtemp(join(tmpdir(), 'pi-native-http-'));
  try {
    await service.control({ late: true });
    const first = await nativeProcess({ baseURL: service.baseURL, directory });
    assert.equal(first.stopReason, 'stop');
    assert.equal(first.sessionId, 'subagent.0123456789abcdef');
    assert.ok(first.opaque > 0);
    await service.control({ restart: true });
    const resumed = await nativeProcess({ baseURL: service.baseURL, directory, sessionFile: first.sessionFile });
    assert.equal(resumed.stopReason, 'stop');
    assert.equal(resumed.sessionId, first.sessionId);
    assert.ok(resumed.opaque > first.opaque);
    const observed = await service.control();
    assert.equal(observed.bindings, 0);
    assert.equal(observed.calls.length, 3);
    assert.ok(observed.calls.slice(1).every(call => call.body.input.some(item => item.encrypted_content === 'provider-opaque-A')));
  } finally { await rm(directory, { recursive: true, force: true }); }
});
